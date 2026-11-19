"""
Conversational chat layer over the deterministic assistant.

- ChatTurn dataclass for history
- process_message(): routes user text, handles follow-ups, optional LLM
- Deterministic answer is always the base; LLM only enhances if configured
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from ai_assistant import deterministic as det


FOLLOWUP_KEYWORDS = [
    "چرا", "دلیل", "دلایل", "بیشتر", "توضیح", "جزئیات",
    "چی شد", "چطور شد", "شرح بده", "باز کن",
]


@dataclass
class ChatTurn:
    role: str  # "user" | "assistant"
    text: str
    kind: str = ""
    sources: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    ts: datetime = field(default_factory=datetime.now)


def is_followup(text: str) -> bool:
    """Short message with follow-up keywords."""
    text = (text or "").strip()
    if not text or len(text) > 20:
        return False
    for kw in FOLLOWUP_KEYWORDS:
        if kw in text:
            return True
    return False


def _find_last_assistant(history: list[ChatTurn]) -> ChatTurn | None:
    for h in reversed(history):
        if h.role == "assistant":
            return h
    return None


def _route_followup(
    session: Session, last_turn: ChatTurn, user_text: str,
) -> ChatTurn | None:
    """Expand on the previous assistant answer."""
    kind = last_turn.kind

    if kind == "market":
        result = det._handle_market(session)
        return ChatTurn(
            role="assistant",
            text="**توضیح بیشتر — وضعیت بازار:**\n\n" + result["answer"],
            kind="market",
            sources=result.get("sources", []),
            data=result.get("data", {}),
        )

    if kind == "symbol":
        symbol = last_turn.data.get("symbol")
        if symbol:
            result = det._handle_symbol(session, symbol)
            return ChatTurn(
                role="assistant",
                text=f"**توضیح بیشتر — {symbol}:**\n\n" + result["answer"],
                kind="symbol",
                sources=result.get("sources", []),
                data=result.get("data", {}),
            )

    if kind == "top":
        result = det._handle_top(session)
        return ChatTurn(
            role="assistant",
            text="**توضیح بیشتر — بهترین نمادها:**\n\n" + result["answer"],
            kind="top",
            sources=result.get("sources", []),
            data=result.get("data", {}),
        )

    if kind == "paper":
        result = det._handle_paper(session)
        return ChatTurn(
            role="assistant",
            text="**توضیح بیشتر — Paper Trading:**\n\n" + result["answer"],
            kind="paper",
            sources=result.get("sources", []),
            data=result.get("data", {}),
        )

    if kind == "portfolio":
        result = det._handle_portfolio(session)
        return ChatTurn(
            role="assistant",
            text="**توضیح بیشتر — پرتفوی:**\n\n" + result["answer"],
            kind="portfolio",
            sources=result.get("sources", []),
            data=result.get("data", {}),
        )

    return None


def _llm_enhance(
    current_question: str,
    history: list[ChatTurn],
    det_result: dict,
) -> str | None:
    """Optional: enhance with LLM, using history as context."""
    import httpx

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    system_prompt = (
        "تو یک دستیار تحلیل بورس ایران هستی. قواعد:\n"
        "1. فقط بر اساس داده‌ای که در deterministic_answer داده شده پاسخ بده.\n"
        "2. اگر داده کافی نیست، صریح بگو «داده کافی نیست».\n"
        "3. کوتاه و ساختاریافته. فارسی روان.\n"
        "4. هیچ پیش‌بینی قطعی نکن — بگو «بر اساس داده فعلی».\n"
        "5. هیچ سفارش واقعی پیشنهاد نکن — فقط تحلیل."
    )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Last 6 turns as context
    for turn in history[-6:]:
        role = "user" if turn.role == "user" else "assistant"
        messages.append({
            "role": role,
            "content": turn.text[:800],
        })

    sources = ", ".join(det_result.get("sources", [])) or "-"
    messages.append({
        "role": "user",
        "content": (
            f"سوال فعلی: {current_question}\n\n"
            f"پاسخ deterministic (پایه):\n{det_result['answer']}\n\n"
            f"منابع: {sources}\n\n"
            f"پاسخ نهایی را بده. اگر deterministic کافی است، "
            f"همان را بهبود بده. در پایان منابع را ذکر کن."
        ),
    })

    try:
        r = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 600,
            },
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def process_message(
    session: Session,
    message: str,
    history: list[ChatTurn],
    use_llm: bool = False,
) -> ChatTurn:
    """Route one user message to an assistant response."""
    text = (message or "").strip()
    if not text:
        return ChatTurn(
            role="assistant", text="سوالی وارد نشده.",
            kind="help", sources=[], data={},
        )

    # Follow-up handling
    if is_followup(text) and history:
        last = _find_last_assistant(history)
        if last is not None:
            expanded = _route_followup(session, last, text)
            if expanded is not None:
                return expanded

    # Base deterministic answer
    result = det.answer(session, text)
    answer_text = result["answer"]

    # Optional LLM enhancement
    if use_llm:
        from ai_assistant.llm import is_configured
        if is_configured():
            enhanced = _llm_enhance(text, history, result)
            if enhanced:
                answer_text = enhanced

    return ChatTurn(
        role="assistant",
        text=answer_text,
        kind=result.get("kind", "unknown"),
        sources=result.get("sources", []),
        data=result.get("data", {}),
    )