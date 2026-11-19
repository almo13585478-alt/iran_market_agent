"""
Daily briefing generator with risk, benchmark, and data-freshness sections.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.market_state import classify_market
from alerts.engine import check_alerts
from news.query import negative_symbols_today, recent_news
from portfolio.holdings import compute_holdings
from portfolio.risk_advisor import compute_risk_report
from paper_trading.tracker import paper_stats
from macro.query import latest_all as macro_latest_all, pct_change as macro_pct
from reports.benchmark_comparison import compute_comparison
from database.models import Fundamental, Instrument, PriceBar


@dataclass
class Briefing:
    generated_at: datetime = field(default_factory=datetime.now)
    summary_text: str = ""
    action_items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    market: dict = field(default_factory=dict)
    alerts: dict = field(default_factory=dict)
    portfolio: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    benchmark: dict = field(default_factory=dict)
    paper: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
    macro: dict = field(default_factory=dict)
    data_health: dict = field(default_factory=dict)
    flow_highlights: list[dict] = field(default_factory=list)


def _check_data_freshness(session: Session) -> dict:
    """Check staleness of prices and fundamentals for held symbols."""
    from portfolio.holdings import compute_holdings as _ch
    holdings = _ch(session)
    symbols = [h.symbol for h in holdings]

    if not symbols:
        return {"stale_prices": [], "stale_fundamentals": [], "message": ""}

    now = datetime.now()
    stale_prices = []
    stale_fund = []

    for sym in symbols:
        inst = session.scalar(
            select(Instrument).where(Instrument.symbol == sym)
        )
        if not inst:
            continue
        latest_price = session.scalar(
            select(PriceBar.ts)
            .where(PriceBar.instrument_id == inst.id)
            .order_by(PriceBar.ts.desc())
            .limit(1)
        )
        if latest_price is None:
            stale_prices.append((sym, "هیچ"))
        elif (now - latest_price).days > 3:
            stale_prices.append((sym, f"{latest_price.strftime('%Y-%m-%d')}"))

        latest_fund = session.scalar(
            select(Fundamental.as_of_date)
            .where(Fundamental.symbol == sym)
            .order_by(Fundamental.as_of_date.desc())
            .limit(1)
        )
        if latest_fund is None:
            stale_fund.append((sym, "هیچ"))
        elif (now - latest_fund).days > 120:
            stale_fund.append((sym, f"{latest_fund.strftime('%Y-%m-%d')}"))

    return {
        "stale_prices": stale_prices[:5],
        "stale_fundamentals": stale_fund[:5],
        "message": (
            f"{len(stale_prices)} نماد قیمت قدیمی، "
            f"{len(stale_fund)} نماد بنیادی قدیمی"
        ) if (stale_prices or stale_fund) else "",
    }


def generate_briefing(session: Session) -> Briefing:
    b = Briefing()

    # --- Market ---
    try:
        ms = classify_market(session)
        b.market = {
            "state": ms.state, "score": ms.score,
            "breadth_today": ms.breadth_today,
            "trend_breadth": ms.trend_breadth,
            "vol_ratio": ms.vol_ratio,
            "size_multiplier": ms.size_multiplier,
            "score_bonus": ms.score_bonus,
            "reasons": ms.reasons,
        }
    except Exception:
        b.market = {"state": "unknown", "reasons": ["داده بازار نیست"]}

    # --- Alerts ---
    try:
        alerts = check_alerts(session)
        b.alerts = {
            "critical": sum(1 for a in alerts if a.level == "critical"),
            "warning": sum(1 for a in alerts if a.level == "warning"),
            "info": sum(1 for a in alerts if a.level == "info"),
            "top": [
                {"level": a.level, "message": a.message}
                for a in alerts[:3]
            ],
        }
    except Exception:
        b.alerts = {"critical": 0, "warning": 0, "info": 0, "top": []}

    # --- Portfolio + Risk + Benchmark ---
    try:
        holdings = compute_holdings(session)
        if holdings:
            tv = sum(h.market_value for h in holdings)
            tc = sum(h.cost_basis for h in holdings)
            pl_pct = ((tv - tc) / tc * 100) if tc > 0 else 0
            b.portfolio = {
                "n_positions": len(holdings),
                "value": tv, "cost": tc,
                "pl_pct": round(pl_pct, 2),
            }
        else:
            b.portfolio = {"n_positions": 0, "value": 0, "cost": 0, "pl_pct": 0}
    except Exception:
        b.portfolio = {"n_positions": 0}

    try:
        risk = compute_risk_report(session, b.portfolio.get("value", 0))
        b.risk = {
            "hhi": risk.hhi,
            "top_position_pct": risk.top_position_pct,
            "top_industry_pct": risk.top_industry_pct,
            "daily_var_pct_worst": risk.daily_var_pct_worst,
            "daily_var_95_worst": risk.daily_var_95_worst,
            "current_open_risk_pct": risk.current_open_risk_pct,
            "remaining_risk_budget_pct": risk.remaining_risk_budget_pct,
            "max_new_position_value": risk.max_new_position_value,
            "n_positions": risk.n_positions,
            "warnings": risk.warnings,
            "actions": risk.actions,
            "summary": risk.summary,
        }
    except Exception:
        b.risk = {"summary": "محاسبه ریسک ممکن نیست", "warnings": [], "actions": []}

    try:
        bm = compute_comparison(session)
        b.benchmark = {
            "portfolio_return_pct": bm.portfolio_return_pct,
            "index_return_pct": bm.index_return_pct,
            "alpha_pct": bm.alpha_pct,
            "days_tracked": bm.days_tracked,
            "verdict": bm.verdict,
            "summary": bm.summary,
        }
    except Exception:
        b.benchmark = {"summary": "مقایسه با شاخص ممکن نیست"}

    # --- Paper ---
    try:
        ps = paper_stats(session)
        b.paper = {
            "open": ps["open"], "closed": ps["total"],
            "win_rate": ps["win_rate"], "avg_r": ps["avg_r"],
            "profit_factor": ps["profit_factor"],
        }
    except Exception:
        b.paper = {"open": 0, "closed": 0}

    # --- News ---
    try:
        neg = negative_symbols_today(session, days=3)
        recent = recent_news(session, limit=3)
        b.news = {
            "negative_count": len(neg),
            "negative_top": [
                {"symbol": n["symbol"], "worst": n["worst_sentiment"],
                 "title": n["latest_title"][:80]}
                for n in neg[:3]
            ],
            "recent_titles": [
                {"title": n.title[:80], "sentiment": n.sentiment_score,
                 "published": n.published_at.strftime("%Y-%m-%d")}
                for n in recent
            ],
        }
    except Exception:
        b.news = {"negative_count": 0}

    # --- Macro ---
    try:
        macro = macro_latest_all(session)
        usd_pct = macro_pct(session, "usd_free", days=30)
        b.macro = {"items": macro, "usd_30d_pct": usd_pct}
    except Exception:
        b.macro = {"items": {}, "usd_30d_pct": None}

    # --- Data health ---
    try:
        b.data_health = _check_data_freshness(session)
    except Exception:
        b.data_health = {"stale_prices": [], "stale_fundamentals": [], "message": ""}

    # --- Order flow highlights ---
    try:
        from database.models import OrderFlowDaily
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = session.scalars(
            select(OrderFlowDaily)
            .where(OrderFlowDaily.ts >= today)
            .order_by(OrderFlowDaily.buy_inst_vol.desc())
            .limit(20)
        ).all()
        b.flow_highlights = [
            {
                "symbol": r.symbol,
                "inst_net": r.buy_inst_vol - r.sell_inst_vol,
                "buy_power": round(
                    (r.buy_inst_vol + r.buy_retail_vol) /
                    max(1.0, r.sell_inst_vol + r.sell_retail_vol),
                    2,
                ),
            }
            for r in rows[:5]
        ]
    except Exception:
        b.flow_highlights = []

    _build_text(b)
    return b


def _build_text(b: Briefing) -> None:
    state = b.market.get("state", "unknown")
    state_fa = {"green": "سبز", "yellow": "زرد", "red": "قرمز"}.get(state, "نامشخص")

    parts: list[str] = []
    actions: list[str] = []
    warns: list[str] = []

    # Market
    if state == "green":
        parts.append(f"بازار امروز **سبز** است (امتیاز {b.market.get('score', 0):.0f}).")
    elif state == "red":
        parts.append(f"بازار امروز **قرمز** است (امتیاز {b.market.get('score', 0):.0f}).")
    else:
        parts.append(f"بازار امروز **{state_fa}** است (امتیاز {b.market.get('score', 0):.0f}).")

    if b.market.get("breadth_today", 0) > 0:
        parts.append(
            f"عرض {b.market.get('breadth_today')}%، "
            f"{b.market.get('trend_breadth')}% بالای EMA50."
        )

    # Alerts
    if b.alerts["critical"] > 0:
        parts.append(f"🔴 **{b.alerts['critical']} هشدار بحرانی** فعال.")
        actions.append(f"بررسی فوری {b.alerts['critical']} هشدار بحرانی")

    # Portfolio risk
    if b.portfolio.get("n_positions", 0) > 0:
        risk_summary = b.risk.get("summary", "")
        if risk_summary:
            parts.append(risk_summary)

        # Risk warnings
        for w in b.risk.get("warnings", [])[:2]:
            warns.append(w)

        # Benchmark
        bm_summary = b.benchmark.get("summary", "")
        if bm_summary and b.benchmark.get("alpha_pct") is not None:
            parts.append(bm_summary)

        # Remaining budget as action
        remaining = b.risk.get("remaining_risk_budget_pct", 0)
        max_new = b.risk.get("max_new_position_value", 0)
        if remaining >= 0.5:
            actions.append(
                f"بودجه ریسک باقیمانده: {remaining}% — "
                f"پوزیشن جدید تا {max_new:,.0f} ریال ممکن است"
            )
        else:
            actions.append("بودجه ریسک پر است — پوزیشن جدید باز نکنید")

    # Paper
    if b.paper.get("closed", 0) > 0:
        parts.append(
            f"Paper: {b.paper['closed']} بسته، "
            f"نرخ برد {b.paper['win_rate']}%، "
            f"میانگین R {b.paper['avg_r']:.2f}."
        )
        if b.paper["avg_r"] < 0:
            warns.append("Paper در زیان — استراتژی را بازبینی کنید.")
    if b.paper.get("open", 0) > 0:
        actions.append(f"بررسی {b.paper['open']} سیگنال باز Paper")

    # News
    if b.news.get("negative_count", 0) > 0:
        symbols = ", ".join(n["symbol"] for n in b.news["negative_top"])
        parts.append(
            f"📰 {b.news['negative_count']} نماد خبر منفی ({symbols})."
        )
        actions.append(f"بررسی اخبار منفی {b.news['negative_count']} نماد")

    # Macro
    if b.macro.get("usd_30d_pct") is not None:
        pct = b.macro["usd_30d_pct"]
        if abs(pct) >= 3:
            direction = "افزایش" if pct > 0 else "کاهش"
            parts.append(f"💵 دلار در ۳۰ روز {pct:+.1f}% ({direction}).")
            if pct > 5:
                actions.append("دلار رشد داشته — سهام صادراتی بررسی شود")

    # Data health
    dh = b.data_health
    if dh.get("stale_prices"):
        symbols = ", ".join(s for s, _ in dh["stale_prices"][:3])
        warns.append(
            f"قیمت {symbols} قدیمی است — sync کنید"
        )
    if dh.get("stale_fundamentals"):
        symbols = ", ".join(s for s, _ in dh["stale_fundamentals"][:3])
        warns.append(
            f"بنیادی {symbols} قدیمی است — به‌روزرسانی از Codal/CSV"
        )

    # Flow
    if b.flow_highlights:
        top = b.flow_highlights[0]
        parts.append(
            f"🌊 تابلوخوانی: «{top['symbol']}» بیشترین خرید حقوقی خالص "
            f"({top['inst_net']:+,.0f} سهم)."
        )

    # Market recommendation
    if state == "red":
        actions.append("بازار قرمز: حجم پوزیشن‌های جدید را نصف یا نقد بمانید")
    elif state == "green":
        actions.append("بازار سبز: ورود با حجم عادی مساعد است")
    else:
        actions.append("بازار زرد: با احتیاط و حجم کمتر وارد شوید")

    if not actions:
        actions.append("امروز شرایط خاصی نیست — صبر کنید")

    b.summary_text = " ".join(parts)
    b.action_items = actions
    b.warnings = warns


def enhance_with_llm(briefing: Briefing, session: Session) -> str:
    """Optional: rewrite briefing in conversational tone."""
    import json
    import os
    import httpx

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return briefing.summary_text

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    context = {
        "market": briefing.market,
        "alerts": briefing.alerts,
        "risk": briefing.risk,
        "benchmark": briefing.benchmark,
        "paper": briefing.paper,
        "portfolio": briefing.portfolio,
        "news": briefing.news,
        "macro": briefing.macro,
        "data_health": briefing.data_health,
        "summary": briefing.summary_text,
        "actions": briefing.action_items,
    }

    try:
        r = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "تو یک تحلیلگر بورس ایران هستی. بر اساس داده زیر، "
                            "یک بریفینگ صبحگاهی ۴-۶ جمله‌ای بنویس. "
                            "فقط از داده داده‌شده استفاده کن، هیچ پیش‌بینی "
                            "قطعی نکن، و اگر داده ناکافی است صریح بگو."
                        ),
                    },
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                "temperature": 0.3,
                "max_tokens": 500,
            },
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return briefing.summary_text