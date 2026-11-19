"""
Reusable Plotly chart helpers for the decision dashboard.

All figures are dark-themed, RTL-friendly (reversed x-axis for Persian
candlesticks), and designed to embed inside st.plotly_chart.
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


FONT = "Tahoma, Arial, sans-serif"
COLOR_UP = "#26a69a"
COLOR_DOWN = "#ef5350"
COLOR_WARN = "#ffc107"
COLOR_INFO = "#2196f3"


def _hex_to_rgba(hex_color: str, alpha: float = 0.15) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _safe_df(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    df = df.copy()
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Candlestick with setup overlays
# ---------------------------------------------------------------------------

def candlestick_with_setup(
    df: pd.DataFrame,
    symbol: str = "",
    entry: float | None = None,
    entry_high: float | None = None,
    stop: float | None = None,
    target1: float | None = None,
    target2: float | None = None,
    ema_periods: tuple[int, ...] = (20, 50, 200),
    show_rsi: bool = True,
    height: int = 620,
) -> go.Figure | None:
    """Full candlestick with volume, EMAs, RSI, and entry/stop/target lines."""
    df = _safe_df(df)
    if df is None:
        return None
    if len(df) < 20:
        return None

    rows = 3 if show_rsi and len(df) >= 20 else 2
    row_heights = [0.6, 0.2, 0.2] if rows == 3 else [0.75, 0.25]

    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
    )

    # Candles
    fig.add_trace(
        go.Candlestick(
            x=df["ts"],
            open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name=symbol or "قیمت",
            increasing_line_color=COLOR_UP,
            increasing_fillcolor=COLOR_UP,
            decreasing_line_color=COLOR_DOWN,
            decreasing_fillcolor=COLOR_DOWN,
            showlegend=True,
        ),
        row=1, col=1,
    )

    # EMAs
    for period in ema_periods:
        if len(df) < period:
            continue
        ema_series = df["close"].ewm(span=period, adjust=False).mean()
        fig.add_trace(
            go.Scatter(
                x=df["ts"], y=ema_series,
                mode="lines",
                name=f"EMA{period}",
                line=dict(width=1.2),
                opacity=0.75,
                hoverinfo="skip",
            ),
            row=1, col=1,
        )

    # Setup lines
    if entry is not None:
        e_high = entry_high if entry_high is not None else entry * 1.005
        fig.add_hrect(
            y0=entry, y1=e_high,
            fillcolor=_hex_to_rgba(COLOR_INFO, 0.2),
            line_width=0,
            row=1, col=1,
        )
        fig.add_hline(
            y=(entry + e_high) / 2,
            line=dict(color=COLOR_INFO, width=1, dash="dot"),
            annotation_text=f"ورود {entry:,.0f}–{e_high:,.0f}",
            annotation_position="right",
            row=1, col=1,
        )

    if stop is not None:
        fig.add_hline(
            y=stop,
            line=dict(color=COLOR_DOWN, width=1.5, dash="dash"),
            annotation_text=f"حد ضرر {stop:,.0f}",
            annotation_position="right",
            row=1, col=1,
        )

    if target1 is not None:
        fig.add_hline(
            y=target1,
            line=dict(color=COLOR_UP, width=1, dash="dash"),
            annotation_text=f"هدف ۱ {target1:,.0f}",
            annotation_position="right",
            row=1, col=1,
        )

    if target2 is not None:
        fig.add_hline(
            y=target2,
            line=dict(color="#004d40", width=1, dash="dot"),
            annotation_text=f"هدف ۲ {target2:,.0f}",
            annotation_position="right",
            row=1, col=1,
        )

    # Volume
    volume_colors = [
        COLOR_UP if c >= o else COLOR_DOWN
        for c, o in zip(df["close"], df["open"])
    ]
    fig.add_trace(
        go.Bar(
            x=df["ts"], y=df["volume"],
            marker_color=volume_colors,
            name="حجم", showlegend=False,
            opacity=0.6,
        ),
        row=2, col=1,
    )

    # RSI
    if rows == 3:
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi_series = (100 - (100 / (1 + rs))).fillna(50)

        fig.add_trace(
            go.Scatter(
                x=df["ts"], y=rsi_series,
                mode="lines", name="RSI",
                line=dict(color="#ba68c8", width=1.5),
                showlegend=False,
            ),
            row=3, col=1,
        )
        for lvl, color in [(70, COLOR_DOWN), (30, COLOR_UP), (50, "#888")]:
            fig.add_hline(
                y=lvl,
                line=dict(color=color, width=0.5, dash="dot"),
                row=3, col=1,
            )
        fig.update_yaxes(range=[0, 100], row=3, col=1)

    fig.update_layout(
        height=height,
        margin=dict(l=10, r=60, t=20, b=10),
        template="plotly_dark",
        font=dict(family=FONT, size=11),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        dragmode="pan",
    )
    fig.update_xaxes(autorange="reversed")
    fig.update_yaxes(title_text="قیمت", row=1, col=1)
    fig.update_yaxes(title_text="حجم", row=2, col=1, showticklabels=False)
    if rows == 3:
        fig.update_yaxes(title_text="RSI", row=3, col=1)

    return fig


# ---------------------------------------------------------------------------
# Sparkline (for cards)
# ---------------------------------------------------------------------------

def sparkline(df: pd.DataFrame, height: int = 70) -> go.Figure | None:
    """Tiny price chart, no axes, color based on net change."""
    df = _safe_df(df)
    if df is None or len(df) < 2:
        return None

    closes = df["close"].tolist()
    change = closes[-1] - closes[0]
    color = COLOR_UP if change >= 0 else COLOR_DOWN

    fig = go.Figure(go.Scatter(
        x=df["ts"], y=closes,
        mode="lines",
        line=dict(color=color, width=1.8),
        fill="tozeroy",
        fillcolor=_hex_to_rgba(color, 0.18),
        hovertemplate="%{x|%m-%d}<br>%{y:,.0f}<extra></extra>",
    ))
    pad = (max(closes) - min(closes)) * 0.1 or 1.0
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False, autorange="reversed"),
        yaxis=dict(
            visible=False,
            range=[min(closes) - pad, max(closes) + pad],
        ),
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x",
    )
    return fig


# ---------------------------------------------------------------------------
# Market state gauge
# ---------------------------------------------------------------------------

def market_gauge(state: str, score: float, height: int = 260) -> go.Figure:
    """Gauge indicator for overall market state."""
    state_label = {
        "green": "🟢 بازار سبز",
        "yellow": "🟡 بازار زرد",
        "red": "🔴 بازار قرمز",
    }.get(state, state)

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=float(score),
        domain={"x": [0, 1], "y": [0, 1]},
        title={
            "text": state_label,
            "font": {"size": 17, "family": FONT},
        },
        delta={"reference": 50, "increasing": {"color": COLOR_UP},
               "decreasing": {"color": COLOR_DOWN}},
        gauge={
            "axis": {
                "range": [0, 100],
                "tickwidth": 1,
                "tickcolor": "#888",
                "tickfont": {"family": FONT},
            },
            "bar": {"color": "rgba(255,255,255,0.35)"},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 1,
            "bordercolor": "#444",
            "steps": [
                {"range": [0, 35], "color": COLOR_DOWN},
                {"range": [35, 65], "color": COLOR_WARN},
                {"range": [65, 100], "color": COLOR_UP},
            ],
            "threshold": {
                "line": {"color": "white", "width": 4},
                "thickness": 0.8,
                "value": float(score),
            },
        },
        number={"font": {"family": FONT, "size": 28}},
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=20, r=20, t=45, b=15),
        template="plotly_dark",
        font=dict(family=FONT),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ---------------------------------------------------------------------------
# Industry treemap
# ---------------------------------------------------------------------------

def industry_treemap(stats: list, height: int = 500) -> go.Figure | None:
    """Treemap: area = symbol count, color = avg score."""
    if not stats:
        return None
    labels = [s.industry for s in stats]
    values = [max(1, s.n_symbols) for s in stats]
    colors = [s.avg_score for s in stats]
    texts = [
        f"{s.n_symbols} نماد<br>"
        f"امتیاز {s.avg_score}<br>"
        f"RSI {s.avg_rsi}<br>"
        f"{s.pct_uptrend}% صعودی"
        for s in stats
    ]

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=[""] * len(stats),
        values=values,
        text=texts,
        textinfo="label",
        hovertemplate="<b>%{label}</b><br>%{text}<extra></extra>",
        marker=dict(
            colors=colors,
            colorscale="RdYlGn",
            cmin=30, cmid=55, cmax=80,
            showscale=True,
            colorbar=dict(
                title="امتیاز",
                tickfont=dict(family=FONT),
                titlefont=dict(family=FONT),
            ),
            line=dict(color="#222", width=1),
        ),
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        template="plotly_dark",
        font=dict(family=FONT),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ---------------------------------------------------------------------------
# Market breadth bar chart
# ---------------------------------------------------------------------------

def market_breadth_bar(breadths: dict, height: int = 250) -> go.Figure | None:
    """Bar chart of avg score per market (TSE/IFB/ETF)."""
    if not breadths:
        return None
    labels = list(breadths.keys())
    scores = [b["avg_score"] for b in breadths.values()]
    counts = [b["n_symbols"] for b in breadths.values()]

    fig = go.Figure(go.Bar(
        x=labels, y=scores,
        marker=dict(
            color=scores, colorscale="RdYlGn",
            cmin=30, cmid=55, cmax=80,
        ),
        text=[f"{s:.0f} ({n})" for s, n in zip(scores, counts)],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>امتیاز %{y:.1f}<extra></extra>",
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(range=[0, 100], title="میانگین امتیاز",
                   gridcolor="#333"),
        template="plotly_dark",
        font=dict(family=FONT),
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Paper equity curve
# ---------------------------------------------------------------------------

def equity_curve(points: list, height: int = 320) -> go.Figure | None:
    """Cumulative-R curve for paper trading."""
    if not points:
        return None
    dates = [p[0] for p in points]
    values = [p[1] for p in points]
    positive = values[-1] >= 0
    color = COLOR_UP if positive else COLOR_DOWN

    fig = go.Figure(go.Scatter(
        x=dates, y=values,
        mode="lines+markers",
        line=dict(color=color, width=2),
        marker=dict(size=4),
        fill="tozeroy",
        fillcolor=_hex_to_rgba(color, 0.15),
        hovertemplate="%{x|%m-%d %H:%M}<br>R: %{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color="#888", dash="dash", width=1))
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="Cumulative R",
        hovermode="x unified",
        template="plotly_dark",
        font=dict(family=FONT),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig
# ---------------------------------------------------------------------------
# Market heatmap (treemap by symbol)
# ---------------------------------------------------------------------------

def market_heatmap(symbols_data: list[dict], height: int = 620) -> go.Figure | None:
    """
    Treemap of individual symbols. Size = volume, color = pct_change.

    symbols_data: [{
        "symbol": str,
        "pct_change": float,  # today's % change
        "score": float | None,
        "close": float,
    }, ...]
    """
    if not symbols_data:
        return None

    labels = []
    parents = []
    values = []
    colors = []
    texts = []

    for row in symbols_data:
        sym = row.get("symbol", "?")
        pct = row.get("pct_change") or 0.0
        score = row.get("score")
        close = row.get("close") or 0
        score_str = f"{score:.0f}" if score is not None else "-"
        labels.append(sym)
        parents.append("")
        values.append(max(1.0, abs(pct) + 0.1))
        colors.append(pct)
        texts.append(f"{pct:+.2f}%<br>امتیاز {score_str}<br>{close:,.0f}")

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        text=texts,
        textinfo="label",
        hovertemplate="<b>%{label}</b><br>%{text}<extra></extra>",
        marker=dict(
            colors=colors,
            colorscale=[
                [0.0, "#b71c1c"],
                [0.3, "#ef5350"],
                [0.5, "#424242"],
                [0.7, "#26a69a"],
                [1.0, "#1b5e20"],
            ],
            cmin=-5, cmid=0, cmax=5,
            showscale=True,
            colorbar=dict(
                title="%تغییر",
                tickfont=dict(family=FONT, size=10),
                titlefont=dict(family=FONT),
                thickness=10,
            ),
            line=dict(color="#0d1117", width=1),
        ),
        branchvalues="total",
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=4, r=4, t=30, b=4),
        template="plotly_dark",
        font=dict(family=FONT, size=11),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig