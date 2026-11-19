"""
CSV fallback adapter. Reads local CSV files when TSETMC is unavailable.

Layout:
- data/imports/instruments.csv
    columns: symbol,name,market,industry,ins_code,isin (isins optional)
- data/imports/prices/{symbol}.csv
    columns: date,open,high,low,close,volume,value
    date format: YYYY-MM-DD
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

from app.config import BASE_DIR
from data_sources.base_adapter import BaseAdapter, AdapterResult


_OPTION_PREFIXES = ("ض", "ح")
_DERIVATIVE_ISIN_PREFIXES = ("IRO9", "IRO6")


class CsvImporterAdapter(BaseAdapter):
    name = "csv"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or (BASE_DIR / "data" / "imports"))
        self.instruments_path = self.base_dir / "instruments.csv"
        self.prices_dir = self.base_dir / "prices"

    def health_check(self) -> AdapterResult:
        if not self.instruments_path.exists():
            return AdapterResult(
                ok=False,
                message=f"فایل {self.instruments_path.name} پیدا نشد",
            )
        try:
            df = pd.read_csv(self.instruments_path)
        except Exception as e:
            return AdapterResult(ok=False, message=f"خطای خواندن CSV: {e}")
        if "symbol" not in df.columns:
            return AdapterResult(
                ok=False, message="ستون symbol در instruments.csv نیست"
            )
        return AdapterResult(ok=True, message=f"{len(df)} ردیف در CSV آماده است")

    def fetch_instruments(self, stocks_only: bool = True) -> AdapterResult:
        if not self.instruments_path.exists():
            return AdapterResult(
                ok=False, message=f"فایل {self.instruments_path.name} پیدا نشد"
            )
        try:
            df = pd.read_csv(self.instruments_path, dtype=str).fillna("")
        except Exception as e:
            logger.exception("CSV instruments read failed")
            return AdapterResult(ok=False, message=f"خطای خواندن: {e}")

        parsed: list[dict] = []
        skipped = 0
        for _, row in df.iterrows():
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                continue

            isin = (row.get("isin") or "").strip() or None
            ins_code_raw = (row.get("ins_code") or "").strip() or None
            ins_code = None
            # If CSV puts ISIN in ins_code column, reclassify it
            if ins_code_raw and ins_code_raw.startswith("IRO"):
                isin = isin or ins_code_raw
            else:
                ins_code = ins_code_raw

            is_syncable = True
            if symbol.startswith(_OPTION_PREFIXES):
                is_syncable = False
            elif isin and isin.startswith(_DERIVATIVE_ISIN_PREFIXES):
                is_syncable = False

            if stocks_only and not is_syncable:
                skipped += 1
                continue

            parsed.append(
                {
                    "symbol": symbol,
                    "name": (row.get("name") or symbol).strip(),
                    "market": (row.get("market") or "TSE").strip() or "TSE",
                    "industry": (row.get("industry") or "").strip() or None,
                    "ins_code": ins_code,
                    "isin": isin,
                    "flow": None,
                    "is_active": is_syncable,
                }
            )

        msg = f"{len(parsed)} نماد از CSV"
        if skipped:
            msg += f" ({skipped} مشتقه/آپشن نادیده گرفته شد)"
        return AdapterResult(ok=True, message=msg, rows=len(parsed), payload=parsed)

    def fetch_prices(self, symbol: str, days: int = 30) -> AdapterResult:
        path = self.prices_dir / f"{symbol}.csv"
        if not path.exists():
            return AdapterResult(ok=False, message=f"فایل {path.name} پیدا نشد")
        try:
            df = pd.read_csv(path, dtype=str).fillna("")
        except Exception as e:
            return AdapterResult(ok=False, message=f"خطای خواندن: {e}")

        parsed: list[dict] = []
        for _, row in df.iterrows():
            date_str = (row.get("date") or "").strip()
            if not date_str:
                continue
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            try:
                parsed.append(
                    {
                        "ts": ts,
                        "open": float(row.get("open") or 0),
                        "high": float(row.get("high") or 0),
                        "low": float(row.get("low") or 0),
                        "close": float(row.get("close") or 0),
                        "volume": float(row.get("volume") or 0),
                        "value": float(row.get("value") or 0),
                    }
                )
            except ValueError:
                continue

        parsed.sort(key=lambda x: x["ts"])
        if days and days > 0:
            parsed = parsed[-days:]

        return AdapterResult(
            ok=True,
            message=f"{len(parsed)} رکورد از CSV برای {symbol}",
            rows=len(parsed),
            payload=parsed,
        )