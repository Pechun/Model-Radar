"""RSS 來源離線測試。執行：python tests/test_rss.py"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scraper"))
import scrape_rss as s  # noqa: E402

TODAY = date(2026, 10, 23)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xml:lang="ja">
<channel rdf:about="https://hobby.watch.impress.co.jp/"><title>HOBBY Watch</title></channel>
<item rdf:about="https://hobby.watch.impress.co.jp/docs/news/2150033.html">
 <title>【Amazonセール】フィギュアなどの撮影にぴったりな折りたたみ式撮影ボックスがお買い得</title>
 <link>https://hobby.watch.impress.co.jp/docs/news/2150033.html</link>
 <dc:date>2026-10-22T00:05:00+09:00</dc:date><description><![CDATA[撮影ボックス]]></description></item>
<item rdf:about="https://hobby.watch.impress.co.jp/docs/news/2150100.html">
 <title>「HG 1/144 ガンダムアシュタロン」は11月28日発売！</title>
 <link>https://hobby.watch.impress.co.jp/docs/news/2150100.html</link>
 <dc:date>2026-10-22T00:00:00+09:00</dc:date><description><![CDATA[BANDAI SPIRITSはガンプラ…]]></description></item>
<item rdf:about="https://hobby.watch.impress.co.jp/docs/news/2149808.html">
 <title>フジミ模型、プラモデル「1/12 NEXT4 ホンダ CT125」本日出荷開始！</title>
 <link>https://hobby.watch.impress.co.jp/docs/news/2149808.html</link>
 <dc:date>2026-10-22T00:00:00+09:00</dc:date><description><![CDATA[プラモデル]]></description></item>
</rdf:RDF>"""

ARTICLE = """<html><head>
<meta property="og:image" content="https://hobby.watch.impress.co.jp/img/hbw/list/2150/100/001.jpg">
<meta property="og:title" content="コトブキヤ、プラモデル「ヘキサギア ブロックバスター」2027年3月発売予定">
</head><body><article>
<p>コトブキヤは、プラモデル「ヘキサギア ブロックバスター」の予約受付を開始した。</p>
<p>【ヘキサギア ブロックバスター】<br>2027年3月 発売予定<br>価格：14,300円</p>
<p>【ヘキサギア ブロックバスター 限定版】<br>2027年4月下旬 発売予定<br>価格：16,500円</p>
<p>※発売日は流通により前後する場合があります。</p>
</article></body></html>"""

ARTICLE_NOSPEC = """<html><head>
<meta property="og:image" content="https://example.com/a.jpg">
<meta property="og:title" content="「HG 1/144 ガンダムアシュタロン」は11月28日発売！"></head>
<body><article><p>BANDAI SPIRITSより11月28日発売。</p><p>価格：3,520円</p></article></body></html>"""


def test_feed_and_filter():
    entries = s.parse_feed(FEED)
    assert len(entries) == 3
    kits = [e for e in entries if s.is_kit_news(e["title"], e["desc"])]
    assert [s.article_id(e["link"]) for e in kits] == ["2150100"], kits


def test_article_spec_blocks():
    a = s.parse_article(ARTICLE, "x", TODAY)
    assert a["image"].endswith("001.jpg")
    assert a["maker"] == "コトブキヤ"
    assert len(a["products"]) == 2, a["products"]
    p0, p1 = a["products"]
    assert p0["name"] == "ヘキサギア ブロックバスター" and p0["price"] == 14300
    assert p0["release_date"] == date(2027, 3, 1)
    assert p1["release_date"] == date(2027, 4, 21) and p1["price"] == 16500


def test_article_no_spec():
    a = s.parse_article(ARTICLE_NOSPEC, "x", TODAY)
    p = a["products"][0]
    assert p["name"] == "HG 1/144 ガンダムアシュタロン"
    assert p["release_date"] == date(2026, 11, 28) and p["price"] == 3520


def test_release_year_rollover():
    assert s.parse_release("2月発売予定", TODAY) == date(2027, 2, 1)
    assert s.parse_release("10月22日 出荷開始", TODAY) == date(2026, 10, 22)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
