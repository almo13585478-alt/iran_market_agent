"""
Background scheduler with 7 jobs:

1. sync_prices        — daily 17:30
2. paper_scan         — daily 18:00
3. paper_tracker      — daily 18:15
4. codal_monitor      — daily 20:00
5. auto_walk_forward  — Friday 18:00
6. weekly_report      — Friday 19:00
7. backup             — Friday 20:00
"""

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from data_engine.sync_engine import sync_all_prices
from data_sources.tsetmc import TsetmcAdapter
from database.session import get_session
from paper_trading.scanner import scan
from paper_trading.tracker import update_open_signals
from fundamental.codal_monitor import check_all_active
from reports.weekly_report import build_weekly_report, save_weekly_report


_scheduler: BackgroundScheduler | None = None


# ===========================================================================
# Job implementations
# ===========================================================================

def _job_sync():
    logger.info("Scheduler[sync]: start")
    s = get_session()
    try:
        r = sync_all_prices(
            TsetmcAdapter(), s, days=10,
            max_instruments=200, pause_seconds=2.0,
        )
        logger.info("Scheduler[sync]: {}", r.message)
    except Exception:
        logger.exception("Scheduler[sync] failed")
    finally:
        s.close()


def _job_scan():
    logger.info("Scheduler[scan]: start")
    s = get_session()
    try:
        r = scan(s, strategies=["v2b", "breakout"], min_soft=5)
        logger.info(
            "Scheduler[scan]: new={} existing={}",
            r["new"], r["existing"],
        )
    except Exception:
        logger.exception("Scheduler[scan] failed")
    finally:
        s.close()


def _job_tracker():
    logger.info("Scheduler[tracker]: start")
    s = get_session()
    try:
        c = update_open_signals(s)
        logger.info("Scheduler[tracker]: {}", c)
    except Exception:
        logger.exception("Scheduler[tracker] failed")
    finally:
        s.close()


def _job_codal():
    logger.info("Scheduler[codal]: start")
    s = get_session()
    try:
        results = check_all_active(s)
        total_new = sum(r["new"] for r in results)
        errors = [r for r in results if r["error"]]
        logger.info(
            "Scheduler[codal]: feeds={} new={} errors={}",
            len(results), total_new, len(errors),
        )
    except Exception:
        logger.exception("Scheduler[codal] failed")
    finally:
        s.close()


def _job_auto_wf():
    logger.info("Scheduler[auto_wf]: start")
    s = get_session()
    try:
        from backtesting.auto_walk_forward import run_auto_walk_forward
        r = run_auto_walk_forward(s, strategy="v2b", n_symbols=20)
        if r.ok:
            logger.info(
                "Scheduler[auto_wf]: {} (robust={}, swapped={})",
                r.message, r.n_robust, r.swapped,
            )
            if r.warning:
                logger.warning("Scheduler[auto_wf]: {}", r.warning)
        else:
            logger.warning("Scheduler[auto_wf]: {}", r.message)
    except Exception:
        logger.exception("Scheduler[auto_wf] failed")
    finally:
        s.close()


def _job_weekly():
    logger.info("Scheduler[weekly]: start")
    s = get_session()
    try:
        rpt = build_weekly_report(s, days=7)
        path = save_weekly_report(rpt)
        logger.info("Scheduler[weekly]: saved to {}", path)
    except Exception:
        logger.exception("Scheduler[weekly] failed")
    finally:
        s.close()


def _job_backup():
    logger.info("Scheduler[backup]: start")
    try:
        from scripts.backup_db import full_db_backup, cleanup_old_backups
        ok, msg = full_db_backup()
        if ok:
            deleted = cleanup_old_backups(keep_last_n=10)
            logger.info(
                "Scheduler[backup]: {} (cleaned {})", msg, deleted,
            )
        else:
            logger.warning("Scheduler[backup]: {}", msg)
    except Exception:
        logger.exception("Scheduler[backup] failed")


# ===========================================================================
# Public API
# ===========================================================================

def start_scheduler() -> str:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return "already running"

    _scheduler = BackgroundScheduler(timezone="Asia/Tehran")

    _scheduler.add_job(
        _job_sync, CronTrigger(hour=17, minute=30),
        id="sync", replace_existing=True,
    )
    _scheduler.add_job(
        _job_scan, CronTrigger(hour=18, minute=0),
        id="scan", replace_existing=True,
    )
    _scheduler.add_job(
        _job_tracker, CronTrigger(hour=18, minute=15),
        id="tracker", replace_existing=True,
    )
    _scheduler.add_job(
        _job_codal, CronTrigger(hour=20, minute=0),
        id="codal", replace_existing=True,
    )
    _scheduler.add_job(
        _job_auto_wf,
        CronTrigger(day_of_week="fri", hour=18, minute=0),
        id="auto_wf", replace_existing=True,
    )
    _scheduler.add_job(
        _job_weekly,
        CronTrigger(day_of_week="fri", hour=19, minute=0),
        id="weekly", replace_existing=True,
    )
    _scheduler.add_job(
        _job_backup,
        CronTrigger(day_of_week="fri", hour=20, minute=0),
        id="backup", replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started: sync@17:30 scan@18:00 tracker@18:15 "
        "codal@20:00 auto_wf@Fri18:00 weekly@Fri19:00 backup@Fri20:00"
    )
    return "started"


def stop_scheduler() -> str:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        return "stopped"
    return "not running"


def scheduler_status() -> dict:
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "jobs": []}
    jobs = [
        {
            "id": j.id,
            "next_run": j.next_run_time.strftime("%Y-%m-%d %H:%M")
            if j.next_run_time else "-",
        }
        for j in _scheduler.get_jobs()
    ]
    return {"running": True, "jobs": jobs}