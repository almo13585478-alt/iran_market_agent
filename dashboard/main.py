import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st
from sqlalchemy import select, func, desc, text

from database.init_db import init_database
from database.session import get_session
from database.models import (
    Instrument, PriceBar, DataSourceStatus, PortfolioTransaction, PaperSignal,
    AuditLog, NewsItem, NewsSymbolLink,
)
from database.audit import log_event
from data_sources.tsetmc import TsetmcAdapter
from data_sources.csv_importer import CsvImporterAdapter
from data_engine.sync_engine import sync_instruments, sync_prices, sync_all_prices
from analysis.technical import analyze_instrument
from analysis.market_state import classify_market
from analysis.industry import build_industry_map, aggregate_by_industry
from decision_engine.scoring import compute_score, rank_universe
from decision_engine.signal_generator import generate_signal
from portfolio.holdings import compute_holdings
from portfolio.performance import build_report
from alerts.engine import check_alerts
from backtesting.simple_backtest import backtest_symbol, compute_metrics
from backtesting.group_backtest import (
    run_group_backtest, pick_top_eligible_symbols,
    get_symbol_bar_counts, max_bars_available, count_eligible,
)
from backtesting.strategy_compare import run_grid_search, rank_combos
from backtesting.strategy_v2 import backtest_v2, compute_v2_metrics
from backtesting.strategy_v2b import backtest_v2b, compute_metrics as compute_v2b_metrics
from backtesting.strategy_breakout import backtest_breakout, compute_bo_metrics
from backtesting.walk_forward import run_walk_forward_top
from backtesting.v2_group import run_v2_group
from backtesting.strategy_v2c import backtest_v2c, compute_v2c_metrics
from backtesting.ensemble import backtest_ensemble, compute_ensemble_metrics
from paper_trading.scanner import scan as paper_scan
from paper_trading.tracker import update_open_signals, paper_stats
from reports.daily_report import build_daily_report, to_markdown
from reports.benchmark import take_snapshot, get_history, paper_equity_curve
from app.scheduler import start_scheduler, stop_scheduler, scheduler_status
from news.ingest import add_news, import_csv as news_import_csv
from news.query import has_recent_negative_news, negative_symbols_today, recent_news
from fundamental.query import get_latest as get_fundamental
from dashboard.pages.fundamental import render as fundamental_render
from dashboard.pages.home import render as home_render
from dashboard.pages.macro import render as macro_render
from dashboard.pages.system import render as system_render
from dashboard.pages.auto_walk_forward import render as auto_wf_render
from dashboard.pages.symbol_detail import render as symbol_detail_render
from dashboard.pages.assistant import render as assistant_render
from dashboard.pages.codal import render as codal_render
from dashboard.pages.weekly_report import render as weekly_render
from dashboard.pages.paper_analytics import render as paper_analytics_render
from dashboard.pages.rss_news import render as rss_news_render
from dashboard.pages.ensemble_view import render as ensemble_render

st.set_page_config(page_title="دستیار بورس ایران", page_icon="📈", layout="wide")
# Initialize theme (default: light) and apply CSS
if "theme_toggle" not in st.session_state:
    st.session_state["theme_toggle"] = "روشن"

_theme_key = "dark" if st.session_state["theme_toggle"] == "تیره" else "light"
from dashboard.themes import get_css
st.markdown(get_css(_theme_key), unsafe_allow_html=True)
# ============ Load persisted settings into session_state (once) ============
if "_settings_loaded" not in st.session_state:
    try:
        _s_init = get_session()
        try:
            _settings = get_all_settings(_s_init)
        finally:
            _s_init.close()
        for _k, _v in _settings.items():
            st.session_state[f"_setting_{_k}"] = _v
        # Theme: honor saved setting on first load
        if _settings.get("theme") in ("روشن", "تیره"):
            st.session_state["theme_toggle"] = _settings["theme"]
    except Exception:
        # Fall back to defaults — do not crash the app
        pass
    st.session_state["_settings_loaded"] = True
# ============================================================================
# ============ Auto-load rankings from DB (once per session) ============
if "_rankings_loaded_from_db" not in st.session_state:
    if not st.session_state.get("rankings"):
        try:
            _s_r = get_session()
            try:
                _cached = load_latest_rankings(_s_r)
            finally:
                _s_r.close()
            if _cached:
                st.session_state["rankings"] = _cached
                st.session_state["_rankings_loaded_from_db"] = True
                st.session_state["_rankings_from_db"] = True
        except Exception:
            pass
    st.session_state["_rankings_loaded_from_db"] = True
# =====================================================================
init_database()


def _adapter(kind: str):
    return TsetmcAdapter() if kind.startswith("TSETMC") else CsvImporterAdapter()


# ================= Sidebar =================

with st.sidebar:
    # Theme selector
    st.radio(
        "🎨 تم",
        ["روشن", "تیره"],
        horizontal=True,
        key="theme_toggle",
    )
    st.divider()

    st.header("مدیریت داده")
    adapter_choice = st.selectbox("منبع داده", ["TSETMC (غیررسمی)", "CSV محلی"])

    if st.button("تست اتصال"):
        with st.spinner("تست..."):
            r = _adapter(adapter_choice).health_check()
        (st.success if r.ok else st.error)(r.message)

    if st.button("همگام‌سازی نمادها"):
        with st.spinner("دریافت..."):
            s = get_session()
            try:
                r = sync_instruments(_adapter(adapter_choice), s)
            finally:
                s.close()
        (st.success if r.ok else st.error)(r.message)

    st.divider()
    max_n = st.number_input("حداکثر نماد سینک", 1, 800, 50, 10)
    pause_s = st.number_input("فاصله درخواست (ثانیه)", 1.0, 10.0, 2.5, 0.5)
    sync_days = st.number_input("تعداد روز", 60, 500, 200, 20)
    if st.button(f"سینک قیمت ({max_n} × {sync_days} روز)"):
        s = get_session()
        try:
            bar = st.progress(0.0, text="شروع...")

            def _cb(i, n, sym):
                bar.progress(min(i / max(n, 1), 1.0), text=f"{i}/{n} — {sym}")

            r = sync_all_prices(
                _adapter(adapter_choice), s, days=int(sync_days),
                max_instruments=int(max_n),
                pause_seconds=float(pause_s), progress_cb=_cb,
            )
            bar.empty()
        finally:
            s.close()
        (st.success if r.ok else st.error)(r.message)
        st.rerun()

    st.divider()
    st.subheader("تنظیمات معامله")
    portfolio_value = st.number_input(
        "ارزش سبد (ریال)", 1_000_000, 10_000_000_000_000,
        value=int(st.session_state.get("_setting_portfolio_value", 1_000_000_000)),
        step=100_000_000, format="%d",
        help=(
            "کل سرمایه‌ای که در این استراتژی می‌گذارید. "
            "حجم پیشنهادی هر معامله بر این اساس محاسبه می‌شود."
        ),
    )
    risk_pct = st.number_input(
        "ریسک هر معامله (٪)", 0.1, 5.0,
        value=float(st.session_state.get("_setting_risk_pct", 0.5)),
        step=0.1,
        help=(
            "چند درصد از سبد در هر معامله ریسک می‌کنید؟ "
            "۰.۵٪ استاندارد است."
        ),
    )
    min_rr = st.number_input(
        "حداقل R/R", 1.0, 5.0,
        value=float(st.session_state.get("_setting_min_rr", 2.0)),
        step=0.5,
        help=(
            "نسبت حداقل سود به ریسک. زیر ۲ ارزش معامله ندارد."
        ),
    )

    # Save button
    if st.button("💾 ذخیره تنظیمات", key="save_sidebar_settings",
                 use_container_width=True):
        try:
            _s_save = get_session()
            try:
                save_all_settings(_s_save, {
                    "portfolio_value": int(portfolio_value),
                    "risk_pct": float(risk_pct),
                    "min_rr": float(min_rr),
                    "theme": st.session_state.get("theme_toggle", "روشن"),
                })
                st.session_state["_setting_portfolio_value"] = int(portfolio_value)
                st.session_state["_setting_risk_pct"] = float(risk_pct)
                st.session_state["_setting_min_rr"] = float(min_rr)
                st.success("✅ ذخیره شد")
            finally:
                _s_save.close()
        except Exception as e:
            st.error(f"خطای ذخیره: {e}")

    # Sidebar quick help
    try:
        from dashboard.pages.guide import render_sidebar_help
        render_sidebar_help()
    except Exception:
        pass

    # Active settings summary
    with st.expander("📋 خلاصه تنظیمات فعال", expanded=False):
        st.markdown(
            f"<div style='font-size:13px;line-height:1.9;'>"
            f"💰 سرمایه: <b>{portfolio_value:,.0f}</b> ریال<br>"
            f"📊 ریسک: <b>{risk_pct:.2f}%</b><br>"
            f"⚖️ حداقل R/R: <b>{min_rr:.1f}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
        _ms = st.session_state.get("market_state")
        if _ms:
            _icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(_ms.state, "⚪")
            st.markdown(
                f"<div style='font-size:13px;'>"
                f"{_icon} بازار: <b>{_ms.state}</b><br>"
                f"📉 آستانه: +{_ms.score_bonus:.0f} | "
                f"حجم: ×{_ms.size_multiplier}"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.divider()
    st.subheader("زمان‌بند")
    sched = scheduler_status()
    if sched["running"]:
        st.success("فعال")
        for j in sched["jobs"]:
            st.caption(f"• {j['id']} → {j['next_run']}")
        if st.button("⏹ توقف"):
            stop_scheduler()
            st.rerun()
    else:
        st.info("خاموش")
        if st.button("▶️ فعال‌سازی"):
            start_scheduler()
            st.rerun()

# ================= Navigation (14 pages) =================
# Consume pending cross-page navigation BEFORE the page_selector widget
# is created. This avoids the "cannot be modified after the widget with that
# key is instantiated" error.
if "_pending_page" in st.session_state:
    st.session_state["page_selector"] = st.session_state.pop("_pending_page")
if "_pending_symbol" in st.session_state:
    _sym = st.session_state.pop("_pending_symbol")
    st.session_state["_force_symbol"] = _sym
    st.session_state["detail_symbol"] = _sym

if "page_selector" not in st.session_state:
    st.session_state["page_selector"] = "🏠 داشبورد"

# ================= Section-based navigation =================

SECTION_STRUCTURE = {
    "⚡ روزانه": [
        "⚡ امروز",
        "🏠 داشبورد",
        "🎯 سیگنال و هشدار",
        "📋 گزارش روزانه",
        "📅 گزارش هفتگی",
    ],
    "📊 تحلیل": [
        "🔍 تحلیل نماد",
        "📈 قیمت و تحلیل",
        "🏭 صنعت",
        "📊 بنیادی",
        "🌍 کلان",
        "📰 اخبار",
        "📡 RSS",
        "📥 Codal",
        "🤖 دستیار",
    ],
    "💼 سبد": [
        "💼 پرتفوی",
        "📝 Paper Trading",
        "📊 تحلیل Paper",
        "📊 گزارش عملکرد",
    ],
    "🔬 پیشرفته": [
        "🧪 بک‌تست",
        "🎯 Ensemble",
        "📊 WF خودکار",
        "🔬 استراتژی‌ها",
        "⚙️ تنظیمات",
        "📖 راهنما",
        "🛠️ سیستم",
        "⚙️ داده",
],
}

# Handle pending navigation (must be before widget creation)
if "_pending_page" in st.session_state:
    _target = st.session_state.pop("_pending_page")
    for _sec, _pages in SECTION_STRUCTURE.items():
        if _target in _pages:
            st.session_state["section_selector"] = _sec
            st.session_state[f"page_selector_{_sec}"] = _target
            break

# Ensure defaults
if "section_selector" not in st.session_state:
    st.session_state["section_selector"] = "⚡ روزانه"

_section = st.sidebar.radio(
    "📂 بخش",
    list(SECTION_STRUCTURE.keys()),
    key="section_selector",
)

page = st.sidebar.radio(
    "📑 صفحه",
    SECTION_STRUCTURE[_section],
    key=f"page_selector_{_section}",
)

# Global alert banner (skip on data + alerts pages to avoid duplicate work)
if page not in ("⚙️ داده", "🎯 سیگنال و هشدار"):
    try:
        from dashboard.components import render_global_alert_banner
        render_global_alert_banner()
    except Exception:
        pass
# ============ Breadcrumb + page intro (defensive) ============
try:
    from dashboard.help_content import PAGE_HELP

    _section_short = {
        "⚡ روزانه": "روزانه",
        "📊 تحلیل": "تحلیل",
        "💼 سبد": "سبد",
        "🔬 پیشرفته": "پیشرفته",
    }.get(_section, str(_section).strip("⚡📊💼🔬 ").strip())

    st.markdown(
        f"<div style='font-size:11px;color:var(--text-muted);"
        f"margin-bottom:6px;opacity:0.85;'>"
        f"🏠 {_section_short}"
        f"<span style='margin:0 6px;color:var(--border-strong);'>›</span>"
        f"<span style='color:var(--text-primary);font-weight:600;'>"
        f"{page}</span></div>",
        unsafe_allow_html=True,
    )

    _help = PAGE_HELP.get(page)
    if _help and page not in ("📖 راهنما",):
        with st.expander(
            f"ℹ️ راهنمای این صفحه — {_help['title']}",
            expanded=False,
        ):
            st.markdown(
                f"<div style='font-size:13px;line-height:1.9;'>"
                f"<b>چه کاری اینجا؟</b> {_help['what']}<br>"
                f"<b>چه زمانی؟</b> {_help['when']}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown("**گام‌های صفحه:**")
            for _s in _help["steps"]:
                st.markdown(f"- {_s}")
except Exception as _e:
    # Never block the app for a help widget
    pass
# ============================================================

# ============================================================
# PAGE: 🏠 داشبورد
# ============================================================
if page == "⚡ امروز":
    st.markdown(
        f"<div style='background:rgba(255,193,7,0.15);"
        f"padding:10px;border-radius:6px;margin:6px 0;"
        f"font-size:13px;'>"
        f"🔧 DEBUG: branch «امروز» اجرا شد. "
        f"page={repr(page)} | "
        f"len={len(page)} | "
        f"portfolio={portfolio_value}"
        f"</div>",
        unsafe_allow_html=True,
    )
    try:
        from dashboard.pages.today import render as today_render
        from dashboard.pages.settings import render as settings_render
        from analysis.rankings_store import (
    clear_rankings,
    get_batch_meta as rankings_meta,
    load_latest_rankings,
    save_rankings,
    format_age as rankings_age_text,
)
        from settings.manager import get_all as get_all_settings, save_all as save_all_settings
        today_render(
            portfolio_value=float(portfolio_value),
            risk_pct=float(risk_pct),
            min_rr=float(min_rr),
        )
        st.success("✅ today_render() بدون خطا تمام شد.")
    except Exception as _e:
        import traceback
        st.error(
            f"❌ خطا در صفحه «امروز»: "
            f"{type(_e).__name__}: {_e}"
        )
        with st.expander("🔍 Traceback کامل (این را برای من بفرستید)", expanded=True):
            st.code(traceback.format_exc())

if page == "⚙️ تنظیمات":
    try:
        from dashboard.pages.settings import render as settings_render
        settings_render(adapter_choice)
    except Exception as _e:
        import traceback
        st.error(f"❌ خطا در صفحه تنظیمات: {type(_e).__name__}: {_e}")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())


if page == "📖 راهنما":
    try:
        from dashboard.pages.guide import render as guide_render
        guide_render(adapter_choice)
    except Exception as _e:
        import traceback
        st.error(f"❌ خطا در صفحه راهنما: {type(_e).__name__}: {_e}")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())



if page == "🏠 داشبورد":
    home_render(
        portfolio_value=float(portfolio_value),
        risk_pct=float(risk_pct),
        min_rr=float(min_rr),
    )



# ============================================================
# PAGE: 📋 گزارش روزانه
# ============================================================
elif page == "📡 RSS":
    rss_news_render(adapter_choice)
elif page == "🎯 Ensemble":
    ensemble_render(adapter_choice)

elif page == "📊 تحلیل Paper":
    paper_analytics_render(adapter_choice)
elif page == "🔍 تحلیل نماد":
    symbol_detail_render(
        portfolio_value=float(portfolio_value),
        risk_pct=float(risk_pct),
        min_rr=float(min_rr),
    )


elif page == "📋 گزارش روزانه":
    st.title("📋 گزارش روزانه")
    st.caption("گزارش پایان روز طبق سند §۲۲ — با ۱۰ نماد برتر و تحلیل صنعت.")

    s = get_session()
    try:
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("📸 ثبت snapshot بازار"):
                snap = take_snapshot(s)
                st.success(
                    f"Snapshot {snap.trade_date.strftime('%Y-%m-%d')} ثبت شد — "
                    f"شاخص {snap.eq_index:.2f} ({snap.eq_index_pct:+.2f}%)"
                )
        with col2:
            if st.button("📄 تولید گزارش"):
                rankings = st.session_state.get("rankings", [])
                st.session_state["daily_report"] = build_daily_report(
                    s, rankings=rankings if rankings else None,
                )
        with col3:
            if st.button("🔄 پاک‌سازی گزارش"):
                st.session_state.pop("daily_report", None)

        rpt = st.session_state.get("daily_report")
        if rpt:
            if not rpt.rankings_provided:
                st.warning(
                    "⚠️ رتبه‌بندی نمادها محاسبه نشده. برای دیدن ۱۰ نماد برتر و "
                    "صنایع، ابتدا در صفحه «قیمت و تحلیل» رتبه‌بندی را محاسبه کنید."
                )

            md = to_markdown(rpt)
            st.download_button(
                label="⬇️ دانلود Markdown",
                data=md.encode("utf-8"),
                file_name=f"daily_report_{rpt.trade_date}.md",
                mime="text/markdown",
            )
            st.markdown(md)

        st.divider()
        st.subheader("📈 بنچمارک (شاخص هم‌وزن)")

        history = get_history(s, limit=200)
        if history:
            df = pd.DataFrame([
                {"date": h.trade_date, "index": h.eq_index,
                 "pct": h.eq_index_pct, "state": h.state}
                for h in history
            ])
            st.line_chart(df.set_index("date")[["index"]], height=250)

            h1, h2, h3 = st.columns(3)
            h1.metric("آخرین شاخص", f"{history[-1].eq_index:.2f}",
                     delta=f"{history[-1].eq_index_pct:+.2f}%")
            h2.metric("تعداد روزها", len(history))
            total_chg = (
                (history[-1].eq_index - history[0].eq_index)
                / history[0].eq_index * 100.0
            ) if history[0].eq_index > 0 else 0.0
            h3.metric("تغییر کل", f"{total_chg:+.2f}%")
        else:
            st.info("هنوز snapshot ثبت نشده. دکمه «📸 ثبت snapshot» را بزنید.")

        st.divider()
        st.subheader("📊 منحنی Paper Trading (Cumulative R)")
        curve = paper_equity_curve(s)
        if curve:
            df_eq = pd.DataFrame(
                [{"exit_date": d, "cum_r": r} for d, r in curve]
            )
            st.line_chart(df_eq.set_index("exit_date")[["cum_r"]], height=250)
        else:
            st.info("هنوز Paper Signal بسته‌ای وجود ندارد.")

        st.divider()
        st.subheader("📜 لاگ ممیزی (آخرین ۵۰ رخداد)")
        logs = s.scalars(
            select(AuditLog).order_by(AuditLog.ts.desc()).limit(50)
        ).all()
        if logs:
            st.dataframe(
                [
                    {
                        "زمان": x.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "دسته": x.category,
                        "عملیات": x.action,
                        "موضوع": x.subject or "-",
                        "جزئیات": (x.details or "-")[:80],
                        "موفق": "✅" if x.success else "❌",
                    }
                    for x in logs
                ],
                width="stretch",
            )
        else:
            st.info("هنوز رخدادی ثبت نشده.")
    finally:
        s.close()


# ============================================================
# PAGE: 📰 اخبار
# ============================================================
elif page == "📅 گزارش هفتگی":
    weekly_render(adapter_choice)


elif page == "📰 اخبار":
    st.title("📰 اخبار")
    st.caption(
        "طبقه‌بندی خودکار مبتنی بر کلیدواژه — بدون LLM. "
        "اخبار منفی به‌طور خودکار سیگنال خرید را به «پیگیری» تغییر می‌دهند."
    )

    s = get_session()
    try:
        tab_recent, tab_add, tab_import, tab_negative = st.tabs(
            ["آخرین اخبار", "افزودن دستی", "وارد کردن CSV", "نمادهای با خبر منفی"]
        )

        with tab_recent:
            fc1, fc2, fc3 = st.columns(3)
            f_cat = fc1.selectbox(
                "دسته",
                ["همه", "company", "macro", "industry", "political", "global", "other"],
            )
            f_impact = fc2.selectbox("تأثیر", ["همه", "high", "medium", "low"])
            f_sym = fc3.text_input("نماد (اختیاری)").strip() or None

            news_list = recent_news(
                s, limit=100,
                category=None if f_cat == "همه" else f_cat,
                impact=None if f_impact == "همه" else f_impact,
                symbol=f_sym,
            )
            if not news_list:
                st.info("خبری با این فیلترها نیست.")
            else:
                st.caption(f"{len(news_list)} خبر")
                for n in news_list[:50]:
                    links = s.scalars(
                        select(NewsSymbolLink).where(NewsSymbolLink.news_id == n.id)
                    ).all()
                    syms = ", ".join(x.symbol for x in links) or "-"
                    sent_icon = "🟢" if n.sentiment_score > 0.2 else (
                        "🔴" if n.sentiment_score < -0.2 else "⚪"
                    )
                    with st.expander(
                        f"{sent_icon} {n.published_at.strftime('%m-%d %H:%M')} — "
                        f"{n.title[:90]}"
                    ):
                        st.markdown(f"**تأثیر:** {n.impact} | **احساس:** "
                                    f"{n.sentiment_score:+.2f} | **دسته:** {n.category}")
                        st.markdown(f"**نمادها:** {syms}")
                        if n.body:
                            st.markdown(n.body)
                        if n.source_url:
                            st.markdown(f"[منبع]({n.source_url})")

        with tab_add:
            with st.form("add_news_form", clear_on_submit=True):
                n_title = st.text_input("عنوان *").strip()
                n_body = st.text_area("متن (اختیاری)", height=100)
                nc1, nc2, nc3 = st.columns(3)
                n_source = nc1.text_input("منبع", value="manual")
                n_url = nc2.text_input("URL منبع (اختیاری)")
                n_cred = nc3.selectbox(
                    "اعتبار", [1, 2, 3], index=1,
                    format_func=lambda x: {1: "بالا", 2: "متوسط", 3: "پایین"}[x],
                )
                submitted = st.form_submit_button("افزودن خبر")

            if submitted and n_title:
                created, msg, _ = add_news(
                    s, n_title, n_body, source=n_source,
                    source_url=n_url or None, credibility=int(n_cred),
                )
                (st.success if created else st.warning)(msg)

        with tab_import:
            st.caption(
                "ستون‌های لازم: `title`. اختیاری: `body`, `source`, "
                "`source_url`, `published_at` (YYYY-MM-DD HH:MM), `credibility`."
            )
            uploaded = st.file_uploader("فایل CSV", type=["csv"])
            if uploaded is not None:
                try:
                    text = uploaded.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = uploaded.read().decode("cp1256")
                if st.button("وارد کردن"):
                    res = news_import_csv(s, text)
                    st.success(
                        f"اضافه: {res['added']} | رد: {res['skipped']} | "
                        f"خطا: {len(res['errors'])}"
                    )

            st.divider()
            csv_text = st.text_area("یا متن CSV:", height=150)
            if st.button("وارد کردن از متن") and csv_text.strip():
                res = news_import_csv(s, csv_text)
                st.success(f"اضافه: {res['added']} | رد: {res['skipped']}")

        with tab_negative:
            neg = negative_symbols_today(s, days=3)
            if not neg:
                st.success("هیچ نمادی با خبر منفی در ۳ روز اخیر نیست ✅")
            else:
                st.warning(f"{len(neg)} نماد با خبر منفی:")
                st.dataframe(
                    [
                        {"نماد": x["symbol"], "تعداد": x["count"],
                         "بدترین احساس": x["worst_sentiment"],
                         "آخرین عنوان": x["latest_title"][:100]}
                        for x in neg
                    ],
                    width="stretch",
                )
    finally:
        s.close()


# ============================================================
# PAGE: 📈 قیمت و تحلیل
# ============================================================

elif page == "📈 قیمت و تحلیل":
    st.title("📈 قیمت و تحلیل")
    tab_prices, tab_rankings = st.tabs(["قیمت‌ها", "رتبه‌بندی"])

    with tab_prices:
        s = get_session()
        try:
            priced = s.execute(
                select(
                    Instrument.symbol, Instrument.name, Instrument.market,
                    func.count(PriceBar.id).label("n"),
                    func.max(PriceBar.ts).label("last_ts"),
                )
                .join(PriceBar, PriceBar.instrument_id == Instrument.id)
                .group_by(Instrument.id)
                .order_by(desc("n"))
                .limit(300)
            ).all()
            if not priced:
                st.warning("قیمتی ذخیره نشده.")
            else:
                st.caption(f"{len(priced)} نماد")
                st.dataframe(
                    [
                        {"نماد": sym, "نام": name, "بازار": mkt, "تعداد": n,
                         "آخرین تاریخ": last_ts.strftime("%Y-%m-%d") if last_ts else "-"}
                        for sym, name, mkt, n, last_ts in priced
                    ],
                    width="stretch",
                )
        finally:
            s.close()

    with tab_rankings:
        min_bars = st.number_input("حداقل رکورد", 30, 300, 60, 10, key="rk_min")
                # ---- Batch status banner ----
        try:
            _s_b = get_session()
            try:
                _meta = rankings_meta(_s_b)
            finally:
                _s_b.close()
        except Exception:
            _meta = None

        if _meta:
            age_txt = rankings_age_text(_meta["age_hours"])
            is_stale = _meta["age_hours"] > 24
            if is_stale:
                st.warning(
                    f"⚠️ رتبه‌بندی ذخیره‌شده {age_txt} "
                    f"({_meta['n_symbols']} نماد). برای بروزرسانی، "
                    f"دکمه «محاسبه رتبه‌بندی» را بزنید."
                )
            else:
                st.info(
                    f"ℹ️ رتبه‌بندی ذخیره‌شده {age_txt} "
                    f"({_meta['n_symbols']} نماد، حداقل {_meta['min_bars']} کندل). "
                    f"می‌توانید استفاده کنید یا دوباره محاسبه کنید."
                )

            _c1, _c2 = st.columns([4, 1])
            with _c2:
                if st.button("🗑 پاک کردن", key="rk_clear",
                             use_container_width=True):
                    try:
                        _s_cl = get_session()
                        try:
                            n = clear_rankings(_s_cl)
                        finally:
                            _s_cl.close()
                        st.session_state.pop("rankings", None)
                        st.session_state.pop("_rankings_from_db", None)
                        st.success(f"{n} ردیف پاک شد")
                        st.rerun()
                    except Exception as e:
                        st.error(f"خطا: {e}")
        min_bars = st.number_input("حداقل رکورد", 30, 300, 60, 10, key="rk_min")
        if st.button("محاسبه رتبه‌بندی", key="btn_rank"):
            s = get_session()
            try:
                rows = s.execute(text(f"""
                    SELECT i.symbol FROM instruments i
                    JOIN price_bars p ON p.instrument_id = i.id
                    WHERE i.is_active = 1
                    GROUP BY i.id HAVING COUNT(p.id) >= {int(min_bars)}
                    ORDER BY i.symbol
                """)).all()
                if not rows:
                    st.warning("نمادی واجد شرط نیست.")
                else:
                    progress = st.progress(0.0, text="تحلیل...")
                    results = []
                    for idx, (symbol,) in enumerate(rows, 1):
                        a = analyze_instrument(s, symbol)
                        if a.get("ok"):
                            fund = get_fundamental(s, symbol)
                            results.append(
                                compute_score(symbol, a, fundamental=fund)
                            )
                        progress.progress(
                            idx / len(rows), text=f"{idx}/{len(rows)} — {symbol}"
                        )
                    progress.empty()
                    results = rank_universe(results)
                    results.sort(key=lambda x: -x.total)
                    st.session_state["rankings"] = results
                    st.session_state.pop("daily_report", None)
                    st.session_state.pop("_rankings_from_db", None)

                    try:
                        _s_sv = get_session()
                        try:
                            _save_res = save_rankings(
                                _s_sv, results, min_bars=int(min_bars),
                            )
                        finally:
                            _s_sv.close()
                        if _save_res.get("ok"):
                            st.success(
                                f"✅ {_save_res['message']} — "
                                f"بار بعد خودکار بازیابی می‌شود."
                            )
                    except Exception as _e:
                        st.warning(f"⚠️ ذخیره نشد: {_e}")
            finally:
                s.close()
                            _s_sv.close()
                        if _save_res.get("ok"):
                            st.success(
                                f"✅ {_save_res['message']} — "
                                f"بار بعد خودکار بازیابی می‌شود."
                            )
                    except Exception as _e:
                        st.warning(f"⚠️ ذخیره نشد: {_e}")
            finally:
                s.close()

        results = st.session_state.get("rankings", [])
        if results:
            st.success(f"{len(results)} نماد تحلیل شد — امتیاز percentile")
            top_n = st.slider("نمایش", 5, min(50, len(results)), min(20, len(results)))
            for r in results[:top_n]:
                trend = {"up": "📈", "down": "📉", "neutral": "↔"}.get(
                    r.technical.get("trend"), "-"
                )
                with st.expander(f"**{r.symbol}** — {r.total:.1f} — {trend}"):
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("تکنیکال", f"{r.technical_score:.0f}")
                    m2.metric("بنیادی", f"{r.fundamental_score:.0f}")
                    m3.metric("مومنتوم", f"{r.momentum_score:.0f}")
                    m4.metric("ریسک", f"{r.risk_score:.0f}")
                    st.caption(
                        f"قیمت: {r.technical.get('last_close', 0):,.0f} — "
                        f"RSI: {r.technical.get('rsi14', 0):.1f} — "
                        f"ATR%: {(r.technical.get('atr_pct') or 0):.2f}%"
                    )
                    for x in r.reasons:
                        st.markdown(f"- {x}")
                    for x in r.red_flags:
                        st.markdown(f"⚠️ {x}")
        else:
            st.info("دکمه را بزنید.")


# ============================================================
# PAGE: 🏭 صنعت
# ============================================================
elif page == "🏭 صنعت":
    st.title("🏭 تحلیل صنعت")
    st.caption(
        "بر اساس نتایج رتبه‌بندی محاسبه‌شده. اگر صنعت نمادها خالی باشد، "
        "بر اساس بازار (TSE/IFB/ETF) گروه‌بندی می‌شود."
    )

    results = st.session_state.get("rankings", [])
    if not results:
        st.warning(
            "ابتدا در صفحه «قیمت و تحلیل» رتبه‌بندی را محاسبه کنید. "
            "تحلیل صنعت از همان نتایج استفاده می‌کند (سریع)."
        )
    else:
        s = get_session()
        try:
            ind_map = build_industry_map(s)
            stats = aggregate_by_industry(results, ind_map)

            c1, c2, c3 = st.columns(3)
            c1.metric("تعداد صنایع", len(stats))
            c2.metric("نمادهای تحلیل‌شده", len(results))
            strong_n = sum(1 for x in stats if x.avg_score >= 60)
            c3.metric("صنایع قوی (≥۶۰)", strong_n)

            tab_treemap, tab_summary, tab_detail = st.tabs(
                ["🗺️ نقشه صنعت", "📋 خلاصه صنایع", "🔍 جزئیات صنعت"]
            )

            with tab_treemap:
                from dashboard.components import render_industry_treemap_tab
                render_industry_treemap_tab()

            with tab_summary:
                st.subheader("رتبه‌بندی صنایع")
                st.dataframe(
                    [
                        {
                            "رتبه": i + 1, "صنعت": stt.industry,
                            "تعداد": stt.n_symbols,
                            "میانگین": stt.avg_score,
                            "RSI": stt.avg_rsi,
                            "٪ صعودی": f"{stt.pct_uptrend}%",
                            "≥۷۰": stt.score_70plus,
                            "برترین‌ها": ", ".join(
                                f"{sym}({sc})" for sym, sc in stt.top_symbols[:3]
                            ),
                        }
                        for i, stt in enumerate(stats)
                    ],
                    width="stretch",
                )

                if stats:
                    st.divider()
                    st.subheader("🟢 ۳ صنعت قوی")
                    for stt in stats[:3]:
                        with st.container(border=True):
                            st.markdown(f"### {stt.industry} — {stt.avg_score}")
                            st.caption(
                                f"{stt.n_symbols} نماد | RSI {stt.avg_rsi} | "
                                f"{stt.pct_uptrend}% صعودی | "
                                f"{stt.score_70plus} نماد ≥۷۰"
                            )
                            st.markdown("**برترین‌ها:** " + ", ".join(
                                f"{sym} ({sc})" for sym, sc in stt.top_symbols
                            ))

                    if len(stats) > 3:
                        st.divider()
                        st.subheader("🔴 ۳ صنعت ضعیف")
                        for stt in stats[-3:][::-1]:
                            with st.container(border=True):
                                st.markdown(f"### {stt.industry} — {stt.avg_score}")
                                st.caption(
                                    f"{stt.n_symbols} نماد | RSI {stt.avg_rsi} | "
                                    f"{stt.pct_uptrend}% صعودی"
                                )
                                st.markdown("**ضعیف‌ترین‌ها:** " + ", ".join(
                                    f"{sym} ({sc})" for sym, sc in stt.weak_symbols
                                ))

            with tab_detail:
                if stats:
                    selected = st.selectbox(
                        "صنعت", [x.industry for x in stats], key="ind_sel",
                    )
                    sel_stat = next(
                        (x for x in stats if x.industry == selected), None
                    )
                    if sel_stat:
                        st.subheader(f"صنعت: {sel_stat.industry}")
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("تعداد", sel_stat.n_symbols)
                        k2.metric("میانگین", sel_stat.avg_score)
                        k3.metric("RSI", sel_stat.avg_rsi)
                        k4.metric("٪ صعودی", f"{sel_stat.pct_uptrend}%")

                        st.divider()
                        symbols_in = [
                            r for r in results
                            if ind_map.get(r.symbol) == selected
                        ]
                        symbols_in.sort(key=lambda x: -x.total)
                        st.dataframe(
                            [
                                {
                                    "نماد": r.symbol,
                                    "امتیاز": round(r.total, 1),
                                    "تکنیکال": round(r.technical_score, 0),
                                    "بنیادی": round(r.fundamental_score, 0),
                                    "مومنتوم": round(r.momentum_score, 0),
                                    "ریسک": round(r.risk_score, 0),
                                    "روند": {"up": "📈", "down": "📉",
                                             "neutral": "↔"}.get(
                                        r.technical.get("trend"), "-"
                                    ),
                                    "RSI": round(
                                        r.technical.get("rsi14") or 0, 1
                                    ),
                                    "قیمت": round(
                                        r.technical.get("last_close") or 0, 0
                                    ),
                                }
                                for r in symbols_in
                            ],
                            width="stretch",
                        )
                else:
                    st.info("هیچ صنعتی شناسایی نشد.")
        finally:
            s.close()

# ============================================================
# PAGE: 🎯 سیگنال و هشدار
# ============================================================

elif page == "🎯 سیگنال و هشدار":
    st.title("🎯 سیگنال و هشدار")
    tab_visual, tab_signals, tab_alerts = st.tabs([
        "🎨 کارت‌های بصری", "📊 جدول کامل", "🔔 هشدارها",
    ])

    with tab_visual:
        from dashboard.components import render_visual_signals_tab
        render_visual_signals_tab(
            portfolio_value=float(portfolio_value),
            risk_pct=float(risk_pct),
            min_rr=float(min_rr),
        )

    with tab_signals:
        results = st.session_state.get("rankings", [])
        ms = st.session_state.get("market_state")
        if not results:
            st.warning("اول در صفحه «قیمت و تحلیل» رتبه‌بندی را محاسبه کنید.")
        else:
            if ms:
                st.caption(
                    f"وضعیت بازار: **{ms.state}** | "
                    f"حجم × {ms.size_multiplier} | "
                    f"آستانه +{ms.score_bonus:.1f}"
                )

            s_news = get_session()
            try:
                neg_news_map = {
                    x["symbol"]: x
                    for x in negative_symbols_today(s_news, days=3)
                }
            finally:
                s_news.close()

            if neg_news_map:
                st.info(
                    f"📰 {len(neg_news_map)} نماد خبر منفی اخیر دارند — "
                    f"سیگنالشان به «پیگیری» تغییر می‌کند."
                )

            top_for_signals = st.slider(
                "چند نماد برتر", 5, min(50, len(results)),
                min(15, len(results)), key="sg_n",
            )
            signals = [
                generate_signal(
                    r, portfolio_value=float(portfolio_value),
                    risk_per_trade_pct=float(risk_pct),
                    min_rr=float(min_rr),
                    market_state=ms,
                    recent_negative_news=(r.symbol in neg_news_map),
                )
                for r in results[:top_for_signals]
            ]
            buys = [x for x in signals if x.action == "BUY_CANDIDATE"]
            watches = [x for x in signals if x.action == "WATCH"]
            avoids = [x for x in signals if x.action == "AVOID"]
            c1, c2, c3 = st.columns(3)
            c1.metric("🟢 خرید", len(buys))
            c2.metric("🟡 پیگیری", len(watches))
            c3.metric("🔴 رد", len(avoids))
            if buys:
                for sig in buys:
                    with st.container(border=True):
                        st.markdown(f"### {sig.symbol} — {sig.score:.1f}")
                        n1, n2, n3, n4 = st.columns(4)
                        n1.metric(
                            "ورود",
                            f"{sig.entry_low:,.0f} – {sig.entry_high:,.0f}",
                        )
                        n2.metric("حد ضرر", f"{sig.stop_loss:,.0f}")
                        n3.metric(
                            "اهداف",
                            f"{sig.target1:,.0f} / {sig.target2:,.0f}",
                        )
                        n4.metric("R/R", f"{sig.rr_ratio:.2f}")
                        p1, p2, p3 = st.columns(3)
                        p1.metric("حجم", f"{sig.position_size:,}")
                        p2.metric("ریسک", f"{sig.risk_amount:,.0f}")
                        p3.metric("بازده", f"{sig.reward_amount:,.0f}")
            if watches:
                rows = []
                for s_ in watches:
                    d = {
                        "نماد": s_.symbol,
                        "امتیاز": round(s_.score, 1),
                        "دلیل": "; ".join(s_.invalidations),
                    }
                    if s_.symbol in neg_news_map:
                        d["📰 خبر"] = neg_news_map[s_.symbol][
                            "latest_title"
                        ][:50]
                    rows.append(d)
                st.dataframe(rows, width="stretch")
            if avoids:
                st.dataframe(
                    [
                        {
                            "نماد": s.symbol,
                            "امتیاز": round(s.score, 1),
                            "دلیل": "; ".join(s.invalidations),
                        }
                        for s in avoids
                    ],
                    width="stretch",
                )

    with tab_alerts:
        s = get_session()
        try:
            alerts = check_alerts(s)
            if not alerts:
                st.success("هیچ هشدار فعالی نیست ✅")
            else:
                for a in alerts:
                    icon = {
                        "critical": "🔴",
                        "warning": "🟠",
                        "info": "🔵",
                    }.get(a.level, "⚪")
                    st.markdown(f"{icon} **{a.message}**")
        finally:
            s.close()
# ============================================================
# PAGE: 💼 پرتفوی
# ============================================================

elif page == "💼 پرتفوی":
    st.title("💼 پرتفوی")
    tab_hold, tab_add = st.tabs(["موقعیت‌های فعلی", "ثبت معامله"])

    with tab_add:
        with st.form("add_tx", clear_on_submit=True):
            fc1, fc2, fc3, fc4, fc5 = st.columns(5)
            symbol_input = fc1.text_input("نماد").strip()
            side = fc2.selectbox("نوع", ["BUY", "SELL"])
            price = fc3.number_input("قیمت", 1, 100_000_000, 1000, 10)
            qty = fc4.number_input("تعداد", 1, 100_000_000, 1000, 100)
            fee = fc5.number_input("کارمزد", 0, 100_000_000, 0, 1000)
            note = st.text_input("یادداشت")
            submitted = st.form_submit_button("ثبت")

        if submitted and symbol_input:
            s = get_session()
            try:
                inst = s.scalar(
                    select(Instrument).where(Instrument.symbol == symbol_input)
                )
                if inst is None:
                    st.error(f"نماد «{symbol_input}» نیست.")
                else:
                    s.add(PortfolioTransaction(
                        instrument_id=inst.id, side=side,
                        price=float(price), quantity=float(qty),
                        fee=float(fee), note=note or None,
                    ))
                    s.commit()
                    log_event(
                        s, "trade", f"manual_{side}",
                        subject=symbol_input,
                        details=f"price={price} qty={qty} fee={fee}",
                    )
                    st.success("ثبت شد.")
                    st.rerun()
            finally:
                s.close()

    with tab_hold:
        s = get_session()
        try:
            holdings = compute_holdings(s)
            if not holdings:
                st.info("موقعیت بازی نیست.")
            else:
                tv = sum(h.market_value for h in holdings)
                tc = sum(h.cost_basis for h in holdings)
                tpl = tv - tc
                tpct = (tpl / tc * 100.0) if tc > 0 else 0.0
                k1, k2, k3 = st.columns(3)
                k1.metric("ارزش بازار", f"{tv:,.0f}")
                k2.metric("بهای تمام", f"{tc:,.0f}")
                k3.metric("سود/زیان", f"{tpl:,.0f}", delta=f"{tpct:.2f}%")
                st.dataframe(
                    [
                        {"نماد": h.symbol, "تعداد": f"{h.quantity:,.0f}",
                         "میانگین": f"{h.avg_cost:,.0f}",
                         "آخرین": f"{h.last_close:,.0f}" if h.last_close else "-",
                         "ارزش": f"{h.market_value:,.0f}",
                         "سود/زیان": f"{h.unrealized_pl:,.0f}",
                         "٪": f"{h.unrealized_pl_pct:.2f}%"}
                        for h in holdings
                    ],
                    width="stretch",
                )
        finally:
            s.close()


# ============================================================
# PAGE: 📊 بنیادی
# ============================================================

elif page == "📊 بنیادی":
    fundamental_render(adapter_choice)


# ============================================================
# PAGE: 📝 Paper Trading
# ============================================================

elif page == "📝 Paper Trading":
    st.title("📝 Paper Trading")
    s = get_session()
    try:
        st.subheader("آمار")
        ps = paper_stats(s)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("بسته", ps["total"])
        k2.metric("نرخ برد", f"{ps['win_rate']}%")
        k3.metric("میانگین R", f"{ps['avg_r']:.3f}")
        k4.metric("PF", f"{ps['profit_factor']:.2f}")
        st.metric("باز", ps["open"])

        st.divider()
        tab_scan, tab_update, tab_history = st.tabs(
            ["اسکن", "به‌روزرسانی", "تاریخچه"]
        )

        with tab_scan:
            pc1, pc2, pc3, pc4 = st.columns(4)
            scan_soft = pc1.number_input("حداقل فیلتر", 3, 6, 5, 1, key="pp_soft")
            scan_atr = pc2.number_input("ATR×", 1.0, 4.0, 2.0, 0.5, key="pp_atr")
            scan_rr = pc3.number_input("R/R", 1.0, 4.0, 2.0, 0.5, key="pp_rr")
            scan_min = pc4.number_input("حداقل رکورد", 60, 250, 80, 10, key="pp_min")
            use_breakout = st.checkbox("همچنین Breakout", value=False)
            if st.button("🔍 اسکن امروز"):
                strategies = ["v2b"] + (["breakout"] if use_breakout else [])
                prog = st.progress(0.0, text="شروع...")

                def _pcb(i, n, sym):
                    prog.progress(i / max(n, 1), text=f"{i}/{n} — {sym}")

                res = paper_scan(
                    s, strategies=strategies, min_soft=int(scan_soft),
                    atr_mult=float(scan_atr), rr_ratio=float(scan_rr),
                    min_bars=int(scan_min), progress_cb=_pcb,
                )
                prog.empty()
                st.success(
                    f"اسکن: {res['scanned']} | جدید: {res['new']} | "
                    f"تکراری: {res['existing']}"
                )

        with tab_update:
            exp = st.number_input("انقضا (روز)", 5, 90, 30, 5, key="pp_exp")
            if st.button("🔄 بررسی سیگنال‌ها"):
                c = update_open_signals(s, expire_days=int(exp))
                log_event(
                    s, "system", "paper_update",
                    details=f"target={c['closed_target']} stop={c['closed_stop']} "
                            f"expired={c['closed_expired']} open={c['still_open']}",
                )
                st.success(
                    f"هدف: {c['closed_target']} | ضرر: {c['closed_stop']} | "
                    f"منقضی: {c['closed_expired']} | باز: {c['still_open']}"
                )

        with tab_history:
            opens = s.scalars(
                select(PaperSignal).where(PaperSignal.status == "open")
                .order_by(PaperSignal.signal_ts.desc())
            ).all()
            st.markdown("**سیگنال‌های باز**")
            if opens:
                st.dataframe(
                    [
                        {"نماد": x.symbol, "استراتژی": x.strategy,
                         "زمان": x.signal_ts.strftime("%Y-%m-%d %H:%M"),
                         "ورود": f"{x.entry_price:,.0f}",
                         "حد ضرر": f"{x.stop_loss:,.0f}",
                         "هدف": f"{x.target:,.0f}"}
                        for x in opens
                    ],
                    width="stretch",
                )
            else:
                st.info("سیگنال بازی نیست.")

            st.markdown("**معاملات بسته**")
            closed = s.scalars(
                select(PaperSignal)
                .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
                .order_by(PaperSignal.signal_ts.desc()).limit(200)
            ).all()
            if closed:
                st.dataframe(
                    [
                        {"نماد": x.symbol, "استراتژی": x.strategy,
                         "زمان": x.signal_ts.strftime("%Y-%m-%d"),
                         "ورود": f"{x.entry_price:,.0f}",
                         "خروج": f"{x.exit_price:,.0f}" if x.exit_price else "-",
                         "وضعیت": {"target_hit": "🎯", "stop_hit": "🛑",
                                  "expired": "⏰"}.get(x.status, x.status),
                         "R": f"{x.r_multiple:+.2f}" if x.r_multiple is not None else "-"}
                        for x in closed
                    ],
                    width="stretch",
                )
            else:
                st.info("معامله بسته‌ای نیست.")
    finally:
        s.close()


# ============================================================
# PAGE: 📊 گزارش عملکرد
# ============================================================

elif page == "📊 گزارش عملکرد":
    st.title("📊 گزارش عملکرد")
    st.caption("گزارش کامل از Paper Signals بسته‌شده.")
    s = get_session()
    try:
        if st.button("🔄 ساخت گزارش", key="perf_build"):
            st.session_state["perf"] = build_report(s)

        rpt = st.session_state.get("perf")
        if not rpt:
            st.info("دکمه ساخت گزارش را بزنید.")
        else:
            o = rpt.overall
            st.subheader("خلاصه کل")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("معاملات بسته", o.total)
            k2.metric("نرخ برد", f"{o.win_rate}%")
            k3.metric("میانگین R", f"{o.avg_r:.3f}")
            k4.metric("PF", f"{o.profit_factor:.2f}")
            k5, k6, k7 = st.columns(3)
            k5.metric("برد/باخت", f"{o.wins}/{o.losses}")
            k6.metric("مجموع R", f"{o.total_r:+.2f}")
            k7.metric("حداکثر افت", f"{o.max_dd_r}R")
            st.metric("سیگنال‌های باز", rpt.open_count)

            if o.total > 0:
                if o.avg_r > 0.2 and o.win_rate > 35:
                    st.success(f"✅ edge وجود دارد: avgR={o.avg_r:.3f}")
                elif o.avg_r > 0:
                    st.warning(f"⚠️ مثبت ولی ضعیف: avgR={o.avg_r:.3f}")
                else:
                    st.error(f"🔴 edge ندارد: avgR={o.avg_r:.3f}")

            if rpt.by_strategy:
                st.divider()
                st.subheader("عملکرد هر استراتژی")
                st.dataframe(
                    [
                        {"استراتژی": b.label, "معاملات": b.total,
                         "نرخ برد": f"{b.win_rate}%",
                         "میانگین R": b.avg_r,
                         "PF": b.profit_factor,
                         "افت": f"{b.max_dd_r}R"}
                        for b in sorted(
                            rpt.by_strategy.values(), key=lambda x: -x.avg_r
                        )
                    ],
                    width="stretch",
                )

            if rpt.by_symbol:
                st.divider()
                st.subheader("بهترین و بدترین نمادها (≥۲ معامله)")
                qualified = [
                    b for b in sorted(rpt.by_symbol.values(), key=lambda x: -x.avg_r)
                    if b.total >= 2
                ]
                if qualified:
                    st.markdown("**بهترین‌ها:**")
                    st.dataframe(
                        [
                            {"نماد": b.label, "معاملات": b.total,
                             "نرخ برد": f"{b.win_rate}%",
                             "میانگین R": b.avg_r}
                            for b in qualified[:5]
                        ],
                        width="stretch",
                    )
                    st.markdown("**بدترین‌ها:**")
                    st.dataframe(
                        [
                            {"نماد": b.label, "معاملات": b.total,
                             "نرخ برد": f"{b.win_rate}%",
                             "میانگین R": b.avg_r}
                            for b in qualified[-5:]
                        ],
                        width="stretch",
                    )

            if rpt.by_month:
                st.divider()
                st.subheader("روند ماهانه")
                st.dataframe(
                    [
                        {"ماه": b.label, "معاملات": b.total,
                         "نرخ برد": f"{b.win_rate}%",
                         "میانگین R": b.avg_r}
                        for b in sorted(
                            rpt.by_month.values(), key=lambda x: x.label,
                        )
                    ],
                    width="stretch",
                )
    finally:
        s.close()


# ============================================================
# PAGE: 🧪 بک‌تست
# ============================================================

elif page == "🧪 بک‌تست":
    st.title("🧪 بک‌تست")
    tab_bt, tab_gbt, tab_cmp, tab_wf = st.tabs(
        ["تک‌نماد", "گروهی", "مقایسه", "Walk-Forward"]
    )

    with tab_bt:
        s = get_session()
        try:
            max_b = max_bars_available(s)
            st.caption(f"حداکثر رکورد: {max_b}")
            if max_b < 62:
                st.error("داده کافی نیست.")
            else:
                symbols = [
                    r[0] for r in s.execute(text("""
                        SELECT i.symbol FROM instruments i
                        JOIN price_bars p ON p.instrument_id = i.id
                        WHERE i.is_active = 1
                        GROUP BY i.id HAVING COUNT(p.id) >= 62
                        ORDER BY i.symbol LIMIT 200
                    """)).all()
                ]
                if not symbols:
                    st.warning("نمادی واجد شرط نیست.")
                else:
                    bc1, bc2, bc3, bc4 = st.columns(4)
                    bt_symbol = bc1.selectbox("نماد", symbols, key="bt_sym")
                    bt_min = bc2.number_input("حداقل رکورد", 55, 250, 60, 5, key="bt_min")
                    bt_atr = bc3.number_input("ATR×", 1.0, 4.0, 2.0, 0.5, key="bt_atr")
                    bt_rr = bc4.number_input("R/R", 1.0, 4.0, 2.0, 0.5, key="bt_rr")
                    bt_thr = st.number_input("آستانه", 40.0, 90.0, 60.0, 5.0, key="bt_thr")
                    if st.button("اجرا"):
                        inst_id = s.scalar(
                            select(Instrument.id).where(Instrument.symbol == bt_symbol)
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
                        res = backtest_symbol(
                            bt_symbol, df, threshold=bt_thr,
                            atr_mult=bt_atr, rr_ratio=bt_rr, min_bars=int(bt_min),
                        )
                        if not res.ok:
                            st.error(res.message)
                        else:
                            m = compute_metrics(res.trades)
                            k1, k2, k3, k4 = st.columns(4)
                            k1.metric("معاملات", m["total"])
                            k2.metric("نرخ برد", f"{m['win_rate']}%")
                            k3.metric("میانگین R", m["avg_r"])
                            k4.metric("حداکثر افت", f"{m['max_dd_r']}R")
        finally:
            s.close()

    with tab_gbt:
        s = get_session()
        try:
            eligible = pick_top_eligible_symbols(s, min_bars=62, limit=200)
            if not eligible:
                st.warning("نمادی واجد شرط نیست.")
            else:
                gc1, gc2, gc3, gc4 = st.columns(4)
                n_sym = gc1.number_input(
                    "تعداد نماد", 1, min(100, len(eligible)),
                    min(20, len(eligible)), 5, key="gb_n",
                )
                gb_thr = gc2.number_input("آستانه", 40.0, 90.0, 60.0, 5.0, key="gb_thr")
                gb_atr = gc3.number_input("ATR×", 1.0, 4.0, 2.0, 0.5, key="gb_atr")
                gb_rr = gc4.number_input("R/R", 1.0, 4.0, 2.0, 0.5, key="gb_rr")
                if st.button("اجرای گروهی"):
                    subset = eligible[: int(n_sym)]
                    bar = st.progress(0.0, text="شروع...")

                    def _cb(i, n, sym):
                        bar.progress(i / max(n, 1), text=f"{i}/{n} — {sym}")

                    r = run_group_backtest(
                        s, subset, threshold=gb_thr,
                        atr_mult=gb_atr, rr_ratio=gb_rr, progress_cb=_cb,
                    )
                    bar.empty()
                    st.session_state["group_bt_result"] = r

            r = st.session_state.get("group_bt_result")
            if r and r.total_trades > 0:
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("نرخ برد", f"{r.win_rate}%")
                k2.metric("میانگین R", f"{r.avg_r:.2f}")
                k3.metric("PF", f"{r.profit_factor:.2f}")
                k4.metric("حداکثر افت", f"{r.max_dd_r}R")
        finally:
            s.close()

    with tab_cmp:
        s = get_session()
        try:
            eligible = pick_top_eligible_symbols(s, min_bars=62, limit=50)
            if not eligible:
                st.warning("نمادی واجد شرط نیست.")
            else:
                st.warning("⚠️ Grid search overfit می‌کند. از Walk-Forward استفاده کنید.")
                nc1, nc2 = st.columns(2)
                cmp_n = nc1.number_input(
                    "تعداد نماد", 1, min(30, len(eligible)),
                    min(10, len(eligible)), 1, key="cmp_n",
                )
                min_trades = nc2.number_input(
                    "حداقل معامله", 1, 50, 5, 1, key="cmp_min",
                )
                if st.button("اجرای ۲۷ ترکیب"):
                    subset = eligible[: int(cmp_n)]
                    bar = st.progress(0.0, text="شروع...")

                    def _cb(i, n, label):
                        bar.progress(i / max(n, 1), text=f"{i}/{n} — {label}")

                    cmp = run_grid_search(
                        s, subset,
                        thresholds=[55.0, 65.0, 75.0],
                        atr_mults=[1.5, 2.0, 2.5],
                        rr_ratios=[1.5, 2.0, 2.5],
                        min_bars=60, progress_cb=_cb,
                    )
                    bar.empty()
                    st.session_state["comparison"] = cmp
                    st.session_state["cmp_min_trades"] = int(min_trades)

            cmp = st.session_state.get("comparison")
            if cmp and cmp.combos:
                ranked = rank_combos(
                    cmp, min_trades=st.session_state.get("cmp_min_trades", 5)
                )
                positive = [c for c in ranked if c.result.avg_r > 0]
                m1, m2 = st.columns(2)
                m1.metric("مثبت", f"{len(positive)} / {len(ranked)}")
                m2.metric("منفی", len(ranked) - len(positive))
                if positive:
                    st.session_state["top_combos"] = [
                        (c.threshold, c.atr_mult, c.rr_ratio) for c in positive[:5]
                    ]
                st.dataframe(
                    [
                        {"رتبه": i + 1, "آستانه": c.threshold,
                         "ATR×": c.atr_mult, "R/R": c.rr_ratio,
                         "معاملات": c.result.total_trades,
                         "نرخ برد": f"{c.result.win_rate}%",
                         "میانگین R": c.result.avg_r,
                         "PF": c.result.profit_factor,
                         "وضعیت": "🟢" if c.result.avg_r > 0 else "🔴"}
                        for i, c in enumerate(ranked)
                    ],
                    width="stretch",
                )
        finally:
            s.close()

    with tab_wf:
        s = get_session()
        try:
            eligible = pick_top_eligible_symbols(s, min_bars=120, limit=50)
            if not eligible:
                st.warning("نمادی با ≥۱۲۰ رکورد نیست.")
            else:
                top_combos = st.session_state.get(
                    "top_combos",
                    [(55.0, 2.5, 2.5), (65.0, 2.5, 2.5), (75.0, 2.5, 2.5)],
                )
                wf_n = st.number_input(
                    "تعداد نماد", 1, min(30, len(eligible)),
                    min(10, len(eligible)), 1, key="wf_n",
                )
                if st.button("اجرای Walk-Forward"):
                    subset = eligible[: int(wf_n)]
                    bar = st.progress(0.0, text="شروع...")

                    def _cb(i, n, label):
                        bar.progress(i / max(n, 1), text=f"{i}/{n} — {label}")

                    wf_results = run_walk_forward_top(
                        s, subset, top_combos, progress_cb=_cb,
                    )
                    bar.empty()
                    st.session_state["wf_results"] = wf_results

            wf = st.session_state.get("wf_results")
            if wf:
                st.dataframe(
                    [
                        {"ترکیب": f"thr={r.threshold},atr={r.atr_mult},rr={r.rr_ratio}",
                         "TRAIN": r.train_trades, "TR WR": f"{r.train_win_rate}%",
                         "TR avgR": r.train_avg_r,
                         "TEST": r.test_trades, "TE WR": f"{r.test_win_rate}%",
                         "TE avgR": r.test_avg_r, "افت": r.degradation,
                         "حکم": {"robust": "🟢", "fragile": "🟡",
                                 "overfit": "🔴", "unknown": "⚪"}.get(r.verdict, "⚪")}
                        for r in wf
                    ],
                    width="stretch",
                )
        finally:
            s.close()


# ============================================================
# PAGE: 🔬 استراتژی‌ها
# ============================================================

elif page == "🔬 استراتژی‌ها":
    st.title("🔬 استراتژی‌ها")
    tab_v2, tab_v2b, tab_bo, tab_diag = st.tabs(
        ["v2 (pullback سخت)", "v2b (pullback نرم)", "Breakout", "تشخیص فیلترها"]
    )

    with tab_v2:
        s = get_session()
        try:
            symbols = [
                r[0] for r in s.execute(text("""
                    SELECT i.symbol FROM instruments i
                    JOIN price_bars p ON p.instrument_id = i.id
                    WHERE i.is_active = 1
                    GROUP BY i.id HAVING COUNT(p.id) >= 62
                    ORDER BY i.symbol LIMIT 200
                """)).all()
            ]
            if not symbols:
                st.warning("نمادی واجد شرط نیست.")
            else:
                vc1, vc2, vc3 = st.columns(3)
                v2_sym = vc1.selectbox("نماد", symbols, key="v2_sym")
                v2_atr = vc2.number_input("ATR×", 1.5, 4.0, 2.5, 0.5, key="v2_atr")
                v2_rr = vc3.number_input("R/R", 1.5, 4.0, 2.5, 0.5, key="v2_rr")
                v2_thr = st.number_input("آستانه", 40.0, 90.0, 55.0, 5.0, key="v2_thr")
                if st.button("اجرای v2"):
                    inst_id = s.scalar(
                        select(Instrument.id).where(Instrument.symbol == v2_sym)
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
                    res = backtest_v2(
                        v2_sym, df, threshold=v2_thr,
                        atr_mult=v2_atr, rr_ratio=v2_rr,
                    )
                    if not res.ok:
                        st.error(res.message)
                    else:
                        m = compute_v2_metrics(res.trades)
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("معاملات", m["total"])
                        k2.metric("نرخ برد", f"{m['win_rate']}%")
                        k3.metric("میانگین R", m["avg_r"])
                        k4.metric("حداکثر افت", f"{m['max_dd_r']}R")
        finally:
            s.close()

    with tab_v2b:
        s = get_session()
        try:
            eligible = [
                r[0] for r in s.execute(text("""
                    SELECT i.symbol FROM instruments i
                    JOIN price_bars p ON p.instrument_id = i.id
                    WHERE i.is_active = 1
                    GROUP BY i.id HAVING COUNT(p.id) >= 80
                    ORDER BY i.symbol LIMIT 200
                """)).all()
            ]
            st.info(f"نمادهای واجد شرط (≥۸۰): **{len(eligible)}**")
            if not eligible:
                st.warning("نمادی نیست.")
            else:
                vc1, vc2, vc3, vc4 = st.columns(4)
                v2b_soft = vc1.number_input("حداقل فیلتر", 3, 6, 5, 1, key="v2b_soft")
                v2b_atr = vc2.number_input("ATR×", 1.0, 4.0, 2.0, 0.5, key="v2b_atr")
                v2b_rr = vc3.number_input("R/R", 1.0, 4.0, 2.0, 0.5, key="v2b_rr")
                v2b_n = vc4.number_input(
                    "تعداد نماد", 1, min(50, len(eligible)),
                    min(10, len(eligible)), 1, key="v2b_n",
                )
                if st.button("اجرای v2b گروهی"):
                    subset = eligible[: int(v2b_n)]
                    bar = st.progress(0.0, text="شروع...")

                    def _cb(i, n, sym):
                        bar.progress(i / max(n, 1), text=f"{i}/{n} — {sym}")

                    r = run_v2_group(
                        s, subset, min_soft=int(v2b_soft),
                        atr_mult=v2b_atr, rr_ratio=v2b_rr, progress_cb=_cb,
                    )
                    bar.empty()
                    st.session_state["v2b_group"] = r

            r = st.session_state.get("v2b_group")
            if r:
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("معاملات", r.total_trades)
                k2.metric("نرخ برد", f"{r.win_rate}%")
                k3.metric("میانگین R", f"{r.avg_r:.3f}")
                k4.metric("PF", f"{r.profit_factor:.2f}")
                if r.total_trades > 0 and r.per_symbol:
                    st.dataframe(
                        [
                            {"نماد": sym, "معاملات": m["total"],
                             "نرخ برد": f"{m['win_rate']}%",
                             "میانگین R": m["avg_r"]}
                            for sym, m in sorted(
                                r.per_symbol.items(), key=lambda kv: -kv[1]["avg_r"]
                            )
                        ],
                        width="stretch",
                    )
        finally:
            s.close()

    with tab_bo:
        s = get_session()
        try:
            symbols = [
                r[0] for r in s.execute(text("""
                    SELECT i.symbol FROM instruments i
                    JOIN price_bars p ON p.instrument_id = i.id
                    WHERE i.is_active = 1
                    GROUP BY i.id HAVING COUNT(p.id) >= 80
                    ORDER BY i.symbol LIMIT 200
                """)).all()
            ]
            if not symbols:
                st.warning("نمادی واجد شرط نیست.")
            else:
                bc1, bc2, bc3, bc4 = st.columns(4)
                bo_sym = bc1.selectbox("نماد", symbols, key="bo_sym")
                bo_lb = bc2.number_input("دوره سقف", 20, 100, 40, 5, key="bo_lb")
                bo_atr = bc3.number_input("ATR×", 1.0, 3.0, 1.5, 0.5, key="bo_atr")
                bo_rr = bc4.number_input("R/R", 1.5, 4.0, 2.0, 0.5, key="bo_rr")
                if st.button("اجرای Breakout"):
                    inst_id = s.scalar(
                        select(Instrument.id).where(Instrument.symbol == bo_sym)
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
                    res = backtest_breakout(
                        bo_sym, df, lookback=int(bo_lb),
                        atr_mult=bo_atr, rr_ratio=bo_rr,
                    )
                    if not res.ok:
                        st.error(res.message)
                    else:
                        m = compute_bo_metrics(res.trades)
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("معاملات", m["total"])
                        k2.metric("نرخ برد", f"{m['win_rate']}%")
                        k3.metric("میانگین R", m["avg_r"])
                        k4.metric("حداکثر افت", f"{m['max_dd_r']}R")
        finally:
            s.close()

    with tab_diag:
        st.caption("نرخ عبور هر فیلتر v2 برای یک نماد.")
        s = get_session()
        try:
            symbols = [
                r[0] for r in s.execute(text("""
                    SELECT i.symbol FROM instruments i
                    JOIN price_bars p ON p.instrument_id = i.id
                    WHERE i.is_active = 1
                    GROUP BY i.id HAVING COUNT(p.id) >= 80
                    ORDER BY i.symbol LIMIT 100
                """)).all()
            ]
            if symbols:
                dg_sym = st.selectbox("نماد", symbols, key="dg_sym")
                if st.button("تشخیص"):
                    import subprocess
                    r = subprocess.run(
                        [sys.executable, "-m", "scripts.diagnose_v2_filters", dg_sym],
                        capture_output=True, text=True,
                    )
                    st.code(r.stdout or r.stderr, language="text")
        finally:
            s.close()


# ============================================================
# PAGE: 🤖 دستیار
# ============================================================
elif page == "🌍 کلان":
    macro_render(adapter_choice)


elif page == "📥 Codal":
    codal_render(adapter_choice)


elif page == "🤖 دستیار":
    from dashboard.pages.assistant import render as assistant_render
    try:
        assistant_render(adapter_choice)
    except Exception as e:
        import traceback
        st.error(f"❌ خطا در بارگذاری صفحه دستیار: {type(e).__name__}: {e}")
        with st.expander("جزئیات خطا"):
            st.code(traceback.format_exc())


elif page == "📊 WF خودکار":
    auto_wf_render(adapter_choice)


elif page == "🛠️ سیستم":
    system_render(adapter_choice)


# ============================================================
# PAGE: ⚙️ داده
# ============================================================

elif page == "⚙️ داده":
    st.title("⚙️ مدیریت داده")

    # ==========================================
    # Order Flow sync section
    # ==========================================
    st.subheader("📊 تابلوخوانی (Order Flow)")
    st.caption(
        "دریافت داده حقیقی/حقوقی و قدرت خریدار از TSETMC. "
        "هر نماد یک API call — برای ۳۰ نماد حدود ۱ دقیقه."
    )
    ofc1, ofc2 = st.columns(2)
    of_n = ofc1.number_input(
        "تعداد نماد", 1, 200, 30, 5, key="of_n",
    )
    if ofc2.button("📥 دریافت order-flow", key="of_run"):
        with st.spinner("در حال دریافت از TSETMC..."):
            from analysis.order_flow import bulk_sync
            from database.session import get_session as _gs
            from database.models import PriceBar as _PB

            s2 = _gs()
            try:
                sub_ids = (
                    select(_PB.instrument_id)
                    .distinct()
                    .subquery()
                )
                symbols_with_prices = [
                    r[0] for r in s2.execute(
                        select(Instrument.symbol)
                        .where(Instrument.id.in_(
                            select(sub_ids.c.instrument_id)
                        ))
                        .order_by(Instrument.symbol)
                        .limit(int(of_n))
                    ).all()
                ]

                if not symbols_with_prices:
                    st.warning(
                        "هیچ نمادی با قیمت ثبت‌شده وجود ندارد. اول sync قیمت کنید."
                    )
                else:
                    prog = st.progress(0.0, text="شروع...")

                    def _cb(i, total, sym):
                        prog.progress(
                            i / max(total, 1),
                            text=f"{i}/{total} — {sym}",
                        )

                    result = bulk_sync(
                        s2,
                        symbols=symbols_with_prices,
                        days=30,
                        progress_cb=_cb,
                    )
                    prog.empty()
                    st.success(
                        f"✅ {result['ok']} موفق، "
                        f"❌ {result['fail']} ناموفق از "
                        f"{result['total']} نماد"
                    )
                    if result["errors"]:
                        with st.expander(f"خطاها ({len(result['errors'])})"):
                            for e in result["errors"][:20]:
                                st.markdown(f"- {e}")
            finally:
                s2.close()

    st.divider()

    # ==========================================
    # Failed symbols (existing behavior)
    # ==========================================
    st.subheader("⚠️ نمادهای بدون قیمت")
    s = get_session()
    try:
        sub = (
            select(PriceBar.instrument_id)
            .group_by(PriceBar.instrument_id)
            .subquery()
        )
        failed = s.scalars(
            select(Instrument)
            .where(Instrument.is_active.is_(True))
            .where(Instrument.id.notin_(select(sub.c.instrument_id)))
            .order_by(Instrument.symbol)
        ).all()

        if not failed:
            st.success("همه نمادها قیمت دارند ✅")
        else:
            st.warning(f"{len(failed)} نماد بدون قیمت")
            st.dataframe(
                [{"نماد": i.symbol, "نام": i.name} for i in failed],
                width="stretch",
            )
            if st.button(f"تلاش مجدد ({len(failed)} نماد)", key="retry_failed"):
                adapter = _adapter(adapter_choice)
                progress = st.progress(0.0, text="شروع...")
                ok_n = fail_n = 0
                for idx, inst in enumerate(failed, 1):
                    r = sync_prices(adapter, s, inst.symbol, days=200)
                    if r.ok:
                        ok_n += 1
                    else:
                        fail_n += 1
                    progress.progress(
                        idx / len(failed),
                        text=f"{idx}/{len(failed)} — {inst.symbol}",
                    )
                    progress.empty()
                st.success(f"{ok_n} موفق، {fail_n} ناموفق")
                st.rerun()
    finally:
        s.close()