from datetime import datetime
from sqlalchemy import (
    String, Float, DateTime, Boolean, ForeignKey, Text, Index, Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ins_code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True, index=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    market: Mapped[str] = mapped_column(String(16), default="TSE")
    industry: Mapped[str | None] = mapped_column(String(80), nullable=True)
    flow: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    prices: Mapped[list["PriceBar"]] = relationship(back_populates="instrument", cascade="all, delete-orphan")
    fundamentals: Mapped[list["Fundamental"]] = relationship(back_populates="instrument", cascade="all, delete-orphan")
    order_flows: Mapped[list["OrderFlowDaily"]] = relationship(back_populates="instrument", cascade="all, delete-orphan")


class PriceBar(Base):
    __tablename__ = "price_bars"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(32), default="unknown")
    instrument: Mapped[Instrument] = relationship(back_populates="prices")
    __table_args__ = (Index("ix_price_bars_instr_ts", "instrument_id", "ts", unique=True),)


class Fundamental(Base):
    __tablename__ = "fundamentals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    as_of_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)
    roa: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    instrument: Mapped[Instrument] = relationship(back_populates="fundamentals")
    __table_args__ = (UniqueConstraint("instrument_id", "as_of_date", name="uq_fund_inst_date"),)


class OrderFlowDaily(Base):
    __tablename__ = "order_flow_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    buy_retail_vol: Mapped[float] = mapped_column(Float, default=0.0)
    buy_inst_vol: Mapped[float] = mapped_column(Float, default=0.0)
    sell_retail_vol: Mapped[float] = mapped_column(Float, default=0.0)
    sell_inst_vol: Mapped[float] = mapped_column(Float, default=0.0)
    buy_retail_count: Mapped[int] = mapped_column(Integer, default=0)
    buy_inst_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_retail_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_inst_count: Mapped[int] = mapped_column(Integer, default=0)
    buy_limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_limit_vol: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_limit_vol: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="tsetmc")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    instrument: Mapped[Instrument] = relationship(back_populates="order_flows")
    __table_args__ = (Index("ix_order_flow_instr_ts", "instrument_id", "ts", unique=True),)


class MacroIndicator(Base):
    """Macro indicators (USD, gold, inflation, ...) entered manually or via CSV."""
    __tablename__ = "macro_indicators"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(160))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16), default="IRR")
    as_of_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    __table_args__ = (
        UniqueConstraint("key", "as_of_date", name="uq_macro_key_date"),
    )

class StrategyConfig(Base):
    """Walk-forward result per combo. is_active=True = current recommended."""
    __tablename__ = "strategy_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy: Mapped[str] = mapped_column(String(16), default="v2b", index=True)
    threshold: Mapped[float] = mapped_column(Float)
    atr_mult: Mapped[float] = mapped_column(Float)
    rr_ratio: Mapped[float] = mapped_column(Float)

    # Walk-forward metrics
    train_trades: Mapped[int] = mapped_column(Integer, default=0)
    train_avg_r: Mapped[float] = mapped_column(Float, default=0.0)
    test_trades: Mapped[int] = mapped_column(Integer, default=0)
    test_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    test_avg_r: Mapped[float] = mapped_column(Float, default=0.0)
    test_profit_factor: Mapped[float] = mapped_column(Float, default=0.0)
    degradation: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str] = mapped_column(String(16), default="unknown")

    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="auto")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class AppSettings(Base):
    """Key-value store for user settings that persist across sessions."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON-encoded
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now,
    )

class RankingSnapshot(Base):
    """One row per ranked symbol per batch. Latest batch is auto-loaded."""
    __tablename__ = "ranking_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    technical_score: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity_score: Mapped[float] = mapped_column(Float, default=0.0)
    momentum_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    trend_strength_score: Mapped[float] = mapped_column(Float, default=0.0)
    fundamental_score: Mapped[float] = mapped_column(Float, default=0.0)
    technical_json: Mapped[str] = mapped_column(Text, default="{}")
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    red_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    rank_position: Mapped[int] = mapped_column(Integer, default=0)
    total_ranked: Mapped[int] = mapped_column(Integer, default=0)
    min_bars_used: Mapped[int] = mapped_column(Integer, default=60)


class DataSourceStatus(Base):
    __tablename__ = "data_source_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(64), unique=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=False)


class PortfolioTransaction(Base):
    __tablename__ = "portfolio_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    side: Mapped[str] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaperSignal(Base):
    __tablename__ = "paper_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    strategy: Mapped[str] = mapped_column(String(16), default="v2b")
    signal_ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    atr_at_signal: Mapped[float] = mapped_column(Float, default=0.0)
    score_at_signal: Mapped[float] = mapped_column(Float, default=0.0)
    soft_filters_passed: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    exit_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[datetime] = mapped_column(DateTime, unique=True, index=True)
    state: Mapped[str] = mapped_column(String(16), default="yellow")
    state_score: Mapped[float] = mapped_column(Float, default=0.0)
    breadth_today: Mapped[float] = mapped_column(Float, default=0.0)
    breadth_5d: Mapped[float] = mapped_column(Float, default=0.0)
    trend_breadth: Mapped[float] = mapped_column(Float, default=0.0)
    vol_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    vol_cross: Mapped[float] = mapped_column(Float, default=0.0)
    eq_index: Mapped[float] = mapped_column(Float, default=0.0)
    eq_index_pct: Mapped[float] = mapped_column(Float, default=0.0)
    n_symbols: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)


class NewsItem(Base):
    __tablename__ = "news_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    title: Mapped[str] = mapped_column(String(400))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="manual", index=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)
    impact: Mapped[str] = mapped_column(String(8), default="low")
    credibility: Mapped[int] = mapped_column(Integer, default=2)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class NewsSymbolLink(Base):
    __tablename__ = "news_symbol_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    __table_args__ = (Index("ix_news_symbol_unique", "news_id", "symbol", unique=True),)


class RssFeed(Base):
    __tablename__ = "rss_feeds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class CodalWatch(Base):
    __tablename__ = "codal_watch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    search_url: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=False)
    new_since_check: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class CodalReport(Base):
    __tablename__ = "codal_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("codal_watch.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)