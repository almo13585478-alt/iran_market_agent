"""
Alert engine — computes live alerts for open holdings.

Alerts are computed on the fly (no DB persistence) from:
- current holdings (portfolio.holdings.compute_holdings)
- latest technical analysis (analysis.technical.analyze_instrument)

Alert types:
- stop_loss: price within 3% of stop (= entry - 2*ATR)
- target_1: price within 3% of target1 (= entry + 2*2*ATR)
- rsi_high: RSI > 80
- rsi_low: RSI < 25
- volume_spike: last bar volume > 2x MA20
- daily_drop: last bar change < -5%
- daily_spike: last bar change > +7%
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from analysis.technical import analyze_instrument
from database.models import PriceBar
from portfolio.holdings import compute_holdings


@dataclass
class Alert:
    level: str         # "critical" | "warning" | "info"
    category: str
    symbol: str
    message: str
    value: float | None = None
    threshold: float | None = None


def check_alerts(session: Session) -> list[Alert]:
    alerts: list[Alert] = []
    holdings = compute_holdings(session)

    for h in holdings:
        if not h.last_close:
            continue

        analysis = analyze_instrument(session, h.symbol)
        if not analysis.get("ok"):
            continue

        atr = analysis.get("atr14") or 0
        rsi = analysis.get("rsi14") or 50
        close = h.last_close

        # Stop-loss proximity
        if atr > 0:
            stop = h.avg_cost - 2.0 * atr
            if stop > 0 and close <= stop * 1.03:
                alerts.append(Alert(
                    level="critical",
                    category="stop_loss",
                    symbol=h.symbol,
                    message=f"{h.symbol}: قیمت ({close:,.0f}) نزدیک حد ضرر ({stop:,.0f})",
                    value=close,
                    threshold=stop,
                ))

            # Target proximity
            target = h.avg_cost + 2.0 * 2.0 * atr
            if target > 0 and close >= target * 0.97:
                alerts.append(Alert(
                    level="info",
                    category="target_1",
                    symbol=h.symbol,
                    message=f"{h.symbol}: قیمت ({close:,.0f}) نزدیک هدف ۱ ({target:,.0f})",
                    value=close,
                    threshold=target,
                ))

        # RSI extremes
        if rsi > 80:
            alerts.append(Alert(
                level="warning", category="rsi_high", symbol=h.symbol,
                message=f"{h.symbol}: RSI = {rsi:.1f} (اشباع خرید شدید)",
                value=rsi, threshold=80,
            ))
        elif rsi < 25:
            alerts.append(Alert(
                level="info", category="rsi_low", symbol=h.symbol,
                message=f"{h.symbol}: RSI = {rsi:.1f} (اشباع فروش شدید)",
                value=rsi, threshold=25,
            ))

        # Volume spike
        vol_ma = analysis.get("vol_ma20") or 0
        last_bars = (
            session.query(PriceBar)
            .filter_by(instrument_id=_instrument_id(session, h.symbol))
            .order_by(PriceBar.ts.desc())
            .limit(2)
            .all()
        )
        if len(last_bars) >= 2 and vol_ma > 0:
            last_vol = last_bars[0].volume
            prev_close = last_bars[1].close
            last_close = last_bars[0].close

            if last_vol > 2 * vol_ma:
                alerts.append(Alert(
                    level="info", category="volume_spike", symbol=h.symbol,
                    message=f"{h.symbol}: حجم = {last_vol:,.0f} (بیش از ۲× میانگین)",
                    value=last_vol, threshold=vol_ma * 2,
                ))

            # Daily move
            if prev_close > 0:
                change_pct = (last_close - prev_close) / prev_close * 100.0
                if change_pct <= -5.0:
                    alerts.append(Alert(
                        level="warning", category="daily_drop", symbol=h.symbol,
                        message=f"{h.symbol}: افت روزانه {change_pct:.1f}%",
                        value=change_pct, threshold=-5.0,
                    ))
                elif change_pct >= 7.0:
                    alerts.append(Alert(
                        level="info", category="daily_spike", symbol=h.symbol,
                        message=f"{h.symbol}: رشد روزانه {change_pct:.1f}%",
                        value=change_pct, threshold=7.0,
                    ))

    alerts.sort(key=lambda a: {"critical": 0, "warning": 1, "info": 2}.get(a.level, 3))
    return alerts


def _instrument_id(session: Session, symbol: str) -> int | None:
    from database.models import Instrument
    inst = session.query(Instrument).filter_by(symbol=symbol).first()
    return inst.id if inst else None