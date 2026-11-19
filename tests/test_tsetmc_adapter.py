import json

import httpx
import pytest

from data_sources.tsetmc import TsetmcAdapter


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.content = json.dumps(data, ensure_ascii=False).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("fake", request=None, response=None)

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, mapping):
        self._mapping = mapping

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, **_kw):
        for key, resp in self._mapping.items():
            if key in url:
                return resp
        raise ValueError(f"No mock for URL: {url}")


@pytest.fixture
def mock_httpx(monkeypatch):
    def _install(mapping):
        monkeypatch.setattr(httpx, "Client", lambda **_kw: FakeClient(mapping))
    return _install


# ---------------------------------------------------------------- health_check

def test_health_check_ok(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse(
        {"marketwatch": [{"lva": "خودرو", "insID": "IRO1KHOD0001"}]}
    )})
    assert TsetmcAdapter().health_check().ok


def test_health_check_bare_list(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse(
        [{"lva": "خودرو", "insID": "IRO1KHOD0001"}]
    )})
    assert TsetmcAdapter().health_check().ok


def test_health_check_empty(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": []})})
    assert not TsetmcAdapter().health_check().ok


# --------------------------------------------------------- fetch_instruments

def test_fetch_instruments_marketwatch_shorthand(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": [
        {"lva": "فولاد", "lvc": "فولاد مباركه اصفهان",
         "insID": "IRO1FOLD0001", "flow": 1},
    ]})})
    r = TsetmcAdapter().fetch_instruments()
    assert r.ok and r.rows == 1
    row = r.payload[0]
    assert row["symbol"] == "فولاد"
    assert row["isin"] == "IRO1FOLD0001"
    assert row["market"] == "TSE"
    assert row["is_active"] is True


def test_fetch_instruments_full_field_names(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": [
        {"insCode": "123", "lVal18AFC": "خودرو", "lVal30": "ایران خودرو"},
    ]})})
    r = TsetmcAdapter().fetch_instruments()
    assert r.ok
    assert r.payload[0]["symbol"] == "خودرو"
    assert r.payload[0]["name"] == "ایران خودرو"


def test_fetch_instruments_dedupes(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": [
        {"lva": "فولاد", "insID": "IRO1FOLD0001"},
        {"lva": "فولاد", "insID": "IRO1FOLD0001"},
        {"lva": "خودرو", "insID": "IRO1KHOD0001"},
    ]})})
    assert TsetmcAdapter().fetch_instruments().rows == 2


def test_fetch_instruments_skips_incomplete(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": [
        {"lva": "", "insID": "X"},
        {"lva": "شپنا", "insID": ""},
        {"lva": "شپنا", "insID": "IRO1SHPN0001"},
    ]})})
    r = TsetmcAdapter().fetch_instruments()
    assert r.ok and r.rows == 1
    assert r.payload[0]["symbol"] == "شپنا"


def test_fetch_instruments_filters_options(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": [
        {"lva": "ضخود9001", "insID": "IRO9OPT001", "flow": 6},
        {"lva": "فولاد", "insID": "IRO1FOLD0001", "flow": 1},
    ]})})
    r = TsetmcAdapter().fetch_instruments(stocks_only=True)
    assert r.ok and r.rows == 1
    assert r.payload[0]["symbol"] == "فولاد"


def test_fetch_instruments_includes_options_when_asked(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": [
        {"lva": "ضخود9001", "insID": "IRO9OPT001", "flow": 6},
        {"lva": "فولاد", "insID": "IRO1FOLD0001", "flow": 1},
    ]})})
    r = TsetmcAdapter().fetch_instruments(stocks_only=False)
    assert r.ok and r.rows == 2
    opt = next(p for p in r.payload if p["symbol"] == "ضخود9001")
    assert opt["is_active"] is False


def test_fetch_instruments_empty(mock_httpx):
    mock_httpx({"GetMarketWatch": FakeResponse({"marketwatch": []})})
    assert not TsetmcAdapter().fetch_instruments().ok


# ------------------------------------------------- insCode resolution

def test_resolve_ins_code_exact_match(mock_httpx):
    mock_httpx({"GetInstrumentSearch": FakeResponse({"instrumentSearch": [
        {"insCode": "999", "lVal18AFC": "دیگر"},
        {"insCode": "46348559193224090", "lVal18AFC": "فولاد"},
    ]})})
    assert TsetmcAdapter().resolve_ins_code_by_symbol("فولاد") == "46348559193224090"


def test_resolve_ins_code_fallback_first(mock_httpx):
    mock_httpx({"GetInstrumentSearch": FakeResponse({"instrumentSearch": [
        {"insCode": "123", "lVal18AFC": "دیگر"},
    ]})})
    assert TsetmcAdapter().resolve_ins_code_by_symbol("ناموجود") == "123"


def test_resolve_ins_code_rejects_isin(mock_httpx):
    mock_httpx({"GetInstrumentSearch": FakeResponse({"instrumentSearch": [
        {"insCode": "IRO1FOLD0001", "lVal18AFC": "فولاد"},
    ]})})
    assert TsetmcAdapter().resolve_ins_code_by_symbol("فولاد") is None


# ------------------------------------------------- fetch_prices_by_ins_code

def test_fetch_prices_by_ins_code(mock_httpx):
    mock_httpx({"GetClosingPriceDailyList": FakeResponse({"closingPriceDaily": [{
        "dEven": 20250101, "priceFirst": 1000, "priceMax": 1100,
        "priceMin": 950, "priceLast": 1080,
    }]})})
    r = TsetmcAdapter().fetch_prices_by_ins_code("123", days=10)
    assert r.ok and r.rows == 1
    assert r.payload[0]["close"] == 1080.0


def test_fetch_prices_by_ins_code_rejects_isin(mock_httpx):
    r = TsetmcAdapter().fetch_prices_by_ins_code("IRO1FOLD0001", days=10)
    assert not r.ok
    assert "نامعتبر" in r.message


def test_skip_non_gregorian(mock_httpx):
    """Jalali-like dEven → 0 valid rows → strict adapter returns ok=False."""
    mock_httpx({"GetClosingPriceDailyList": FakeResponse({"closingPriceDaily": [{
        "dEven": 14030101, "priceFirst": 1000, "priceMax": 1100,
        "priceMin": 950, "priceLast": 1080,
    }]})})
    r = TsetmcAdapter().fetch_prices_by_ins_code("123", days=10)
    assert not r.ok
    assert r.rows == 0