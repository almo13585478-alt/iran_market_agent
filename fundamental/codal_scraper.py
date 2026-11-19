"""
Best-effort Codal HTML parser.

HONEST DISCLAIMER:
- Codal has no official API. HTML structure changes without notice.
- This scraper will break. It is labeled "fragile" by design.
- It NEVER auto-saves. User must review and confirm each value.
- If parsing fails, returns success=False with a clear message.
- Confidence is "low" unless 5+ fields are cleanly matched.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from loguru import logger


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


LABELS = {
    "eps": [
        "سود هر سهم", "سود هر سهم (ریال)", "EPS",
        "Earnings per share", "سود هر سهم پس از کسر مالیات",
    ],
    "pe": [
        "نسبت قیمت به درآمد", "P/E", "P/E Ratio",
        "قیمت به درآمد", "نسبت P/E",
    ],
    "roe": [
        "بازده حقوق صاحبان سهام", "ROE", "Return on equity",
        "بازدهی حقوق صاحبان",
    ],
    "roa": [
        "بازده دارایی‌ها", "ROA", "Return on assets",
    ],
    "revenue": [
        "درآمد عملیاتی", "فروش خالص", "Revenue", "Net sales",
        "درآمدهای عملیاتی",
    ],
    "net_profit": [
        "سود خالص", "Net profit", "Net income", "سود (زیان) خالص",
    ],
    "debt_ratio": [
        "نسبت بدهی", "Debt ratio",
    ],
    "profit_growth": [
        "رشد سود", "Profit growth", "رشد سود خالص",
    ],
    "revenue_growth": [
        "رشد فروش", "Revenue growth", "رشد درآمد",
    ],
}


# All the different separators that may appear in Persian/Arabic financial text
_THOUSANDS_SEPS = (
    ",",       # ASCII comma
    "،",       # Arabic comma (U+060C)
    "٬",       # Arabic thousands separator (U+066C)  ← this was the missed one
    "\u200e",  # LRM
    "\u200f",  # RLM
    " ",       # regular space (some sources use "1 234 567")
    "\u00a0",  # non-breaking space
)

_DECIMAL_SEPS = (
    "٫",       # Arabic decimal separator (U+066B)
)


@dataclass
class ParseResult:
    success: bool = False
    url: str = ""
    fetched_at: datetime = field(default_factory=datetime.now)
    parsed: dict = field(default_factory=dict)
    confidence: str = "low"
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    raw_length: int = 0


def _clean_number(text: str) -> float | None:
    """Extract a float from '۱۲۳٬۴۵۶.۷۸' or '123,456.78' or '1 234 567'."""
    if not text:
        return None

    # Persian/Arabic digits → ASCII
    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"
    trans = {ord(p): str(i) for i, p in enumerate(persian)}
    trans.update({ord(a): str(i) for i, a in enumerate(arabic)})
    text = text.translate(trans)

    # Remove all thousands separators
    for sep in _THOUSANDS_SEPS:
        text = text.replace(sep, "")

    # Convert decimal separators
    for dec in _DECIMAL_SEPS:
        text = text.replace(dec, ".")

    text = text.strip()
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _find_labeled_value(
    text: str, labels: list[str], context_chars: int = 200,
) -> float | None:
    """Search for label, return nearest number after it."""
    for label in labels:
        idx = text.find(label)
        if idx < 0:
            continue
        after = text[idx + len(label): idx + len(label) + context_chars]
        val = _clean_number(after)
        if val is not None:
            return val
    return None


def _normalize_url(url: str) -> tuple[str, str | None]:
    """Return (normalized_url, error_message_or_None)."""
    url = (url or "").strip()
    if not url:
        return url, "URL خالی است"

    # Already has protocol
    if url.startswith(("http://", "https://")):
        return url, None

    # Protocol-relative URL
    if url.startswith("//"):
        return "https:" + url, None

    # Looks like a Codal URL without protocol → auto-prepend
    if "codal" in url.lower() or url.startswith("www."):
        return "https://" + url, None

    return url, (
        f"URL نامعتبر: «{url[:60]}». "
        "URL باید با http:// یا https:// شروع شود، یا شامل «codal» باشد."
    )


def fetch_and_parse(url: str, timeout: float = 25.0) -> ParseResult:
    """Fetch a Codal report page and extract labeled numbers."""
    result = ParseResult(url=url)

    normalized, url_err = _normalize_url(url)
    if url_err:
        result.error = url_err
        return result
    result.url = normalized

    if "codal.ir" not in normalized and "codal" not in normalized.lower():
        result.warnings.append(
            "⚠️ URL شامل codal.ir نیست — مطمئن شوید از منبع معتبر است."
        )

    response = None
    html = ""
    try:
        with httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
                "Referer": "https://www.codal.ir/",
            },
            follow_redirects=True,
        ) as c:
            response = c.get(normalized)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPStatusError as e:
        code = None
        if response is not None:
            code = getattr(response, "status_code", None)
        if code is None and getattr(e, "response", None) is not None:
            code = getattr(e.response, "status_code", None)
        result.error = f"HTTP {code}" if code else "HTTP error"
        return result
    except httpx.TimeoutException:
        result.error = "timeout (Codal پاسخ نداد)"
        return result
    except Exception as e:
        result.error = f"خطای شبکه: {e}"
        return result

    result.raw_length = len(html)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            result.error = f"خطای parse HTML: {e}"
            return result

    text = soup.get_text(separator=" ", strip=True)

    for field_name, labels in LABELS.items():
        val = _find_labeled_value(text, labels)
        if val is not None:
            result.parsed[field_name] = val

    n = len(result.parsed)
    if n >= 5:
        result.confidence = "high"
    elif n >= 2:
        result.confidence = "medium"
    else:
        result.confidence = "low"

    if n == 0:
        result.success = False
        result.warnings.append(
            "هیچ مقدار قابل تشخیصی در صفحه یافت نشد. "
            "احتمالاً ساختار صفحه تغییر کرده یا این صفحه گزارش مالی نیست. "
            "از ورود دستی یا CSV استفاده کنید."
        )
    else:
        result.success = True

    result.warnings.append(
        "⚠️ این نتیجه از پارس HTML است و ممکن است اشتباه باشد. "
        "حتماً قبل از ذخیره، مقادیر را با گزارش اصلی مقایسه کنید."
    )

    logger.info(
        "Codal parse {}: {} fields, confidence={}",
        normalized, n, result.confidence,
    )
    return result