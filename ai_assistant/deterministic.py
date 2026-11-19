"""
Deterministic (rule-based) assistant. No API key. No LLM. Always works.

Routes questions by keyword to specialized handlers that query the DB.
Returns {answer: str, kind: str, sources: list[str], data: dict}.
"""

from datetime import datetime, timedelta

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from database.models import (
    Instrument, PriceBar, PaperSignal, PortfolioTransaction, MarketSnapshot,
)
from analysis.technical import analyze_instrument
from analysis.market_state import classify_market
from decision_engine.scoring import compute_score, rank_universe
from portfolio.holdings import compute_holdings
from paper_trading.tracker import paper_stats
from news.query import recent_news, negative_symbols_today
from fundamental.query import get_latest, coverage


def _help_text() -> str:
    return (
        "می‌توانم به این سؤالات پاسخ دهم:\n"
        "- «وضعیت بازار» → طبقه‌بندی بازار (سبز/زرد/قرمز)\n"
        "- «بهترین نمادها» یا «بهترین سهام‌ها» → ۵ نماد برتر\n"
        "- «فولاد» یا «خودرو» → تحلیل یک نماد\n"
        "- «پرتفوی» یا «موقعیت‌ها» → خلاصه پرتفوی\n"
        "- «Paper» یا «پپر» → آمار paper trading\n"
        "- «اخبار» → اخبار منفی اخیر\n"
        "- «استراتژی» → توضیح v2b و breakout\n"
        "- «داده» یا «پوشش» → وضعیت داده و بنیادی"
    )


def _handle_market(session: Session) -> dict:
    ms = classify_market(session)
    src = []
    snap = session.scalar(
        select(MarketSnapshot).order_by(MarketSnapshot.trade_date.desc()).limit(1)
    )
    if snap:
        src.append(f"MarketSnapshot {snap.trade_date.date()}")
    return {
        "answer": (
            f"وضعیت بازار: **{ms.state}** (امتیاز {ms.score}/100)\n"
            f"- عرض امروز: {ms.breadth_today}%\n"
            f"- بالای EMA50: {ms.trend_breadth}%\n"
            f"- نسبت حجم: {ms.vol_ratio}×\n"
            f"- نوسان عرضی: {ms.vol_cross}%\n"
            f"- ضریب حجم پیشنهادی: {ms.size_multiplier}×\n"
            f"- آستانه اضافه: +{ms.score_bonus}\n"
            f"- دلایل: " + "; ".join(ms.reasons)
        ),
        "kind": "market",
        "sources": src,
        "data": {
            "state": ms.state, "score": ms.score,
            "breadth_today": ms.breadth_today,
            "trend_breadth": ms.trend_breadth,
            "size_multiplier": ms.size_multiplier,
        },
    }


def _handle_top(session: Session) -> dict:
    rows = session.execute(
        select(Instrument.symbol)
        .join(PriceBar, PriceBar.instrument_id == Instrument.id)
        .where(Instrument.is_active.is_(True))
        .group_by(Instrument.id)
        .having(func.count(PriceBar.id) >= 60)
    ).all()
    if not rows:
        return {"answer": "داده کافی برای تحلیل نیست.", "kind": "top", "sources": [], "data": {}}

    scores = []
    for (sym,) in rows:
        a = analyze_instrument(session, sym)
        if a.get("ok"):
            scores.append(compute_score(sym, a))

    if not scores:
        return {"answer": "هیچ نمادی تحلیل نشد.", "kind": "top", "sources": [], "data": {}}

    ranked = sorted(rank_universe(scores), key=lambda x: -x.total)
    top5 = ranked[:5]
    lines = ["۵ نماد برتر (بر اساس percentile):"]
    for i, s_ in enumerate(top5, 1):
        tech = s_.technical or {}
        lines.append(
            f"{i}. {s_.symbol} — امتیاز {s_.total:.1f} | "
            f"روند {tech.get('trend', '-')} | RSI {tech.get('rsi14', 0):.0f} | "
            f"قیمت {tech.get('last_close', 0):,.0f}"
        )
    return {
        "answer": "\n".join(lines),
        "kind": "top",
        "sources": [f"{len(ranked)} نماد تحلیل شد"],
        "data": {"symbols": [x.symbol for x in top5]},
    }


def _handle_symbol(session: Session, sym: str) -> dict:
    inst = session.scalar(select(Instrument).where(Instrument.symbol == sym))
    if inst is None:
        return {"answer": f"نماد «{sym}» در دیتابیس نیست.", "kind": "symbol",
                "sources": [], "data": {}}

    a = analyze_instrument(session, sym)
    if not a.get("ok"):
        return {"answer": f"{sym}: {a.get('message', 'تحلیل ناموفق')}",
                "kind": "symbol", "sources": [], "data": {}}

    fund = get_latest(session, sym)
    sc = compute_score(sym, a, fundamental=fund)
    tech = sc.technical

    lines = [
        f"### {sym} — {sc.total:.1f}/100",
        f"- روند: {tech.get('trend', '-')}",
        f"- قیمت: {tech.get('last_close', 0):,.0f}",
        f"- RSI: {tech.get('rsi14', 0):.1f}",
        f"- ATR%: {(tech.get('atr_pct') or 0):.2f}%",
        f"- تکنیکال: {sc.technical_score:.0f} | مومنتوم: {sc.momentum_score:.0f} | "
        f"ریسک: {sc.risk_score:.0f} | نقدشوندگی: {sc.liquidity_score:.0f}",
    ]
    if fund:
        lines.append(
            f"- بنیادی: {sc.fundamental_score:.0f} "
            f"(P/E={fund.get('pe')}, ROE={fund.get('roe')}, "
            f"رشد سود={fund.get('profit_growth')})"
        )
    else:
        lines.append("- بنیادی: داده‌ای ثبت نشده (پیش‌فرض ۵۰)")

    if tech.get("support") and tech.get("resistance"):
        lines.append(f"- حمایت: {tech['support']:,.0f} | مقاومت: {tech['resistance']:,.0f}")

    if sc.reasons:
        lines.append("- سیگنال‌ها: " + "; ".join(sc.reasons[:3]))
    if sc.red_flags:
        lines.append("- ⚠️ " + "; ".join(sc.red_flags))

    neg = negative_symbols_today(session, days=3)
    if any(n["symbol"] == sym for n in neg):
        lines.append("- 📰 خبر منفی اخیر دارد")

    return {
        "answer": "\n".join(lines),
        "kind": "symbol",
        "sources": [f"تحلیل تکنیکال + {len(inst.prices) if hasattr(inst,'prices') else 'N/A'} bars"] +
                    (["بنیادی از DB"] if fund else []),
        "data": {"symbol": sym, "total": sc.total},
    }


def _handle_portfolio(session: Session) -> dict:
    holdings = compute_holdings(session)
    if not holdings:
        return {"answer": "هیچ موقعیت بازی ثبت نشده.", "kind": "portfolio",
                "sources": [], "data": {}}
    tv = sum(h.market_value for h in holdings)
    tc = sum(h.cost_basis for h in holdings)
    pl = tv - tc
    pl_pct = (pl / tc * 100.0) if tc > 0 else 0.0
    lines = [
        f"**پرتفوی:** {len(holdings)} موقعیت",
        f"- ارزش: {tv:,.0f} ریال",
        f"- بهای تمام: {tc:,.0f}",
        f"- سود/زیان: {pl:,.0f} ({pl_pct:+.2f}%)",
        "",
    ]
    for h in holdings[:10]:
        lines.append(
            f"- {h.symbol}: {h.quantity:,.0f} سهم | "
            f"میانگین {h.avg_cost:,.0f} | آخرین {h.last_close:,.0f} | "
            f"{h.unrealized_pl_pct:+.2f}%"
        )
    return {"answer": "\n".join(lines), "kind": "portfolio",
            "sources": ["PortfolioTransaction"], "data": {"n": len(holdings)}}


def _handle_paper(session: Session) -> dict:
    ps = paper_stats(session)
    return {
        "answer": (
            f"**Paper Trading:**\n"
            f"- بسته: {ps['total']} | باز: {ps['open']}\n"
            f"- نرخ برد: {ps['win_rate']}%\n"
            f"- میانگین R: {ps['avg_r']:.3f}\n"
            f"- Profit Factor: {ps['profit_factor']:.2f}\n\n"
            + (
                "✅ edge دارد (avgR > 0.2)" if ps['avg_r'] > 0.2 else
                "⚠️ ضعیف (avgR 0-0.2)" if ps['avg_r'] > 0 else
                "🔴 edge ندارد"
            )
        ),
        "kind": "paper",
        "sources": ["paper_signals"],
        "data": ps,
    }


def _handle_news(session: Session) -> dict:
    neg = negative_symbols_today(session, days=3)
    recent = recent_news(session, limit=10)
    lines = ["**اخبار اخیر:**"]
    if neg:
        lines.append(f"\nنمادهای با خبر منفی (۳ روز):")
        for n in neg[:10]:
            lines.append(f"- {n['symbol']}: {n['count']} خبر، احساس {n['worst_sentiment']:.2f}")
    else:
        lines.append("\n- هیچ خبر منفی اخیر برای نمادی ثبت نشده")
    if recent:
        lines.append(f"\nآخرین اخبار:")
        for x in recent[:5]:
            lines.append(f"- [{x.published_at.strftime('%m-%d')}] {x.title[:80]}")
    return {"answer": "\n".join(lines), "kind": "news",
            "sources": ["news_items"], "data": {"negative": len(neg)}}


def _handle_strategy() -> dict:
    return {
        "answer": (
            "**استراتژی v2b:** pullback نرم‌شده به EMA20 در روند صعودی.\n"
            "- ۶ فیلتر امتیازی: نزدیکی EMA20، ADX≥15، حجم≥0.6×MA20، "
            "RSI 30-70، MACD مثبت، امتیاز ترکیبی ≥50\n"
            "- حداقل ۵ از ۶ باید پاس شود\n"
            "- حد ضرر: 2×ATR زیر ورود\n"
            "- هدف: 2×ریسک (R/R=2)\n\n"
            "**استراتژی Breakout:** شکست سقف ۴۰ کندل اخیر با حجم ≥1.5×MA20 "
            "و RSI 50-75.\n\n"
            "**محدودیت‌ها:** هیچ سفارش واقعی ارسال نمی‌شود. "
            "حجم بر اساس ریسک محاسبه می‌شود."
        ),
        "kind": "strategy",
        "sources": ["master prompt §6"],
        "data": {},
    }


def _handle_data(session: Session) -> dict:
    cov = coverage(session)
    n_active = session.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    n_priced = session.scalar(
        select(func.count(func.distinct(PriceBar.instrument_id)))
    ) or 0
    return {
        "answer": (
            f"**وضعیت داده:**\n"
            f"- نماد فعال: {n_active}\n"
            f"- دارای قیمت: {n_priced} ({n_priced/n_active*100:.1f}%)\n"
            f"- دارای بنیادی: {cov['with_fundamental']} ({cov['pct_covered']}%)\n"
            f"- نمادهای بی‌قیمت: {n_active - n_priced}"
        ),
        "kind": "data",
        "sources": ["instruments", "price_bars", "fundamentals"],
        "data": cov,
    }


def answer(session: Session, question: str) -> dict:
    """Main entry: classify question, route to handler."""
    q = (question or "").strip()
    if not q:
        return {"answer": _help_text(), "kind": "help", "sources": [], "data": {}}

    ql = q.lower()

    if any(k in q for k in ["کمک", "راهنما", "help"]):
        return {"answer": _help_text(), "kind": "help", "sources": [], "data": {}}

    if any(k in q for k in ["وضعیت بازار", "بازار سبز", "بازار قرمز", "بازار زرد"]):
        return _handle_market(session)

    if any(k in q for k in ["بهترین نماد", "بهترین سهام", "برترین", "top"]):
        return _handle_top(session)

    if any(k in q for k in ["پرتفوی", "موقعیت", "سبد من"]):
        return _handle_portfolio(session)

    if any(k in ql for k in ["paper", "پپر", "کاغذی"]):
        return _handle_paper(session)

    if any(k in q for k in ["اخبار", "خبر"]):
        return _handle_news(session)

    if any(k in q for k in ["استراتژی", "strategy", "v2b", "breakout"]):
        return _handle_strategy()

    if any(k in q for k in ["داده", "پوشش", "coverage"]):
        return _handle_data(session)

    # try symbol match
    symbols = [r[0] for r in session.execute(
        select(Instrument.symbol).where(Instrument.is_active.is_(True))
    ).all()]
    for sym in symbols:
        if sym and sym in q:
            return _handle_symbol(session, sym)

    return {
        "answer": "سؤال را متوجه نشدم.\n\n" + _help_text(),
        "kind": "fallback", "sources": [], "data": {},
    }