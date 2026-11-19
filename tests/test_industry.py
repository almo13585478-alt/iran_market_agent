from dataclasses import dataclass

from analysis.industry import aggregate_by_industry, IndustryStat


@dataclass
class _FakeScore:
    symbol: str
    total: float
    technical: dict
    reasons: list
    red_flags: list


def _mk(sym, score, trend="up", rsi=50):
    return _FakeScore(
        symbol=sym, total=score,
        technical={"ok": True, "trend": trend, "rsi14": rsi, "last_close": 1000},
        reasons=[], red_flags=[],
    )


def test_empty_returns_empty():
    assert aggregate_by_industry([], {}) == []


def test_groups_by_industry():
    scores = [
        _mk("A", 80), _mk("B", 60),
        _mk("C", 70), _mk("D", 90),
    ]
    ind_map = {"A": "فلزات", "B": "فلزات", "C": "خودرو", "D": "خودرو"}
    stats = aggregate_by_industry(scores, ind_map)
    assert len(stats) == 2
    labels = {s.industry for s in stats}
    assert labels == {"فلزات", "خودرو"}


def test_fallback_industry_when_missing():
    scores = [_mk("A", 70), _mk("B", 50)]
    ind_map = {}  # no mapping
    stats = aggregate_by_industry(scores, ind_map)
    assert len(stats) == 1
    assert stats[0].industry == "[نامشخص]"
    assert stats[0].n_symbols == 2


def test_top_and_weak_symbols():
    scores = [
        _mk("A", 90), _mk("B", 80), _mk("C", 70),
        _mk("D", 40), _mk("E", 30),
    ]
    ind_map = {s.symbol: "X" for s in scores}
    stats = aggregate_by_industry(scores, ind_map)
    assert len(stats) == 1
    st = stats[0]
    assert st.top_symbols[0][0] == "A"
    assert st.top_symbols[0][1] == 90
    assert st.weak_symbols[0][0] == "E"
    assert st.weak_symbols[0][1] == 30


def test_score_70plus_count():
    scores = [_mk("A", 80), _mk("B", 75), _mk("C", 60), _mk("D", 50)]
    ind_map = {s.symbol: "X" for s in scores}
    st = aggregate_by_industry(scores, ind_map)[0]
    assert st.score_70plus == 2