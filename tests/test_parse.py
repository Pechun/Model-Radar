"""解析器離線測試：用模擬 1999.co.jp 版面的 HTML 驗證欄位抽取。
執行：python -m pytest tests/ 或 python tests/test_parse.py"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scraper"))
import scrape_1999 as s  # noqa: E402

TODAY = date(2026, 9, 30)

LIST_HTML = """
<html><body>
<nav><a href="https://www.1999.co.jp/gundam/">ガンプラ他</a><a href="https://www.1999.co.jp/mlist/654/7/1">HG</a></nav>
<div class="item">
  <a href="https://www.1999.co.jp/11474729">MODEROID ヴォルケイン (プラモデル) - 予約品</a>
  <a href="https://www.1999.co.jp/11474729">予約開始日:2026/9/25 2027年4月 発売予定 MODEROID ヴォルケイン (プラモデル)</a>
  <span>12%OFF</span><span>¥7,832</span>
</div>
<div class="item">
  <a href="/11476472">エミュレータ#1A (プラモデル) - 予約品 - 在庫なし - 注文再開メール</a>
  <a href="/11476472">予約開始日:2026/9/29 2027年3月 発売予定 エミュレータ#1A (プラモデル)</a>
</div>
<div class="item">
  <a href="https://www.1999.co.jp/11400000">S.H.Figuarts なにか (完成品) - 予約品</a>
</div>
<a href="https://www.1999.co.jp/more/reserve/gundam?spage=2">next</a>
</body></html>
"""

DETAIL_HTML = """
<html><head>
<meta property="og:title" content="MODEROID ヴォルケイン (プラモデル) - ホビーサーチ ガンプラ他">
<meta property="og:image" content="https://www.1999.co.jp/itbig147/11474729.jpg">
</head><body>
<h1>MODEROID ヴォルケイン (プラモデル)</h1>
<div>12%OFF</div>
<div>販売価格 ¥7,832</div>
<div><s>メーカー希望小売価格：¥8,900 (税込)</s></div>
<p>発売予定日：2027年4月(2026/9/25予約開始)</p>
<p>本商品は10月28日までキャンセル可能です。それ以降のキャンセルはできません。</p>
<dl>
<dt>メーカー</dt><dd><a href="https://www.1999.co.jp/search?typ1_c=109&cat=gundam&target=Make&sortid=7&searchkey=x">グッドスマイルカンパニー</a></dd>
<dt>スケール</dt><dd>NON</dd>
<dt>シリーズ</dt><dd><a href="https://www.1999.co.jp/search?typ1_c=109&cat=&target=Series&sortid=7&searchkey=y">MODEROID＜モデロイド＞</a></dd>
<dt>原作</dt><dd><a href="https://www.1999.co.jp/search?typ1_c=109&cat=gundam&target=Serieshin&sortid=7&searchkey=z">ガン×ソード</a></dd>
</dl>
<h2>商品仕様</h2>
<dl><dt>JANコード</dt><dd>4570232592759</dd></dl>
</body></html>
"""


def test_list_parse():
    items, has_next = s.parse_list_page(LIST_HTML)
    assert has_next
    assert set(items) == {"11474729", "11476472", "11400000"}
    assert s.is_assembly(items["11474729"]["texts"])
    assert not s.is_assembly(items["11400000"]["texts"])
    assert s.name_from_list(items["11476472"]["texts"]) == "エミュレータ#1A (プラモデル)"
    assert "在庫なし" in " ".join(items["11476472"]["texts"])


def test_detail_parse():
    d = s.parse_detail(DETAIL_HTML, "11474729", TODAY)
    assert d["name"] == "MODEROID ヴォルケイン (プラモデル)"
    assert d["image_url"].endswith("/itbig147/11474729.jpg")
    assert d["maker"] == "グッドスマイルカンパニー"
    assert d["series"] == "MODEROID＜モデロイド＞"
    assert d["original_work"] == "ガン×ソード"
    assert d["scale"] == "NON"
    assert d["jan"] == "4570232592759"
    assert d["price_jpy"] == 7832 and d["list_price_jpy"] == 8900
    assert d["release_text"] == "2027年4月"
    assert d["release_date"] == "2027-04-01"
    assert d["preorder_start"] == "2026-09-25"
    assert d["cancel_deadline"] == "2026-10-28"
    assert d["is_rerelease"] is False


def test_release_variants():
    assert s.parse_release("2027年2月再販", TODAY) == date(2027, 2, 1)
    assert s.parse_release("10月下旬", TODAY) == date(2026, 10, 21)
    assert s.parse_release("2月", TODAY) == date(2027, 2, 1)       # 沒寫年份 → 明年
    assert s.parse_release("2026年9月下旬", TODAY) == date(2026, 9, 21)
    assert s.parse_release("2026/09/26", TODAY) == date(2026, 9, 26)
    assert s.parse_release(None, TODAY) is None
    assert s.parse_month_day_future(1, 15, TODAY) == date(2027, 1, 15)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
