from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, RssFeed, NewsItem
from news.rss_adapter import (
    fetch_feed, fetch_all_active, add_feed, remove_feed,
    _entry_to_news_fields,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


class FakeEntry:
    def __init__(self, title, summary="", link="", published_parsed=None):
        self.title = title
        self.summary = summary
        self.link = link
        self.published_parsed = published_parsed


class FakeFeed:
    def __init__(self, entries, status=200):
        self.entries = entries
        self.status = status


def _fake_parser(entries, status=200):
    return lambda url: FakeFeed(entries, status=status)


def test_entry_extraction():
    e = FakeEntry(
        "رشد فروش فولاد",
        "به دلیل افزایش صادرات",
        "https://example.com/1",
        published_parsed=(2025, 6, 1, 10, 30, 0),
    )
    f = _entry_to_news_fields(e)
    assert f is not None
    assert f["title"] == "رشد فروش فولاد"
    assert f["published_at"] == datetime(2025, 6, 1, 10, 30, 0)


def test_entry_no_title():
    e = FakeEntry("", "no title")
    assert _entry_to_news_fields(e) is None


def test_fetch_feed_success():
    s = _session()
    try:
        s.add(RssFeed(name="test", url="http://x"))
        s.commit()
        feed = s.query(RssFeed).first()

        entries = [
            FakeEntry("رشد فروش فولاد", "به دلیل افزایش صادرات"),
            FakeEntry("افت خودرو در بازار"),
        ]
        parser = _fake_parser(entries)
        r = fetch_feed(s, feed, parser=parser)

        assert r["ok"]
        assert r["new"] == 2
        assert feed.is_healthy
        assert s.query(NewsItem).count() == 2
    finally:
        s.close()


def test_fetch_feed_http_error():
    s = _session()
    try:
        s.add(RssFeed(name="test", url="http://x"))
        s.commit()
        feed = s.query(RssFeed).first()
        parser = _fake_parser([], status=500)
        r = fetch_feed(s, feed, parser=parser)
        assert not r["ok"]
        assert "HTTP 500" in r["error"]
        assert not feed.is_healthy
    finally:
        s.close()


def test_dedup_across_fetches():
    s = _session()
    try:
        s.add(RssFeed(name="test", url="http://x"))
        s.commit()
        feed = s.query(RssFeed).first()
        entries = [FakeEntry("خبر یکسان")]
        parser = _fake_parser(entries)

        r1 = fetch_feed(s, feed, parser=parser)
        r2 = fetch_feed(s, feed, parser=parser)
        assert r1["new"] == 1
        assert r2["new"] == 0
        assert r2["skipped"] == 1
    finally:
        s.close()


def test_add_and_remove_feed():
    s = _session()
    try:
        ok, _ = add_feed(s, "f1", "http://x")
        assert ok
        ok2, msg = add_feed(s, "f1", "http://y")
        assert not ok2
        assert "از قبل" in msg

        feed = s.query(RssFeed).first()
        assert remove_feed(s, feed.id)
        assert s.query(RssFeed).count() == 0
    finally:
        s.close()


def test_fetch_all_active():
    s = _session()
    try:
        s.add(RssFeed(name="a", url="http://a", is_active=True))
        s.add(RssFeed(name="b", url="http://b", is_active=False))
        s.commit()
        parser = _fake_parser([FakeEntry("خبر")])
        results = fetch_all_active(s, parser=parser)
        assert len(results) == 1
        assert results[0]["feed"] == "a"
    finally:
        s.close()