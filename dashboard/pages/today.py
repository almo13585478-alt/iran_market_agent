"""
Landing page: 4-step guided daily workflow.
Uses native Streamlit components — theme-independent, robust.
"""

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import select, func, desc

from database.session import get_session
from database.models import Instrument, PriceBar
from analysis.technical import analyze_instrument
from analysis.market_state import classify_market
from decision_engine.scoring import compute_score, rank_universe
from decision_engine.signal_generator import generate_signal
from news.query import negative_symbols_today
from fundamental.query import get_latest as get_fundamental
from backtesting.group_backtest import max_bars_available
from dashboard.components import render_signal_card, warning_banner


def render(portfolio_value: float, risk_pct: float, min_rr: float) -> None:
    st.title("⚡ امروز")
    st.caption(
        f"{datetime.now().strftime('%A %d %B %Y — %H:%M')} — "
        f"دستیار شما در ۴ گام"
    )

    # ---- Detect state ----
    try:
        s = get_session()
        try:
            state = _detect_state(s)
        finally:
            s.close()
    except Exception as e:
        st.error(f"❌ خطا در تشخیص وضعیت: {type(e).__name__}: {e}")
        import traceback
        with st.expander("Traceback"):
            st.code(traceback.format_exc())
        return

    # ---- Stepper (progress indicators) ----
    _render_stepper(state)

    st.divider()

    # ---- Step 1: Data ----
    st.markdown("### 🔵 گام ۱ · داده")
    try:
        _step_data(state)
    except Exception as e:
        st.error(f"❌ خطا در گام ۱: {type(e).__name__}: {e}")

    st.divider()

    # ---- Step 2: Market ----
    st.markdown("### 🟣 گام ۲ · وضعیت بازار")
    try:
        _step_market(state)
    except Exception as e:
        st.error(f"❌ خطا در گام ۲: {type(e).__name__}: {e}")
        import traceback
        with st.expander("Traceback گام ۲"):
            st.code(traceback.format_exc())

    st.divider()

    # ---- Step 3: Signals ----
    st.markdown("### 🟡 گام ۳ · سیگنال‌های امروز")
    try:
        _step_signals(state, portfolio_value, risk_pct, min_rr)
    except Exception as e:
        st.error(f"❌ خطا در گام ۳: {type(e).__name__}: {e}")
        import traceback
        with st.expander("Traceback گام ۳"):
            st.code(traceback.format_exc())

    st.divider()

    # ---- Step 4: Action ----
    st.markdown("### 🟢 گام ۴ · اقدام نهایی")
    try:
        _step_action(state)
    except Exception as e:
        st.error(f"❌ خطا در گام ۴: {type(e).__name__}: {e}")
        import traceback
        with st.expander("Traceback گام ۴"):
            st.code(traceback.format_exc())


# ===========================================================================
# State detection
# ===========================================================================

def _detect_state(s) -> dict:
    state = {}
    max_bars = max_bars_available(s)
    last_price_ts = s.scalar(select(func.max(PriceBar.ts)))
    n_priced = s.scalar(
        select(func.count(func.distinct(PriceBar.instrument_id)))
    ) or 0
    n_active = s.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    days_old = (datetime.now() - last_price_ts).days if last_price_ts else 999

    state["data_ok"] = (max_bars >= 62 and days_old <= 3 and n_priced >= 5)
    state["max_bars"] = max_bars
    state["n_priced"] = n_priced
    state["n_active"] = n_active
    state["days_old"] = days_old

    try:
        state["market"] = classify_market(s)
    except Exception as e:
        state["market"] = None
        state["market_error"] = str(e)

    rankings = st.session_state.get("rankings", [])
    state["has_rankings"] = len(rankings) > 0
    state["n_rankings"] = len(rankings)
    return state


# ===========================================================================
# Stepper (progress bar)
# ===========================================================================

def _render_stepper(state: dict) -> None:
    data_ok = state["data_ok"]
    market_ok = state.get("market") is not None
    has_rankings = state["has_rankings"]

    steps = [
        {"n": 1, "label": "داده", "done": data_ok},
        {"n": 2, "label": "بازار", "done": data_ok and market_ok},
        {"n": 3, "label": "سیگنال", "done": data_ok and has_rankings},
        {"n": 4, "label": "اقدام", "done": data_ok and has_rankings},
    ]

    cols = st.columns(4)
    for col, step in zip(cols, steps):
        with col:
            if step["done"]:
                st.success(f"✓ گام {step['n']}\n\n**{step['label']}**")
            else:
                st.info(f"● گام {step['n']}\n\n**{step['label']}**")


# ===========================================================================
# Step 1: Data
# ===========================================================================

def _step_data(state: dict) -> None:
    if state["data_ok"]:
        st.success(
            f"✅ داده آماده است — "
            f"{state['n_priced']} از {state['n_active']} نماد دارای قیمت، "
            f"حداکثر {state['max_bars']} کندل، "
            f"آخرین بروزرسانی {state['days_old']} روز پیش."
        )
        return

    # Show issues
    issues = []
    if state["max_bars"] < 62:
        issues.append(f"حداکثر {state['max_bars']} کندل (نیاز ۶۲+)")
    if state["days_old"] > 3:
        issues.append(f"آخرین قیمت {state['days_old']} روز پیش")
    if state["n_priced"] < 5:
        issues.append(f"فقط {state['n_priced']} نماد دارای قیمت")

    st.warning("⚠️ " + "، ".join(issues))

    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "🔄 سینک ۲۰۰ روز تاریخچه",
            key="today_sync_200",
            use_container_width=True,
            type="primary",
        ):
            _run_sync(200)
    with c2:
        if st.button(
            "🔄 سینک ۱۰ روز آخر",
            key="today_sync_10",
            use_container_width=True,
        ):
            _run_sync(10)


def _run_sync(days: int):
    from data_sources.tsetmc import TsetmcAdapter
    from data_engine.sync_engine import sync_all_prices
    s = get_session()
    try:
        prog = st.progress(0.0, text="در حال سینک...")
        def _cb(i, n, sym):
            prog.progress(i / max(n, 1), text=f"{i}/{n} — {sym}")
        r = sync_all_prices(
            TsetmcAdapter(), s, days=days,
            max_instruments=30, pause_seconds=2.5, progress_cb=_cb,
        )
        prog.empty()
        st.success(r.message)
        st.rerun()
    finally:
        s.close()


# ===========================================================================
# Step 2: Market
# ===========================================================================

def _step_market(state: dict) -> None:
    ms = state.get("market")
    if ms is None:
        err = state.get("market_error", "")
        st.info(
            "ℹ️ اطلاعات بازار در دسترس نیست."
            + (f" (خطا: {err})" if err else "")
        )
        return

    if ms.state == "red":
        st.error("🔴 **بازار قرمز** — حجم پوزیشن‌های جدید را نصف کنید. نقد ماندن معتبر است.")
    elif ms.state == "green":
        st.success("🟢 **بازار سبز** — شرایط برای ورود با حجم عادی مساعد است.")
    else:
        st.warning("🟡 **بازار زرد** — با احتیاط عمل کنید. آستانه‌ی ورود بالاتر است.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("امتیاز بازار", f"{ms.score:.0f}")
    c2.metric("عرض امروز", f"{ms.breadth_today}%")
    c3.metric("بالای EMA50", f"{ms.trend_breadth}%")
    c4.metric("نسبت حجم", f"{ms.vol_ratio}×")


# ===========================================================================
# Step 3: Signals
# ===========================================================================

def _step_signals(state, portfolio_value, risk_pct, min_rr):
    if not state["has_rankings"]:
        if not state["data_ok"]:
            st.info("ابتدا گام ۱ را کامل کنید.")
            return
        st.info(
            "هنوز رتبه‌بندی محاسبه نشده. با یک کلیک، ۳۰ نماد برتر تحلیل "
            "می‌شوند (حدود ۳۰ ثانیه)."
        )
                # Show saved-message if any (from prior run)
        _saved_msg = st.session_state.pop("_rankings_saved_msg", None)
        if _saved_msg:
           st.success(f"✅ {_saved_msg} — ذخیره شد، بار بعد سریع بارگذاری می‌شود.")
        if st.button(
            "⚡ تحلیل ۳۰ نماد (سریع)",
            key="today_scan",
            type="primary",
            use_container_width=True,
        ):
            _run_quick_scan()
            st.rerun()
        return

    rankings = st.session_state.get("rankings", [])
    s = get_session()
        # Show "from DB" badge if loaded from cache
    if st.session_state.get("_rankings_from_db"):
        try:
            from analysis.rankings_store import get_batch_meta, format_age
            _s_meta = get_session()
            try:
                _meta = get_batch_meta(_s_meta)
            finally:
                _s_meta.close()
            if _meta:
                st.caption(
                    f"💾 استفاده از رتبه‌بندی ذخیره‌شده "
                    f"({format_age(_meta['age_hours'])}، "
                    f"{_meta['n_symbols']} نماد). "
                    f"برای بروزرسانی، به صفحه «📈 قیمت و تحلیل» بروید."
                )
        except Exception:
            pass
    try:
        neg_map = {n["symbol"]: n for n in negative_symbols_today(s, days=3)}
        signals = []
        for r in rankings[:20]:
            sig = generate_signal(
                r, portfolio_value=portfolio_value,
                risk_per_trade_pct=risk_pct, min_rr=min_rr,
                market_state=state.get("market"),
                recent_negative_news=(r.symbol in neg_map),
            )
            if sig.action == "BUY_CANDIDATE":
                signals.append((sig, r))

        if not signals:
            st.warning(
                "هیچ‌کدام از ۲۰ نماد برتر شرایط خرید کامل ندارند. "
                "**نقد ماندن یک تصمیم معتبر است.**"
            )
            return

        st.success(f"🟢 {len(signals)} سیگنال خرید معتبر")
        top = signals[:3]
        cols = st.columns(len(top)) if len(top) > 1 else [st.container()]
        for col, (sig, score_bd) in zip(cols, top):
            with col:
                df = _load_df(s, sig.symbol, 60)
                render_signal_card(sig, score_bd, df)
    finally:
        s.close()


def _run_quick_scan():
    s = get_session()
    try:
        rows = s.execute(
            select(Instrument.symbol, func.count(PriceBar.id).label("n"))
            .join(PriceBar, PriceBar.instrument_id == Instrument.id)
            .where(Instrument.is_active.is_(True))
            .group_by(Instrument.id)
            .having(func.count(PriceBar.id) >= 60)
            .order_by(desc("n")).limit(30)
        ).all()

        if not rows:
            st.error("هیچ نمادی با ≥۶۰ کندل نیست. اول سینک کنید.")
            return

        prog = st.progress(0.0, text="در حال تحلیل...")
        results = []
        for idx, (sym, _n) in enumerate(rows, 1):
            a = analyze_instrument(s, sym)
            if a.get("ok"):
                fund = get_fundamental(s, sym)
                results.append(compute_score(sym, a, fundamental=fund))
            prog.progress(idx / len(rows), text=f"{idx}/{len(rows)} — {sym}")
        prog.empty()

        if not results:
            st.error("هیچ نمادی تحلیل نشد — داده کافی نیست.")
            return

               results = rank_universe(results)
        results.sort(key=lambda x: -x.total)
        st.session_state["rankings"] = results

        # Persist to DB
        try:
            from analysis.rankings_store import save_rankings
            _save_res = save_rankings(s, results, min_bars=60)
            if _save_res.get("ok"):
                st.session_state["_rankings_saved_msg"] = _save_res["message"]
        except Exception:
            pass
    finally:
        s.close()


def _load_df(session, symbol, bars):
    inst = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        return None
    rows = (
        session.query(PriceBar)
        .filter(PriceBar.instrument_id == inst.id)
        .order_by(PriceBar.ts.desc()).limit(bars).all()
    )
    if not rows:
        return None
    return pd.DataFrame([
        {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume, "value": b.value}
        for b in reversed(rows)
    ])


# ===========================================================================
# Step 4: Action
# ===========================================================================

def _step_action(state):
    if not state["has_rankings"]:
        st.info("ابتدا گام ۳ را کامل کنید.")
        return

    st.markdown("**سه انتخاب دارید:**")
    st.markdown(
        "1. 📝 **Paper Trading** — ثبت معامله فرضی (توصیه برای شروع)\n"
        "2. 💼 **پرتفوی** — مشاهده سود/زیان موقعیت‌های فعلی\n"
        "3. ⚪ **هیچ کاری نکنید** — نقد ماندن یک تصمیم معتبر است"
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("📝 Paper Trading", key="today_paper",
                     use_container_width=True):
            st.session_state["_pending_page"] = "📝 Paper Trading"
            st.rerun()
    with c2:
        if st.button("💼 پرتفوی من", key="today_portfolio",
                     use_container_width=True):
            st.session_state["_pending_page"] = "💼 پرتفوی"
            st.rerun()
    with c3:
        if st.button("📊 تحلیل نماد", key="today_detail",
                     use_container_width=True):
            st.session_state["_pending_page"] = "🔍 تحلیل نماد"
            st.rerun()

    st.divider()
    st.caption(
        "💡 **یادآوری:** اگر امروز تصمیم گرفتید معامله نکنید، این هم یک "
        "تصمیم درست است. صبر کردن هزینه‌ای ندارد، ولی معامله‌ی اشتباه دارد."
    )

    st.markdown("**🚀 برای ادامه:**")
    nc1, nc2, nc3, nc4 = st.columns(4)
    with nc1:
        if st.button("📋 گزارش امروز", key="today_go_daily",
                     use_container_width=True):
            st.session_state["_pending_page"] = "📋 گزارش روزانه"
            st.rerun()
    with nc2:
        if st.button("🎯 همه سیگنال‌ها", key="today_go_signals",
                     use_container_width=True):
            st.session_state["_pending_page"] = "🎯 سیگنال و هشدار"
            st.rerun()
    with nc3:
        if st.button("📖 راهنما", key="today_go_guide",
                     use_container_width=True):
            st.session_state["_pending_page"] = "📖 راهنما"
            st.rerun()
    with nc4:
        if st.button("🛠️ سیستم", key="today_go_system",
                     use_container_width=True):
            st.session_state["_pending_page"] = "🛠️ سیستم"
            st.rerun()