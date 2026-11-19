from decision_engine.scoring import ScoreBreakdown
from decision_engine.signal_generator import generate_signal


def _make_score(total, trend, rsi, last_close, atr, support):
    tech = {
        "ok": True,
        "trend": trend,
        "rsi14": rsi,
        "last_close": last_close,
        "atr14": atr,
        "support": support,
        "signals": ["tests"],
    }
    return ScoreBreakdown(
        symbol="TEST",
        total=total,
        technical_score=total,
        liquidity_score=70,
        momentum_score=60,
        risk_score=80,
        technical=tech,
        reasons=["test"],
    )


def test_buy_candidate_when_strong_uptrend():
    sc = _make_score(75, "up", 55, 1000, 20, 950)
    sig = generate_signal(sc, portfolio_value=10_000_000, risk_per_trade_pct=0.5)
    assert sig.action == "BUY_CANDIDATE"
    assert sig.stop_loss < 1000
    assert sig.target1 > 1000
    assert sig.position_size > 0
    assert sig.rr_ratio >= 2.0


def test_watch_when_score_below_threshold():
    sc = _make_score(50, "up", 55, 1000, 20, 950)
    sig = generate_signal(sc, portfolio_value=10_000_000)
    assert sig.action == "WATCH"
    assert sig.position_size == 0


def test_avoid_when_downtrend():
    sc = _make_score(80, "down", 40, 1000, 20, 950)
    sig = generate_signal(sc, portfolio_value=10_000_000)
    assert sig.action == "AVOID"
    assert "روند نزولی" in sig.invalidations


def test_watch_when_rsi_overbought():
    sc = _make_score(80, "up", 82, 1000, 20, 950)
    sig = generate_signal(sc, portfolio_value=10_000_000)
    assert sig.action == "WATCH"


def test_avoid_when_atr_zero():
    sc = _make_score(80, "up", 55, 1000, 0, 950)
    sig = generate_signal(sc, portfolio_value=10_000_000)
    assert sig.action == "AVOID"


def test_position_size_capped_at_20pct():
    # Huge portfolio, tiny risk per share → cap should engage
    sc = _make_score(80, "up", 55, 1000, 2, 995)
    sig = generate_signal(sc, portfolio_value=100_000_000, risk_per_trade_pct=0.5)
    assert sig.action == "BUY_CANDIDATE"
    max_shares = int(100_000_000 * 0.20 / 1000)
    assert sig.position_size <= max_shares