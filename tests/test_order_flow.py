from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, OrderFlowDaily, PriceBar
from data_sources.tsetmc_orderflow import TsetmcOrderFlowAdapter
from analysis.order_flow import analyze, sync_order_flow, _row_to_snapshot


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("fake", request=None, response=None)

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, mapping):
        self._mapping = mapping

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, **_kw):
        for key, resp in self._mapping.items():
            if key in url:
                return resp
        raise ValueError(f"No mock for URL: {url}")


@pytest.fixture
def mock_httpx(monkeypatch):
    def _install(mapping):
        monkeypatch.setattr(
            httpx, "Client", lambda **_kw: FakeClient(mapping),
        )
    return _install


def test_snapshot_buy_power():
    row = OrderFlowDaily(
        instrument_id=1, symbol="X", ts=datetime(2025, 1, 1),
        buy_retail_vol=100, buy_inst_vol=300,
        sell_retail_vol=50, sell_inst_vol=100,
    )
    snap = _row_to_snapshot(row)
    assert snap.buy_power == 2.67  # 400 / 150
    assert snap.inst_net == 200
    assert snap.dominant == "inst"


def test_smart_money_positive():
    row = OrderFlowDaily(
        instrument_id=1, symbol="X", ts=datetime(2025, 1, 1),
        buy_inst_vol=500, sell_inst_vol=100,
        buy_retail_vol=200, sell_retail_vol=200,
    )
    snap = _row_to_snapshot(row)
    assert snap.smart_money_score > 50


def test_smart_money_negative():
    row = OrderFlowDaily(
        instrument_id=1, symbol="X", ts=datetime(2025, 1, 1),
        buy_inst_vol=100, sell_inst_vol=500,
        buy_retail_vol=200, sell_retail_vol=200,
    )
    snap = _row_to_snapshot(row)
    assert snap.smart_money_score < 50


def test_analyze_no_data():
    s = _session()
    try:
        s.add(Instrument(symbol="X", name="X", market="TSE", is_active=True))
        s.commit()
        rpt = analyze(s, "X")
        assert not rpt.available
        assert "ذخیره نشده" in rpt.message
    finally:
        s.close()


def test_analyze_with_history():
    s = _session()
    try:
        inst = Instrument(symbol="X", name="X", market="TSE", is_active=True)
        s.add(inst); s.commit()
        base = datetime(2025, 1, 1)
        for i in range(10):
            s.add(OrderFlowDaily(
                instrument_id=inst.id, symbol="X",
                ts=base + timedelta(days=i),
                buy_retail_vol=100, buy_inst_vol=200,
                sell_retail_vol=100, sell_inst_vol=100,
            ))
        s.commit()
        rpt = analyze(s, "X")
        assert rpt.available
        assert len(rpt.history) == 10
        assert rpt.latest is not None
        assert rpt.trend_5d in ("improving", "deteriorating", "stable")
    finally:
        s.close()


def test_sync_with_mock(mock_httpx):
    mock_httpx({
        "GetClientTypeHistory": FakeResponse({
            "clientType": [
                {
                    "recDate": 20250101,
                    "buy_N_Volume": 1000, "buy_I_Volume": 500,
                    "sell_N_Volume": 800, "sell_I_Volume": 700,
                    "buy_N_Count": 50, "buy_I_Count": 10,
                    "sell_N_Count": 40, "sell_I_Count": 15,
                },
                {
                    "recDate": 20250102,
                    "buy_N_Volume": 1200, "buy_I_Volume": 900,
                    "sell_N_Volume": 700, "sell_I_Volume": 400,
                    "buy_N_Count": 60, "buy_I_Count": 15,
                    "sell_N_Count": 30, "sell_I_Count": 10,
                },
            ],
        }),
        "BestLimits": FakeResponse({"bestLimits": []}),
    })
    s = _session()
    try:
        inst = Instrument(
            symbol="X", name="X", market="TSE", is_active=True,
            ins_code="46348559193224090",
        )
        s.add(inst); s.commit()
        s.add(PriceBar(
            instrument_id=inst.id, ts=datetime(2025, 1, 1),
            open=1000, high=1010, low=990, close=1000,
            volume=1000, value=1_000_000, source="t",
        ))
        s.commit()

        ok, msg = sync_order_flow(s, TsetmcOrderFlowAdapter(), "X", days=30)
        assert ok
        assert "2 جدید" in msg

        # second time should update
        ok2, msg2 = sync_order_flow(s, TsetmcOrderFlowAdapter(), "X", days=30)
        assert ok2
        assert "2 بروز" in msg2

        assert s.query(OrderFlowDaily).count() == 2
    finally:
        s.close()