"""
TSETMC order-flow adapter.

Endpoints:
- ClientType/GetClientTypeHistory/{insCode} — daily retail/institutional
- BestLimits/{insCode} — current order book (only valid during market hours)

Fragile as all TSETMC endpoints. Returns clear errors when data unavailable.
"""

from datetime import datetime
from typing import Any

import httpx
from loguru import logger

from data_sources.base_adapter import BaseAdapter, AdapterResult


class TsetmcOrderFlowAdapter(BaseAdapter):
    name = "tsetmc_orderflow"
    BASE_URL = "https://cdn.tsetmc.com/api"
    TIMEOUT = 20.0
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
    def _to_int(v: Any) -> int:
        try:
            return 0 if v is None else int(float(v))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _unwrap_rows(payload: Any, key: str) -> list[dict]:
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
            for vv in payload.values():
                if isinstance(vv, list):
                    return [r for r in vv if isinstance(r, dict)]
        return []

    # ---------------- BaseAdapter interface ----------------

    def health_check(self) -> AdapterResult:
        # Use a known large symbol (فولاد)
        try:
            with self._client() as c:
                r = c.get(f"{self.BASE_URL}/ClientType/GetClientTypeHistory/46348559193224090")
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            return AdapterResult(ok=False, message=f"خطا: {e}")
        rows = self._unwrap_rows(data, "clientType")
        if not rows:
            return AdapterResult(ok=False, message="پاسخ خالی بود")
        return AdapterResult(
            ok=True, message=f"{len(rows)} ردیف — endpoint سالم است",
        )

    def fetch_instruments(self) -> AdapterResult:
        return AdapterResult(ok=False, message="این adapter فقط order-flow دارد")

    def fetch_prices(self, symbol: str, days: int = 30) -> AdapterResult:
        return AdapterResult(ok=False, message="این adapter فقط order-flow دارد")

    # ---------------- Order flow ----------------

    def fetch_client_type_history(
        self, ins_code: str, days: int = 30,
    ) -> AdapterResult:
        """Fetch retail/institutional daily history."""
        ins_code = str(ins_code).strip()
        if not ins_code or ins_code.startswith("IRO"):
            return AdapterResult(ok=False, message=f"insCode نامعتبر: {ins_code}")

        url = f"{self.BASE_URL}/ClientType/GetClientTypeHistory/{ins_code}"
        try:
            with self._client() as c:
                r = c.get(url)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            code = getattr(r, "status_code", None)
            return AdapterResult(ok=False, message=f"HTTP {code}")
        except Exception as e:
            return AdapterResult(ok=False, message=f"خطا: {e}")

        rows = self._unwrap_rows(data, "clientType")
        if not rows:
            return AdapterResult(ok=False, message="پاسخ خالی بود")

        parsed = []
        for row in rows:
            d_even = row.get("recDate")
            if not d_even:
                continue
            try:
                ts = datetime(
                    int(str(d_even)[:4]),
                    int(str(d_even)[4:6]),
                    int(str(d_even)[6:8]),
                )
            except (ValueError, IndexError):
                continue
            if ts.year < 1900:
                continue

            parsed.append({
                "ts": ts,
                "buy_retail_vol": self._to_float(row.get("buy_N_Volume")),
                "buy_inst_vol": self._to_float(row.get("buy_I_Volume")),
                "sell_retail_vol": self._to_float(row.get("sell_N_Volume")),
                "sell_inst_vol": self._to_float(row.get("sell_I_Volume")),
                "buy_retail_count": self._to_int(row.get("buy_N_Count")),
                "buy_inst_count": self._to_int(row.get("buy_I_Count")),
                "sell_retail_count": self._to_int(row.get("sell_N_Count")),
                "sell_inst_count": self._to_int(row.get("sell_I_Count")),
            })

        parsed.sort(key=lambda x: x["ts"])
        if days and days > 0:
            parsed = parsed[-days:]

        return AdapterResult(
            ok=True,
            message=f"{len(parsed)} ردیف order-flow",
            rows=len(parsed),
            payload=parsed,
        )

    def fetch_best_limits(self, ins_code: str) -> AdapterResult:
        """Current order book (only valid during market hours)."""
        ins_code = str(ins_code).strip()
        if not ins_code or ins_code.startswith("IRO"):
            return AdapterResult(ok=False, message=f"insCode نامعتبر: {ins_code}")

        url = f"{self.BASE_URL}/BestLimits/{ins_code}"
        try:
            with self._client() as c:
                r = c.get(url)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            code = getattr(r, "status_code", None)
            return AdapterResult(ok=False, message=f"HTTP {code}")
        except Exception as e:
            return AdapterResult(ok=False, message=f"خطا: {e}")

        rows = self._unwrap_rows(data, "bestLimits")
        if not rows:
            return AdapterResult(ok=False, message="پاسخ خالی بود (بازار بسته؟)")

        # Take the row with highest number of active limits
        first = rows[0]
        buy = (first.get("bestLimits") or [{}])
        buy_limit = buy[0] if isinstance(buy, list) and buy else {}

        # Best bid: row with highest buy price
        buy_price = self._to_float(first.get("bestBuyLimitPrice"))
        buy_vol = self._to_float(first.get("bestBuyLimitVolume"))
        sell_price = self._to_float(first.get("bestSellLimitPrice"))
        sell_vol = self._to_float(first.get("bestSellLimitVolume"))

        return AdapterResult(
            ok=True,
            message="عمق بازار دریافت شد",
            rows=1,
            payload={
                "buy_limit_price": buy_price,
                "buy_limit_vol": buy_vol,
                "sell_limit_price": sell_price,
                "sell_limit_vol": sell_vol,
            },
        )