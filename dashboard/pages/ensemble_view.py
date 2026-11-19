"""Ensemble + v2c backtest page with ATR screener and dynamic rr."""

import pandas as pd
import streamlit as st
from sqlalchemy import select, text
from analysis.indicators import atr as compute_atr, sma

from database.session import get_session
from database.models import Instrument, PriceBar
from backtesting.ensemble import backtest_ensemble, compute_ensemble_metrics
from backtesting.strategy_v2c import backtest_v2c, compute_v2c_metrics


@st.cache_data(ttl=3600)
def _compute_atr_screener(session_id: int):
    """Return list of (symbol, n_bars, atr_pct) sorted by atr_pct desc."""
    s = get_session()
    try:
        rows = s.execute(text("""
            SELECT i.symbol, COUNT(p.id) AS n
            FROM instruments i
            JOIN price_bars p ON p.instrument_id = i.id
            WHERE i.is_active = 1
            GROUP BY i.id
            HAVING COUNT(p.id) >= 100
            ORDER BY i.symbol
        """)).all()

        out = []
        for sym, n in rows:
            inst_id = s.scalar(
                select(Instrument.id).where(Instrument.symbol == sym)
            )
            bars = (
                s.query(PriceBar)
                .filter(PriceBar.instrument_id == inst_id)
                .order_by(PriceBar.ts)
                .all()
            )
            if len(bars) < 20:
                continue
            close = pd.Series([b.close for b in bars], dtype=float)
            high = pd.Series([b.high for b in bars], dtype=float)
            low = pd.Series([b.low for b in bars], dtype=float)
            atr_series = compute_atr(high, low, close, 14)
            last_atr = atr_series.iloc[-1]
            last_close = close.iloc[-1]
            if pd.isna(last_atr) or last_close <= 0:
                continue
            atr_pct = last_atr / last_close * 100.0
            out.append({
                "symbol": sym,
                "n_bars": int(n),
                "atr_pct": round(float(atr_pct), 3),
                "min_rr_for_net1": round(1 + 0.01 / (atr_pct / 100.0), 1)
                if atr_pct > 0 else None,
            })
        out.sort(key=lambda x: -x["atr_pct"])
        return out
    finally:
        s.close()


def render(adapter_choice: str):
    st.title("🎯 Ensemble + v2c")
    st.caption(
        "Ensemble فقط وقتی وارد می‌شود که N استراتژی روی همان کندل موافق باشند. "
        "**فیلتر کارمزد** سود هدف پس از کارمزد را تضمین می‌کند."
    )

    s = get_session()
    try:
        tab_ens, tab_v2c, tab_screen = st.tabs(
            ["Ensemble", "v2c", "🔎 اسکرینر ATR"]
        )

        # ============ SCREENER ============
        with tab_screen:
            st.caption(
                "ATR% = نوسان میانگین نسبت به قیمت. **قاعده:** با کارمزد ۱٪ رفت‌وبرگشت، "
                "برای رسیدن به net_R=1 نیاز است: `ATR% ≥ 1/(rr-1)`. "
                "مثلاً rr=3 → ATR% ≥ 0.5٪."
            )
            with st.spinner("محاسبه ATR..."):
                scr = _compute_atr_screener(id(s))

            if not scr:
                st.info("هیچ نمادی با ≥۱۰۰ رکورد نیست.")
            else:
                df = pd.DataFrame(scr)
                st.dataframe(
                    df.rename(columns={
                        "symbol": "نماد",
                        "n_bars": "رکورد",
                        "atr_pct": "ATR%",
                        "min_rr_for_net1": "حداقل rr لازم",
                    }),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    f"مجموع {len(scr)} نماد | "
                    f"با ATR%≥۰.۵٪: {sum(1 for x in scr if x['atr_pct'] >= 0.5)} نماد | "
                    f"با ATR%≥۱٪: {sum(1 for x in scr if x['atr_pct'] >= 1.0)} نماد"
                )

        # ============ ENSEMBLE ============
        with tab_ens:
            symbols = [
                r[0] for r in s.execute(text("""
                    SELECT i.symbol FROM instruments i
                    JOIN price_bars p ON p.instrument_id = i.id
                    WHERE i.is_active = 1
                    GROUP BY i.id HAVING COUNT(p.id) >= 155
                    ORDER BY i.symbol LIMIT 300
                """)).all()
            ]
            if not symbols:
                st.warning("نمادی با ≥۱۵۵ رکورد نیست. اسکریپت backfill_history را اجرا کنید.")
            else:
                ec1, ec2, ec3, ec4 = st.columns(4)
                pick = ec1.selectbox("نماد", symbols, key="ens_sym")
                min_agree = ec2.number_input(
                    "حداقل توافق (از ۳)", 2, 3, 2, 1, key="ens_min",
                )
                rr = ec3.number_input(
                    "R/R هدف", 1.5, 5.0, 3.0, 0.5, key="ens_rr",
                    help="با rr=2 نیاز به ATR%≥۱٪، با rr=3 نیاز به ≥۰.۵٪",
                )
                min_net_r = ec4.number_input(
                    "حداقل Net R (پس از کارمزد)",
                    0.0, 3.0, 1.0, 0.1, key="ens_net",
                )

                if st.button("اجرای Ensemble"):
                    inst_id = s.scalar(
                        select(Instrument.id).where(Instrument.symbol == pick)
                    )
                    bars = s.query(PriceBar).filter(
                        PriceBar.instrument_id == inst_id
                    ).order_by(PriceBar.ts).all()
                    df = pd.DataFrame([
                        {"ts": b.ts, "open": b.open, "high": b.high,
                         "low": b.low, "close": b.close,
                         "volume": b.volume, "value": b.value}
                        for b in bars
                    ])
                    with st.spinner("اجرا..."):
                        res = backtest_ensemble(
                            pick, df,
                            strategies=("v2b", "breakout", "v2c"),
                            min_agreement=int(min_agree),
                            rr_ratio=float(rr),
                            min_net_r=float(min_net_r),
                        )
                    if not res.ok:
                        st.error(res.message)
                    else:
                        if res.rejected_by_fees or res.rejected_by_atr:
                            st.warning(
                                f"⚠️ رد شدگان: {res.rejected_by_fees} توسط کارمزد، "
                                f"{res.rejected_by_atr} توسط ATR کوچک. "
                                f"میانگین ATR% موارد رد شده: **{res.avg_atr_pct_on_rejects}%**"
                                + (
                                    f"\n\n💡 با rr={rr} نیاز به ATR% ≥ "
                                    f"{0.01/(rr-1)*100:.2f}% دارید. "
                                    f"rr را بیشتر کنید یا نماد دیگری انتخاب کنید."
                                    if res.rejected_by_fees else ""
                                )
                            )
                        st.success(res.message)
                        m = res.stats
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("معاملات", m["total"])
                        k2.metric("نرخ برد", f"{m['win_rate']}%")
                        k3.metric("میانگین R", f"{m['avg_r']:.3f}")
                        k4.metric("PF", f"{m['profit_factor']:.2f}")
                        k5, k6, k7 = st.columns(3)
                        k5.metric("حداکثر افت", f"{m['max_dd_r']}R")
                        k6.metric("برد/باخت", f"{m['wins']}/{m['losses']}")
                        if m["strategies_used"]:
                            k7.metric("ترکیب‌ها", len(m["strategies_used"]))

                        if res.trades:
                            st.divider()
                            st.dataframe(
                                [
                                    {
                                        "ورود": t.entry_ts.strftime("%Y-%m-%d"),
                                        "خروج": t.exit_ts.strftime("%Y-%m-%d"),
                                        "ورود P": f"{t.entry:,.0f}",
                                        "خروج P": f"{t.exit:,.0f}",
                                        "حد ضرر": f"{t.stop:,.0f}",
                                        "هدف": f"{t.target:,.0f}",
                                        "R": t.r_multiple,
                                        "دلیل": t.exit_reason,
                                        "استراتژی": "+".join(t.strategies),
                                    }
                                    for t in res.trades
                                ],
                                width="stretch",
                            )
                        if m["strategies_used"]:
                            st.divider()
                            st.caption("توزیع ترکیب‌ها:")
                            st.dataframe(
                                [{"ترکیب": k, "تعداد": v}
                                 for k, v in sorted(
                                     m["strategies_used"].items(),
                                     key=lambda kv: -kv[1])],
                                width="stretch",
                            )

        # ============ V2C ============
        with tab_v2c:
            symbols = [
                r[0] for r in s.execute(text("""
                    SELECT i.symbol FROM instruments i
                    JOIN price_bars p ON p.instrument_id = i.id
                    WHERE i.is_active = 1
                    GROUP BY i.id HAVING COUNT(p.id) >= 155
                    ORDER BY i.symbol LIMIT 300
                """)).all()
            ]
            if not symbols:
                st.warning("نمادی با ≥۱۵۵ رکورد نیست.")
            else:
                vc1, vc2, vc3, vc4 = st.columns(4)
                pick = vc1.selectbox("نماد", symbols, key="v2c_sym")
                vc_atr = vc2.number_input("ATR×", 1.5, 4.0, 2.0, 0.5, key="v2c_atr")
                vc_rr = vc3.number_input("R/R", 1.5, 5.0, 3.0, 0.5, key="v2c_rr")
                vc_net = vc4.number_input("Net R", 0.0, 3.0, 1.0, 0.1, key="v2c_net")
                if st.button("اجرای v2c"):
                    inst_id = s.scalar(
                        select(Instrument.id).where(Instrument.symbol == pick)
                    )
                    bars = s.query(PriceBar).filter(
                        PriceBar.instrument_id == inst_id
                    ).order_by(PriceBar.ts).all()
                    df = pd.DataFrame([
                        {"ts": b.ts, "open": b.open, "high": b.high,
                         "low": b.low, "close": b.close,
                         "volume": b.volume, "value": b.value}
                        for b in bars
                    ])
                    with st.spinner("اجرا..."):
                        res = backtest_v2c(
                            pick, df, atr_mult=vc_atr, rr_ratio=vc_rr,
                            min_net_r=float(vc_net), min_bars=150,
                        )
                    if not res.ok:
                        st.error(res.message)
                    else:
                        st.success(res.message)
                        m = compute_v2c_metrics(res.trades)
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("معاملات", m["total"])
                        k2.metric("نرخ برد", f"{m['win_rate']}%")
                        k3.metric("میانگین R", m["avg_r"])
                        k4.metric("PF", f"{m['profit_factor']:.2f}")
                        if res.trades:
                            st.dataframe(
                                [
                                    {"ورود": t.entry_ts.strftime("%Y-%m-%d"),
                                     "خروج": t.exit_ts.strftime("%Y-%m-%d"),
                                     "قیمت ورود": f"{t.entry:,.0f}",
                                     "خروج": f"{t.exit:,.0f}",
                                     "R": t.r_multiple, "دلیل": t.exit_reason}
                                    for t in res.trades
                                ],
                                width="stretch",
                            )
                        else:
                            st.info(
                                "v2c هیچ معامله‌ای نیافت. "
                                "با rr=3 و نمادی که ATR%≥۰.۵٪ دارد امتحان کنید."
                            )
    finally:
        s.close()