"""
Conversational chat assistant — defensive version.

- Falls back to deterministic if conversation.py is missing
- Falls back to text_input if st.chat_input unavailable (old Streamlit)
- Always shows welcome message + quick replies
"""

import streamlit as st

from database.session import get_session


# ---- Defensive imports ----
_CONV_OK = True
_CONV_ERROR = ""
try:
    from ai_assistant.conversation import ChatTurn, process_message
except Exception as e:
    _CONV_OK = False
    _CONV_ERROR = f"{type(e).__name__}: {e}"
    ChatTurn = None  # type: ignore
    process_message = None  # type: ignore

_LLM_OK = True
try:
    from ai_assistant.llm import is_configured as _llm_is_configured
except Exception:
    _LLM_OK = False
    def _llm_is_configured():
        return False


_DET_OK = True
try:
    from ai_assistant import deterministic as _det
except Exception:
    _DET_OK = False
    _det = None  # type: ignore


# ---- Detect modern chat UI ----
_HAS_CHAT_UI = hasattr(st, "chat_message") and hasattr(st, "chat_input")
_HAS_TOGGLE = hasattr(st, "toggle")


QUICK_REPLIES = [
    "وضعیت بازار",
    "بهترین نمادها",
    "پرتفوی من",
    "Paper trading",
    "اخبار",
    "وضعیت داده",
    "استراتژی v2b چیست",
    "کمک",
]


def render(adapter_choice: str):
    st.title("🤖 دستیار مکالمه‌ای")
    st.caption(
        "دستیار همیشه با داده واقعی سیستم پاسخ می‌دهد. "
        "حالت deterministic همیشه کار می‌کند. LLM اختیاری است."
    )

    # ---- Status / diagnostics ----
    _render_status()

    # ---- Init session ----
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    # ---- Controls row ----
    _render_controls()

    st.divider()

    # ---- Welcome (always visible if no messages) ----
    if not st.session_state["chat_history"]:
        st.info(
            "👋 سلام! من دستیار تحلیل بورس شما هستم. "
            "می‌توانید از دکمه‌های زیر شروع کنید یا سوال خود را تایپ کنید."
        )

    # ---- Quick replies (always visible) ----
    st.markdown("**💡 پیشنهاد شروع:**")
    cols = st.columns(4)
    for i, q in enumerate(QUICK_REPLIES):
        col = cols[i % 4]
        if col.button(q, key=f"qr_{i}", use_container_width=True):
            _handle_message(q)
            st.rerun()

    st.divider()

    # ---- History ----
    _render_history()

    # ---- Input (chat_input if available, text_input fallback) ----
    _render_input()


# ===========================================================================
# Sub-renderers
# ===========================================================================

def _render_status():
    """Show diagnostic banner if anything is broken."""
    issues = []
    if not _CONV_OK:
        issues.append(f"conversation.py: {_CONV_ERROR}")
    if not _DET_OK:
        issues.append("deterministic.py در دسترس نیست")
    if not _HAS_CHAT_UI:
        issues.append(
            "Streamlit نسخه قدیمی است — از text_input استفاده می‌شود. "
            "برای UI مدرن: `pip install -U streamlit`"
        )

    if issues:
        with st.expander("⚠️ هشدارهای setup (برای عیب‌یابی)", expanded=True):
            for issue in issues:
                st.markdown(f"- {issue}")
            if not _DET_OK and not _CONV_OK:
                st.error(
                    "❌ هیچ‌کدام از ماژول‌های دستیار قابل import نیستند. "
                    "چک کنید `ai_assistant/deterministic.py` و "
                    "`ai_assistant/conversation.py` در پوشه پروژه هستند."
                )
            elif not _CONV_OK:
                st.warning(
                    "⚠️ conversation.py موجود نیست — به حالت deterministic "
                    "برمی‌گردد. برای چت کامل، فایل conversation.py را بسازید."
                )


def _render_controls():
    c1, c2, c3 = st.columns([2, 1, 1])

    llm_ready = _llm_is_configured() if _LLM_OK else False
    with c1:
        if _HAS_TOGGLE:
            use_llm = st.toggle(
                "استفاده از LLM",
                value=llm_ready,
                disabled=not llm_ready,
                help=(
                    "برای فعال‌سازی، LLM_API_KEY را در .env تنظیم کنید. "
                    "بدون کلید، حالت deterministic استفاده می‌شود."
                ),
            )
        else:
            use_llm = st.checkbox(
                "استفاده از LLM",
                value=llm_ready,
                disabled=not llm_ready,
            )
        st.session_state["_use_llm"] = use_llm

        if not llm_ready:
            st.caption(
                "💡 LLM پیکربندی نشده — پاسخ‌ها deterministic خواهند بود."
            )

    with c2:
        st.write("")
        if st.button("🗑 پاک کردن", use_container_width=True):
            st.session_state["chat_history"] = []
            st.rerun()

    with c3:
        n_turns = len(
            [h for h in st.session_state.get("chat_history", [])
             if getattr(h, "role", "") == "user"]
        )
        st.metric("پیام‌ها", n_turns)


def _render_history():
    history = st.session_state.get("chat_history", [])
    if not history:
        return

    for turn in history:
        if _HAS_CHAT_UI:
            with st.chat_message(turn.role):
                st.markdown(turn.text)
                if turn.role == "assistant" and getattr(turn, "sources", None):
                    st.caption("منابع: " + " | ".join(turn.sources))
        else:
            # Fallback: plain markdown
            icon = "👤" if turn.role == "user" else "🤖"
            st.markdown(f"**{icon}** {turn.text}")
            if turn.role == "assistant" and getattr(turn, "sources", None):
                st.caption("منابع: " + " | ".join(turn.sources))
            st.markdown("---")


def _render_input():
    use_llm = st.session_state.get("_use_llm", False)

    if _HAS_CHAT_UI:
        msg = st.chat_input("سوال خود را بنویسید...")
        if msg:
            _handle_message(msg, use_llm=use_llm)
            st.rerun()
    else:
        # Fallback for old Streamlit
        c1, c2 = st.columns([4, 1])
        text = c1.text_input(
            "سوال", key="fallback_chat_input",
            label_visibility="collapsed",
            placeholder="سوال خود را بنویسید...",
        )
        if c2.button("ارسال", key="fallback_send", use_container_width=True):
            if text.strip():
                _handle_message(text.strip(), use_llm=use_llm)
                st.rerun()


# ===========================================================================
# Message handler
# ===========================================================================

def _handle_message(msg: str, use_llm: bool | None = None) -> None:
    if use_llm is None:
        use_llm = st.session_state.get("_use_llm", False)

    if _CONV_OK and process_message is not None and ChatTurn is not None:
        # Modern path: use conversation module
        s = get_session()
        try:
            with st.spinner("در حال پردازش..."):
                history_before = list(
                    st.session_state.get("chat_history", [])
                )
                response = process_message(
                    s, msg, history_before, use_llm=use_llm,
                )
        finally:
            s.close()

        st.session_state["chat_history"].append(
            ChatTurn(role="user", text=msg)
        )
        st.session_state["chat_history"].append(response)
        return

    # Fallback path: use deterministic directly
    if not _DET_OK or _det is None:
        st.session_state["chat_history"].append(
            ChatTurn(role="user", text=msg) if ChatTurn
            else _SimpleTurn(role="user", text=msg)
        )
        st.session_state["chat_history"].append(
            ChatTurn(
                role="assistant",
                text="❌ هیچ ماژول دستیاری بارگذاری نشده. "
                     "چک کنید فایل‌های ai_assistant موجودند.",
                kind="error", sources=[], data={},
            ) if ChatTurn
            else _SimpleTurn(
                role="assistant",
                text="❌ ماژول دستیار بارگذاری نشده.",
            )
        )
        return

    s = get_session()
    try:
        with st.spinner("در حال پردازش..."):
            result = _det.answer(s, msg)
    finally:
        s.close()

    st.session_state["chat_history"].append(
        ChatTurn(role="user", text=msg) if ChatTurn
        else _SimpleTurn(role="user", text=msg)
    )
    st.session_state["chat_history"].append(
        ChatTurn(
            role="assistant",
            text=result.get("answer", "پاسخی یافت نشد."),
            kind=result.get("kind", "unknown"),
            sources=result.get("sources", []),
            data=result.get("data", {}),
        ) if ChatTurn
        else _SimpleTurn(
            role="assistant",
            text=result.get("answer", "پاسخی یافت نشد."),
        )
    )


class _SimpleTurn:
    """Fallback for when ChatTurn isn't available (missing conversation.py)."""
    def __init__(self, role: str, text: str):
        self.role = role
        self.text = text
        self.sources = []
        self.data = {}
        self.kind = ""