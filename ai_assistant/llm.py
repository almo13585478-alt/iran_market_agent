"""
Optional LLM assistant. Uses any OpenAI-compatible API.
Set in .env:
    LLM_API_KEY=sk-...
    LLM_BASE_URL=https://api.openai.com/v1  (or any compatible)
    LLM_MODEL=gpt-4o-mini  (or any)

If not configured, returns a clear message. Never blocks deterministic mode.
"""

import os

import httpx
from loguru import logger
from sqlalchemy.orm import Session

from ai_assistant.deterministic import answer as deterministic_answer


SYSTEM_PROMPT = """تو یک دستیار تحلیل بورس ایران هستی. قوانین:
1. فقط بر اساس داده‌ای که در context به تو داده شده پاسخ بده.
2. اگر داده کافی نیست، صریحاً بگو «داده کافی نیست».
3. هیچ‌گاه پیش‌بینی قطعی نکن. بگو «احتمال» یا «بر اساس داده فعلی».
4. پاسخ کوتاه و ساختاریافته بده. فارسی روان.
5. هیچ سفارش واقعی پیشنهاد نکن — فقط تحلیل.
"""


def _build_context(session: Session, deterministic_data: dict, sources: list[str]) -> str:
    """Pack deterministic result + DB stats as context for the LLM."""
    import json
    parts = []
    if deterministic_data:
        try:
            parts.append("deterministic_data=" + json.dumps(
                deterministic_data, ensure_ascii=False, default=str,
            ))
        except Exception:
            pass
    if sources:
        parts.append("sources=" + ", ".join(sources))
    return "\n".join(parts) if parts else "(no context)"


def is_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def ask(session: Session, question: str) -> dict:
    """Call LLM with deterministic result as context."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        det = deterministic_answer(session, question)
        return {
            "answer": (
                "⚠️ LLM پیکربندی نشده (LLM_API_KEY در .env نیست).\n\n"
                "پاسخ deterministic:\n\n" + det["answer"]
            ),
            "kind": "fallback_deterministic",
            "sources": det["sources"],
            "data": det["data"],
        }

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    det = deterministic_answer(session, question)
    context = _build_context(session, det.get("data", {}), det.get("sources", []))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"سؤال: {question}\n\nContext:\n{context}\n\n"
                           f"پاسخ deterministic اولیه:\n{det['answer']}\n\n"
                           f"لطفاً پاسخ نهایی را بده. اگر deterministic کافی است، "
                           f"همان را بهبود بده. منابع را ذکر کن.",
            },
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }

    try:
        r = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload, timeout=40.0,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        return {
            "answer": text,
            "kind": "llm",
            "sources": det.get("sources", []) + [f"LLM: {model}"],
            "data": det.get("data", {}),
        }
    except httpx.HTTPStatusError as e:
        logger.warning("LLM HTTP error: {}", e)
        return {
            "answer": (
                f"⚠️ خطای LLM (HTTP {e.response.status_code}).\n\n"
                f"پاسخ deterministic:\n\n" + det["answer"]
            ),
            "kind": "fallback_deterministic",
            "sources": det.get("sources", []),
            "data": det.get("data", {}),
        }
    except Exception as e:
        logger.exception("LLM call failed")
        return {
            "answer": (
                f"⚠️ خطای LLM: {e}\n\nپاسخ deterministic:\n\n" + det["answer"]
            ),
            "kind": "fallback_deterministic",
            "sources": det.get("sources", []),
            "data": det.get("data", {}),
        }