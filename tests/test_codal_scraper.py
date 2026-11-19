import httpx
import pytest

from fundamental.codal_scraper import (
    fetch_and_parse, _clean_number, _find_labeled_value,
)


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("fake", request=None, response=None)


class FakeClient:
    def __init__(self, text, status=200):
        self._r = FakeResponse(text, status)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **kw):
        return self._r


@pytest.fixture
def mock_httpx(monkeypatch):
    def _install(text, status=200):
        monkeypatch.setattr(
            httpx, "Client", lambda **_kw: FakeClient(text, status),
        )
    return _install


def test_clean_number_persian():
    assert _clean_number("۱۲۳٬۴۵۶") == 123456.0
    assert _clean_number("۱۲۳.۴۵") == 123.45


def test_clean_number_english():
    assert _clean_number("1,234.56") == 1234.56
    assert _clean_number("-15.3") == -15.3
    assert _clean_number("abc") is None


def test_find_labeled_value():
    assert _find_labeled_value("سود هر سهم (ریال): 1,200", ["سود هر سهم"]) == 1200.0


def test_fetch_empty_url():
    r = fetch_and_parse("")
    assert not r.success
    assert "URL" in (r.error or "")


def test_fetch_invalid_protocol():
    r = fetch_and_parse("ftp://x")
    assert not r.success
    assert "http" in (r.error or "").lower()


def test_fetch_http_error(mock_httpx):
    mock_httpx("<html></html>", status=500)
    r = fetch_and_parse("https://www.codal.ir/foo")
    assert not r.success
    assert "HTTP 500" in (r.error or "")


def test_fetch_no_labels(mock_httpx):
    mock_httpx("<html><body>hello world</body></html>")
    r = fetch_and_parse("https://www.codal.ir/foo")
    assert not r.success
    assert any("هیچ مقدار" in w for w in r.warnings)


def test_fetch_parses_pe_roe_eps(mock_httpx):
    html = """
    <html><body>
    <p>P/E: 6.5</p>
    <p>ROE: 22.5</p>
    <p>EPS: 1,200</p>
    </body></html>
    """
    mock_httpx(html)
    r = fetch_and_parse("https://www.codal.ir/foo")
    assert r.success
    assert r.parsed.get("pe") == 6.5
    assert r.parsed.get("roe") == 22.5
    assert r.parsed.get("eps") == 1200.0
    assert r.confidence in ("medium", "high")


def test_fetch_persian_labels(mock_httpx):
    html = """
    <html><body>
    <p>سود هر سهم (ریال): ۱,۲۰۰</p>
    <p>بازده حقوق صاحبان سهام: ۲۲.۵</p>
    </body></html>
    """
    mock_httpx(html)
    r = fetch_and_parse("https://www.codal.ir/foo")
    assert r.success
    assert r.parsed.get("eps") == 1200.0
    assert r.parsed.get("roe") == 22.5