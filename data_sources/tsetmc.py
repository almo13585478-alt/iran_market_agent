"""
TSETMC unofficial CDN adapter.

Primary daily-history endpoint: /api/ClosingPrice/GetClosingPriceDailyList/:InsCode/:Top
Fallback: /api/ClosingPrice/GetClosingPriceDaily/:InsCode/:DEven

Symbol→insCode resolution ranks candidates by market type so options are
never chosen over the underlying stock.
"""

import time
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

from app.config import BASE_DIR
from data_sources.base_adapter import BaseAdapter, AdapterResult


_INS_CODE_KEYS = [
    "insCode", "ins_code", "InsCode", "INSCODE",
    "instrumentCode", "instrumentId", "insID", "id",
]
_SYMBOL_KEYS = [
    "lVal18AFC", "lval18AFC", "lVal18", "symbol", "symbolName", "Symbol",
    "ticker", "lva",
]
_NAME_KEYS = [
    "lVal30", "name", "companyName", "CompanyName", "lVal30AFC", "longName", "lvc",
]

_FLOW_TO_MARKET = {
    1: "TSE", 2: "IFB", 3: "DERIV", 4: "ETF",
    5: "ENERGY", 6: "OPTION", 7: "ETF_BOND", 8: "IFB_ETF",
}
_SYNCABLE_FLOWS = {1, 2, 4}
_OPTION_PREFIXES = ("ض", "ح")
_DERIVATIVE_ISIN_PREFIXES = ("IRO9", "IRO6")


def _first_present(row: dict, keys: list[str]) -> Any:
    for k in keys:
        if k in row:
            v = row[k]
            if v is not None and v != "":
                return v
    return None


def _is_syncable(symbol: str, isin: str | None, flow: int | None) -> bool:
    if flow in _SYNCABLE_FLOWS:
        return not symbol.startswith(_OPTION_PREFIXES)
    if flow is None:
        if symbol.startswith(_OPTION_PREFIXES):
            return False
        if isin and isin.startswith(_DERIVATIVE_ISIN_PREFIXES):
            return False
        return True
    return False


class TsetmcAdapter(BaseAdapter):
    name = "tsetmc"
    BASE_URL = "https://cdn.tsetmc.com/api"
    TIMEOUT = 20.0
    MAX_RETRIES = 2
    BACKOFF_BASE = 2.0
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(self, timeout: float = TIMEOUT):
        self.timeout = timeout
        self._headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.tsetmc.com/",
            "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
        }
        self._debug_dir = BASE_DIR / "logs"
        self._debug_dir.mkdir(parents=True, exist_ok=True)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout, headers=self._headers, follow_redirects=True,
        )

    @staticmethod
    def _to_float(v: Any) -> float:
        try:
            return 0.0 if v is None else float(v)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _unwrap_rows(payload: Any, expected_key: str) -> list[dict]:
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            v = payload.get(expected_key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
            for vv in payload.values():
                if isinstance(vv, list):
                    return [r for r in vv if isinstance(r, dict)]
        return []

    @staticmethod
    def _market_watch_params() -> list[tuple[str, Any]]:
        p: list[tuple[str, Any]] = [
            ("market", 0), ("withBestLimits", "false"), ("hEven", 0), ("RefID", 0),
        ]
        for i in range(9):
            p.append((f"paperTypes[{i}]", i + 1))
        return p

    def _http_get(self, endpoint: str, params: Any) -> tuple[int, bytes, Any]:
        url = f"{self.BASE_URL}/{endpoint}"
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                with self._client() as c:
                    r = c.get(url, params=params)
                    if r.status_code == 429 or 500 <= r.status_code < 600:
                        if attempt < self.MAX_RETRIES:
                            time.sleep(self.BACKOFF_BASE * (2 ** attempt))
                            continue
                        logger.warning("TSETMC {} -> HTTP {}", endpoint, r.status_code)
                        return r.status_code, r.content, None
                    r.raise_for_status()
                    return r.status_code, r.content, r.json()
            except httpx.HTTPError as e:
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.BACKOFF_BASE * (2 ** attempt))
                    continue
                logger.warning("TSETMC {} failed: {}", endpoint, e)
                return 0, b"", None
        return 0, b"", None

    # ---------- BaseAdapter ----------

    def health_check(self) -> AdapterResult:
        status, body, data = self._http_get(
            "ClosingPrice/GetMarketWatch", self._market_watch_params()
        )
        if data is None:
            return AdapterResult(
                ok=False, message=f"GetMarketWatch ناموفق: HTTP {status}, {len(body)}B"
            )
        rows = self._unwrap_rows(data, "marketwatch")
        if not rows:
            return AdapterResult(ok=False, message="GetMarketWatch خالی بود")
        return AdapterResult(
            ok=True, message=f"اتصال موفق — {len(rows)} ردیف از GetMarketWatch"
        )

    def fetch_instruments(self, stocks_only: bool = True) -> AdapterResult:
        status, body, data = self._http_get(
            "ClosingPrice/GetMarketWatch", self._market_watch_params()
        )
        if data is None:
            return AdapterResult(
                ok=False, message=f"GetMarketWatch ناموفق: HTTP {status}"
            )
        rows = self._unwrap_rows(data, "marketwatch")
        if not rows:
            return AdapterResult(ok=False, message="GetMarketWatch خالی بود")

        parsed: list[dict] = []
        skipped_derivative = 0
        seen_sym: set[str] = set()
        for row in rows:
            sym_raw = _first_present(row, _SYMBOL_KEYS)
            isin_raw = _first_present(row, _INS_CODE_KEYS)
            if sym_raw is None or isin_raw is None:
                continue
            symbol = str(sym_raw).strip()
            isin = str(isin_raw).strip()
            if not symbol or not isin or symbol in seen_sym:
                continue

            flow = row.get("flow")
            flow_int = int(flow) if isinstance(flow, (int, float)) else None
            is_syncable = _is_syncable(symbol, isin, flow_int)

            if stocks_only and not is_syncable:
                skipped_derivative += 1
                continue

            seen_sym.add(symbol)
            name_raw = _first_present(row, _NAME_KEYS)
            parsed.append({
                "symbol": symbol,
                "name": str(name_raw).strip() if name_raw else symbol,
                "isin": isin,
                "flow": flow_int,
                "market": _FLOW_TO_MARKET.get(flow_int, "OTHER"),
                "industry": None,
                "is_active": is_syncable,
            })

        msg = f"{len(parsed)} نماد سهام/ETF"
        if skipped_derivative:
            msg += f" ({skipped_derivative} مشتقه/آپشن نادیده گرفته شد)"
        return AdapterResult(ok=True, message=msg, rows=len(parsed), payload=parsed)

    def resolve_ins_code_by_symbol(self, symbol: str) -> str | None:
        """
        Resolve the numeric insCode for a Persian symbol via search, ranking
        candidates so the underlying stock always wins over its options.

        Ranking (first hit wins):
          1. exact symbol match AND flow in {1,2,4}
          2. exact symbol match AND symbol not starting with ض/ح
          3. exact symbol match (any flow)
          4. flow in {1,2,4}, symbol not starting with ض/ح
          5. first valid candidate
        """
        if not symbol:
            return None

        status, body, data = self._http_get(
            f"Instrument/GetInstrumentSearch/{symbol}", None
        )
        if data is None:
            return None
        candidates = self._unwrap_rows(data, "instrumentSearch")

        valid: list[dict] = []
        for c in candidates:
            code = str(c.get("insCode") or "").strip()
            if not code or code.startswith("IRO"):
                continue
            sym = (c.get("lVal18AFC") or "").strip()
            flow = c.get("flow")
            flow_int = int(flow) if isinstance(flow, (int, float)) else None
            valid.append({"code": code, "symbol": sym, "flow": flow_int})

        if not valid:
            return None

        # Priority 1
        for v in valid:
            if v["symbol"] == symbol and v["flow"] in _SYNCABLE_FLOWS:
                return v["code"]
        # Priority 2
        for v in valid:
            if v["symbol"] == symbol and not v["symbol"].startswith(_OPTION_PREFIXES):
                return v["code"]
        # Priority 3
        for v in valid:
            if v["symbol"] == symbol:
                return v["code"]
        # Priority 4
        for v in valid:
            if v["flow"] in _SYNCABLE_FLOWS and not v["symbol"].startswith(_OPTION_PREFIXES):
                return v["code"]
        # Fallback
        return valid[0]["code"]

    def fetch_prices(self, symbol: str, days: int = 30) -> AdapterResult:
        code = self.resolve_ins_code_by_symbol(symbol)
        if not code:
            return AdapterResult(ok=False, message=f"insCode برای {symbol} پیدا نشد")
        return self.fetch_prices_by_ins_code(code, days)

    def fetch_prices_by_ins_code(
        self, ins_code: str, days: int = 60
    ) -> AdapterResult:
        ins_code = str(ins_code).strip()
        if not ins_code or ins_code.startswith("IRO"):
            return AdapterResult(ok=False, message=f"insCode نامعتبر: {ins_code}")

        top = max(1, min(int(days), 2000))
        status, body, data = self._http_get(
            f"ClosingPrice/GetClosingPriceDailyList/{ins_code}/{top}", None
        )
        if data is None:
            return AdapterResult(
                ok=False, message=f"insCode={ins_code}: HTTP {status}"
            )

        rows = self._unwrap_rows(data, "closingPriceDaily")
        if not rows and isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        if not rows:
            return AdapterResult(
                ok=False,
                message=f"insCode={ins_code}: پاسخ خالی یا ساختار نامنتظره",
            )

        parsed = self._parse_price_rows(rows)
        if not parsed:
            return AdapterResult(
                ok=False,
                message=(
                    f"insCode={ins_code}: {len(rows)} ردیف خام ولی ۰ ردیف "
                    "معتبر (قیمت‌ها صفر/ناقص‌اند — احتمالاً آپشن یا توقف)"
                ),
            )
        return AdapterResult(
            ok=True,
            message=f"{len(parsed)} رکورد برای insCode={ins_code}",
            rows=len(parsed),
            payload=parsed,
        )

    def _parse_price_rows(self, rows: list[dict]) -> list[dict]:
        parsed: list[dict] = []
        for row in rows:
            d = row.get("dEven")
            if not d:
                continue
            try:
                ts = datetime(int(str(d)[:4]), int(str(d)[4:6]), int(str(d)[6:8]))
            except (ValueError, IndexError):
                continue
            if ts.year < 1900:
                continue

            o = self._to_float(
                row.get("priceFirst") or row.get("priceYesterday") or row.get("pf")
            )
            h = self._to_float(row.get("priceMax") or row.get("pmax"))
            l = self._to_float(row.get("priceMin") or row.get("pmin"))
            c_ = self._to_float(
                row.get("priceLast") or row.get("priceClose")
                or row.get("pl") or row.get("pClosing")
            )
            if o <= 0 or c_ <= 0:
                continue
            if h < max(o, c_) or l > min(o, c_):
                continue
            parsed.append({
                "ts": ts,
                "open": o, "high": h, "low": l, "close": c_,
                "volume": self._to_float(
                    row.get("qTotTran5J") or row.get("qtj")
                ),
                "value": self._to_float(
                    row.get("qTotCap5J") or row.get("qtc")
                ),
            })
        parsed.sort(key=lambda x: x["ts"])
        return parsed