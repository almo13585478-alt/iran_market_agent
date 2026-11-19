"""
One-shot diagnostic for TSETMC connectivity.

Run:
    python -m scripts.diagnose_tsetmc

Prints, for each endpoint, the HTTP status, response size, first 500 chars,
and the result of _unwrap_rows / _parse_instrument_rows. Does NOT touch the
database.
"""

import json

import httpx
from loguru import logger

from app.logging_setup import setup_logging
from data_sources.tsetmc import TsetmcAdapter


def _probe(label: str, url: str, params, headers) -> None:
    print(f"\n=== {label} ===")
    print(f"URL: {url}")
    try:
        with httpx.Client(timeout=20.0, headers=headers, follow_redirects=True) as c:
            r = c.get(url, params=params)
            print(f"HTTP status: {r.status_code}")
            print(f"Response size: {len(r.content)} bytes")
            text = r.text
            print(f"First 500 chars: {text[:500]!r}")
            if r.status_code >= 400:
                print("=> FAILED at HTTP level")
                return
            try:
                data = r.json()
            except Exception as e:
                print(f"=> JSON parse failed: {e}")
                return

            a = TsetmcAdapter()
            rows = a._unwrap_rows(data, "marketwatch") if "MarketWatch" in url \
                else a._unwrap_rows(data, "marketMap")
            print(f"=> _unwrap_rows returned {len(rows)} rows")
            if rows:
                parsed = a._parse_instrument_rows(rows)
                print(f"=> _parse_instrument_rows returned {len(parsed)} rows")
                if parsed:
                    print(f"   First parsed: {json.dumps(parsed[0], ensure_ascii=False, default=str)}")
                else:
                    print(f"   First raw row keys: {list(rows[0].keys())[:25]}")
    except Exception as e:
        print(f"=> EXCEPTION: {type(e).__name__}: {e}")


def main() -> None:
    setup_logging()
    a = TsetmcAdapter()
    base = a.BASE_URL

    print("=== 0) basic connectivity check ===")
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.head("https://cdn.tsetmc.com/")
            print(f"HEAD https://cdn.tsetmc.com/ -> {r.status_code}")
    except Exception as e:
        print(f"HEAD failed: {type(e).__name__}: {e}")

    _probe(
        "1) GetMarketWatch",
        f"{base}/ClosingPrice/GetMarketWatch",
        TsetmcAdapter._market_watch_params(),
        a._headers,
    )

    _probe(
        "2) GetMarketMap",
        f"{base}/ClosingPrice/GetMarketMap",
        {"market": 0, "size": 5, "sector": 0, "typeSelected": 1},
        a._headers,
    )

    _probe(
        "3) GetInstrumentSearch (فولاد)",
        f"{base}/Instrument/GetInstrumentSearch/فولاد",
        None,
        a._headers,
    )


if __name__ == "__main__":
    main()