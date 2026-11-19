from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PriceBar, PaperSignal
from ai_assistant.conversation import (
    ChatTurn, is_followup, _find_last_assistant,
    process_message,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed(s, sym="فولاد", n=80):
    from datetime import datetime, timedelta
    inst = Instrument(symbol=sym, name=sym, market="TSE", is_active=True)
    s.add(inst)
    s.commit()
    base = datetime(2025, 1, 1)
    p = 1000.0
    for i in range(n):
        p += 2
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=i),
            open=p, high=p + 5, low=p - 5, close=p,
            volume=1_000_000, value=1_000_000_000, source="t",
        ))
    s.commit()
    return inst


# ---- is_followup ----

def test_is_followup_yes():
    for text in ["چرا؟", "چرا", "دلیلش", "بیشتر توضیح", "چی شد؟"]:
        assert is_followup(text) is True, f"failed: {text}"


def test_is_followup_no():
    for text in ["وضعیت بازار", "تحلیل فولاد", "بهترین نمادها"]:
        assert is_followup(text) is False, f"should be false: {text}"


def test_is_followup_long_text():
    # Long text with "چرا" inside should NOT trigger followup
    text = "می‌خواهم بدانم چرا این سهم رشد کرده و آیا ارزش خرید دارد یا نه"
    assert is_followup(text) is False


# ---- _find_last_assistant ----

def test_find_last_assistant_empty():
    assert _find_last_assistant([]) is None


def test_find_last_assistant_only_user():
    history = [
        ChatTurn(role="user", text="hello"),
        ChatTurn(role="user", text="world"),
    ]
    assert _find_last_assistant(history) is None


def test_find_last_assistant_mixed():
    history = [
        ChatTurn(role="assistant", text="first"),
        ChatTurn(role="user", text="follow"),
        ChatTurn(role="assistant", text="second"),
        ChatTurn(role="user", text="last"),
    ]
    found = _find_last_assistant(history)
    assert found is not None
    assert found.text == "second"


# ---- process_message ----

def test_process_empty_returns_help():
    s = _session()
    try:
        r = process_message(s, "", [])
        assert r.role == "assistant"
        assert r.kind in ("help", "unknown")
    finally:
        s.close()


def test_process_market_query():
    s = _session()
    try:
        _seed(s, "X")
        r = process_message(s, "وضعیت بازار چیست؟", [])
        assert r.role == "assistant"
        assert r.kind == "market"
        assert r.text != ""
    finally:
        s.close()


def test_process_portfolio_empty():
    s = _session()
    try:
        r = process_message(s, "پرتفوی من", [])
        assert r.kind == "portfolio"
        assert "هیچ" in r.text or "خالی" in r.text
    finally:
        s.close()


def test_process_symbol_known():
    s = _session()
    try:
        _seed(s, "فولاد")
        r = process_message(s, "تحلیل فولاد", [])
        assert r.kind == "symbol"
        assert "فولاد" in r.text
        assert r.data.get("symbol") == "فولاد"
    finally:
        s.close()


def test_process_unknown_falls_back():
    s = _session()
    try:
        r = process_message(s, "abcdxyz نامعلوم", [])
        assert r.role == "assistant"
        assert r.text != ""
    finally:
        s.close()


# ---- Follow-up handling ----

def test_followup_market():
    s = _session()
    try:
        _seed(s, "X")
        history = [
            ChatTurn(role="user", text="وضعیت بازار"),
            ChatTurn(role="assistant", text="بازار زرد",
                     kind="market", data={"state": "yellow"}),
        ]
        r = process_message(s, "چرا؟", history)
        assert r.kind == "market"
        assert "توضیح بیشتر" in r.text
    finally:
        s.close()


def test_followup_symbol():
    s = _session()
    try:
        _seed(s, "فولاد")
        history = [
            ChatTurn(role="user", text="تحلیل فولاد"),
            ChatTurn(role="assistant", text="...",
                     kind="symbol", data={"symbol": "فولاد"}),
        ]
        r = process_message(s, "دلیلش چیه؟", history)
        assert r.kind == "symbol"
        assert "فولاد" in r.text
        assert "توضیح بیشتر" in r.text
    finally:
        s.close()


def test_followup_top():
    s = _session()
    try:
        _seed(s, "X")
        history = [
            ChatTurn(role="user", text="بهترین نمادها"),
            ChatTurn(role="assistant", text="...", kind="top"),
        ]
        r = process_message(s, "بیشتر توضیح بده", history)
        # Could route to top or fall back
        assert r.role == "assistant"
        assert r.text != ""
    finally:
        s.close()


def test_followup_without_history():
    s = _session()
    try:
        r = process_message(s, "چرا؟", [])
        # Should not crash, should return something
        assert r.role == "assistant"
        assert r.text != ""
    finally:
        s.close()


# ---- ChatTurn dataclass ----

def test_chat_turn_defaults():
    t = ChatTurn(role="user", text="hi")
    assert t.kind == ""
    assert t.sources == []
    assert t.data == {}
    assert isinstance(t.ts, datetime)