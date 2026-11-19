"""
Comprehensive system health check + real-money readiness.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from database.models import (
    AuditLog, DataSourceStatus, Fundamental, Instrument,
    MacroIndicator, OrderFlowDaily, PaperSignal, PriceBar,
)


@dataclass
class HealthItem:
    name: str
    status: str       # "ok" | "warn" | "fail" | "unknown"
    value: str
    detail: str = ""
    action: str = ""


@dataclass
class ReadinessItem:
    name: str
    status: str       # "pass" | "warn" | "fail"
    value: str
    threshold: str
    action: str


@dataclass
class HealthReport:
    items: list[HealthItem] = field(default_factory=list)
    readiness: list[ReadinessItem] = field(default_factory=list)
    overall_status: str = "unknown"
    ready_for_real_money: bool = False
    computed_at: datetime = field(default_factory=datetime.now)


def _check_db(session: Session) -> HealthItem:
    db_path = BASE_DIR / "data" / "market.db"
    if not db_path.exists():
        return HealthItem(
            "فایل دیتابیس", "fail", "موجود نیست",
            "اول init_database را اجرا کنید",
        )
    size_mb = db_path.stat().st_size / (1024 * 1024)
    status = "ok" if size_mb > 0.1 else "warn"
    return HealthItem(
        "فایل دیتابیس", status, f"{size_mb:.1f} MB",
        f"مسیر: {db_path}",
    )


def _check_instruments(session: Session) -> HealthItem:
    total = session.scalar(select(func.count()).select_from(Instrument)) or 0
    active = session.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    if active == 0:
        return HealthItem(
            "نمادها", "fail", "0 فعال",
            "داده‌ای وجود ندارد",
            "دکمه «همگام‌سازی نمادها» در سایدبار",
        )
    if active < 100:
        return HealthItem(
            "نمادها", "warn", f"{active} فعال از {total}",
            "تعداد کم — شاید سینک ناقص است",
        )
    return HealthItem("نمادها", "ok", f"{active} فعال از {total}")


def _check_price_coverage(session: Session) -> HealthItem:
    active = session.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    priced = session.scalar(
        select(func.count(func.distinct(PriceBar.instrument_id)))
    ) or 0
    if active == 0:
        return HealthItem("پوشش قیمت", "fail", "0%", "نمادی فعال نیست")
    pct = priced / active * 100
    if pct >= 70:
        status = "ok"
    elif pct >= 40:
        status = "warn"
    else:
        status = "fail"
    return HealthItem(
        "پوشش قیمت", status, f"{pct:.1f}% ({priced}/{active})",
        action="دکمه «سینک قیمت» در سایدبار"
        if pct < 70 else "",
    )


def _check_bar_count(session: Session) -> HealthItem:
    max_bars = session.scalar(
        select(func.count(PriceBar.id))
        .group_by(PriceBar.instrument_id)
        .order_by(func.count(PriceBar.id).desc())
        .limit(1)
    ) or 0
    if max_bars >= 200:
        return HealthItem("حداکثر کندل", "ok", f"{max_bars}",
                          "کافی برای همه استراتژی‌ها")
    if max_bars >= 120:
        return HealthItem("حداکثر کندل", "warn", f"{max_bars}",
                          "v2c به ۱۵۲ نیاز دارد — backfill کنید")
    if max_bars >= 62:
        return HealthItem("حداکثر کندل", "warn", f"{max_bars}",
                          "فقط برای v1 کافی است — backfill_history را اجرا کنید")
    return HealthItem(
        "حداکثر کندل", "fail", f"{max_bars}",
        action="python -m scripts.backfill_history 400",
    )


def _check_data_freshness(session: Session) -> HealthItem:
    latest_ts = session.scalar(select(func.max(PriceBar.ts)))
    if latest_ts is None:
        return HealthItem("تازگی قیمت", "fail", "هیچ", "قیمتی ذخیره نشده")
    days = (datetime.now() - latest_ts).days
    if days <= 2:
        return HealthItem("تازگی قیمت", "ok", f"{days} روز پیش")
    if days <= 7:
        return HealthItem("تازگی قیمت", "warn", f"{days} روز پیش",
                          "سینک کنید")
    return HealthItem(
        "تازگی قیمت", "fail", f"{days} روز پیش",
        action="دکمه «سینک قیمت» در سایدبار",
    )


def _check_fundamental_coverage(session: Session) -> HealthItem:
    active = session.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    with_fund = session.scalar(
        select(func.count(func.distinct(Fundamental.instrument_id)))
    ) or 0
    if active == 0:
        return HealthItem("پوشش بنیادی", "fail", "0", "نمادی فعال نیست")
    pct = with_fund / active * 100
    if pct >= 50:
        return HealthItem("پوشش بنیادی", "ok", f"{pct:.1f}%")
    if pct >= 10:
        return HealthItem(
            "پوشش بنیادی", "warn", f"{pct:.1f}%",
            "برای دقت بیشتر از Codal یا CSV اضافه کنید",
        )
    return HealthItem(
        "پوشش بنیادی", "warn", f"{pct:.1f}%",
        action="صفحه «📊 بنیادی» یا «📥 Codal»",
    )


def _check_order_flow(session: Session) -> HealthItem:
    count = session.scalar(
        select(func.count()).select_from(OrderFlowDaily)
    ) or 0
    if count == 0:
        return HealthItem(
            "تابلوخوانی", "warn", "0",
            "داده order-flow دریافت نشده",
            "صفحه «⚙️ داده» → «دریافت order-flow»",
        )
    if count < 100:
        return HealthItem("تابلوخوانی", "warn", f"{count} ردیف",
                          "داده کم — گسترش دهید")
    return HealthItem("تابلوخوانی", "ok", f"{count} ردیف")


def _check_macro(session: Session) -> HealthItem:
    count = session.scalar(
        select(func.count()).select_from(MacroIndicator)
    ) or 0
    if count == 0:
        return HealthItem(
            "داده کلان", "warn", "0",
            "برای بریفینگ دقیق‌تر، نرخ ارز و تورم وارد کنید",
            "صفحه «🌍 کلان»",
        )
    return HealthItem("داده کلان", "ok", f"{count} ردیف")


def _check_datasources(session: Session) -> list[HealthItem]:
    items = []
    sources = session.scalars(select(DataSourceStatus)).all()
    if not sources:
        return [
            HealthItem(
                "منابع داده", "warn", "0",
                "هنوز هیچ منبعی وضعیت ثبت نکرده",
                "از سایدبار «تست اتصال» بزنید",
            )
        ]
    for src in sources:
        if src.is_healthy:
            items.append(HealthItem(
                f"منبع: {src.source_name}", "ok", "سالم",
                f"آخرین: {src.last_success_at.strftime('%Y-%m-%d %H:%M')}"
                if src.last_success_at else "",
            ))
        else:
            items.append(HealthItem(
                f"منبع: {src.source_name}", "fail", "خطا",
                (src.last_error or "")[:100],
            ))
    return items


def _check_paper(session: Session) -> HealthItem:
    total = session.scalar(select(func.count()).select_from(PaperSignal)) or 0
    closed = session.scalar(
        select(func.count()).select_from(PaperSignal)
        .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
    ) or 0
    if total == 0:
        return HealthItem(
            "Paper Trading", "warn", "0",
            "هنوز هیچ سیگنالی ثبت نشده",
            "صفحه «📝 Paper Trading» → «اسکن امروز»",
        )
    if closed < 20:
        return HealthItem(
            "Paper Trading", "warn", f"{closed} بسته از {total}",
            f"برای آمادگی پول واقعی به ≥۲۰ بسته نیاز دارید",
        )
    return HealthItem("Paper Trading", "ok",
                      f"{closed} بسته از {total}")


def _check_audit(session: Session) -> HealthItem:
    recent = session.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.ts >= datetime.now() - timedelta(days=1))
    ) or 0
    if recent == 0:
        return HealthItem(
            "لاگ ممیزی", "warn", "0 در ۲۴ ساعت",
            "شاید سیستم استفاده نشده یا لاگ خراب است",
        )
    return HealthItem("لاگ ممیزی", "ok", f"{recent} رخداد در ۲۴ ساعت")


# ===========================================================================
# Real-money readiness
# ===========================================================================

def _compute_readiness(session: Session) -> list[ReadinessItem]:
    items = []

    # 1. Paper trades
    closed = session.scalar(
        select(func.count()).select_from(PaperSignal)
        .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
    ) or 0
    if closed >= 30:
        s1 = "pass"
    elif closed >= 20:
        s1 = "warn"
    else:
        s1 = "fail"
    items.append(ReadinessItem(
        "معاملات Paper بسته",
        s1, f"{closed}", "≥ ۲۰ (ایده‌آل ۳۰)",
        "به Paper Trading ادامه دهید",
    ))

    # 2. avgR
    closed_signals = session.scalars(
        select(PaperSignal)
        .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
        .where(PaperSignal.r_multiple.isnot(None))
    ).all()
    avg_r = 0.0
    if closed_signals:
        avg_r = sum(s.r_multiple or 0 for s in closed_signals) / len(closed_signals)
    if avg_r >= 0.2:
        s2 = "pass"
    elif avg_r > 0:
        s2 = "warn"
    else:
        s2 = "fail"
    items.append(ReadinessItem(
        "میانگین R", s2, f"{avg_r:.3f}", "≥ +0.20",
        "استراتژی را بازبینی کنید" if avg_r < 0.2 else "",
    ))

    # 3. Win rate
    wins = sum(1 for s in closed_signals if (s.r_multiple or 0) > 0)
    wr = (wins / len(closed_signals) * 100) if closed_signals else 0.0
    if wr >= 35:
        s3 = "pass"
    elif wr >= 25:
        s3 = "warn"
    else:
        s3 = "fail"
    items.append(ReadinessItem(
        "نرخ برد", s3, f"{wr:.1f}%", "≥ ۳۵٪",
        "استراتژی را بازبینی کنید" if wr < 35 else "",
    ))

    # 4. Profit factor
    gross_win = sum(s.r_multiple for s in closed_signals
                    if (s.r_multiple or 0) > 0)
    gross_loss = abs(sum(s.r_multiple for s in closed_signals
                        if (s.r_multiple or 0) <= 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else 0.0
    if pf >= 1.2:
        s4 = "pass"
    elif pf >= 1.0:
        s4 = "warn"
    else:
        s4 = "fail"
    items.append(ReadinessItem(
        "Profit Factor", s4, f"{pf:.2f}", "≥ 1.2",
        "استراتژی edge ندارد" if pf < 1.2 else "",
    ))

    # 5. Walk-forward robust
    # (We check if any walk_forward test was run recently via audit_log)
    from database.models import AuditLog
    wf_run = session.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action.like("%walk_forward%"))
    ) or 0
    items.append(ReadinessItem(
        "Walk-Forward اجرا شده",
        "pass" if wf_run > 0 else "warn",
        f"{wf_run} بار" if wf_run > 0 else "هرگز",
        "≥ ۱ اجرا",
        "صفحه «🧪 بک‌تست» → Walk-Forward",
    ))

    # 6. Data coverage
    active = session.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    priced = session.scalar(
        select(func.count(func.distinct(PriceBar.instrument_id)))
    ) or 0
    coverage = (priced / active * 100) if active > 0 else 0
    if coverage >= 70:
        s6 = "pass"
    elif coverage >= 40:
        s6 = "warn"
    else:
        s6 = "fail"
    items.append(ReadinessItem(
        "پوشش داده قیمت", s6, f"{coverage:.1f}%", "≥ ۷۰٪",
        "سینک قیمت" if coverage < 70 else "",
    ))

    # 7. Data freshness
    latest_ts = session.scalar(select(func.max(PriceBar.ts)))
    days_old = (datetime.now() - latest_ts).days if latest_ts else 999
    if days_old <= 2:
        s7 = "pass"
    elif days_old <= 7:
        s7 = "warn"
    else:
        s7 = "fail"
    items.append(ReadinessItem(
        "تازگی داده", s7, f"{days_old} روز پیش", "≤ ۲ روز",
        "سینک قیمت" if days_old > 2 else "",
    ))

    # 8. Backup exists
    from scripts.backup_db import list_backups
    backups = list_backups()
    db_backups = [b for b in backups if b["kind"] == "db"]
    if len(db_backups) >= 1:
        s8 = "pass"
    else:
        s8 = "fail"
    items.append(ReadinessItem(
        "پشتیبان وجود دارد", s8,
        f"{len(db_backups)} نسخه", "≥ ۱",
        "از صفحه «🛠️ سیستم» پشتیبان بگیرید",
    ))

    # 9. Data history (max bars)
    max_bars = session.scalar(
        select(func.count(PriceBar.id))
        .group_by(PriceBar.instrument_id)
        .order_by(func.count(PriceBar.id).desc())
        .limit(1)
    ) or 0
    if max_bars >= 200:
        s9 = "pass"
    elif max_bars >= 62:
        s9 = "warn"
    else:
        s9 = "fail"
    items.append(ReadinessItem(
        "عمق تاریخچه", s9, f"{max_bars} کندل", "≥ ۲۰۰",
        "backfill_history" if max_bars < 200 else "",
    ))

    # 10. Portfolio tracking
    from database.models import PortfolioTransaction
    tx_count = session.scalar(
        select(func.count()).select_from(PortfolioTransaction)
    ) or 0
    items.append(ReadinessItem(
        "ثبت معاملات (تمرینی)",
        "pass" if tx_count > 0 else "warn",
        f"{tx_count} معامله", "≥ ۱ (تمرین)",
        "چند معامله فرضی ثبت کنید" if tx_count == 0 else "",
    ))

    return items


def compute_health(session: Session) -> HealthReport:
    rpt = HealthReport()

    rpt.items.append(_check_db(session))
    rpt.items.append(_check_instruments(session))
    rpt.items.append(_check_price_coverage(session))
    rpt.items.append(_check_bar_count(session))
    rpt.items.append(_check_data_freshness(session))
    rpt.items.append(_check_fundamental_coverage(session))
    rpt.items.append(_check_order_flow(session))
    rpt.items.append(_check_macro(session))
    rpt.items.extend(_check_datasources(session))
    rpt.items.append(_check_paper(session))
    rpt.items.append(_check_audit(session))

    rpt.readiness = _compute_readiness(session)

    # Overall
    if any(i.status == "fail" for i in rpt.items):
        rpt.overall_status = "fail"
    elif any(i.status == "warn" for i in rpt.items):
        rpt.overall_status = "warn"
    else:
        rpt.overall_status = "ok"

    # Ready for real money: all pass
    rpt.ready_for_real_money = all(
        r.status == "pass" for r in rpt.readiness
    )

    return rpt