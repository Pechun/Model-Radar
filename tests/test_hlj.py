"""HLJ 解析器離線測試。執行：python tests/test_hlj.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scraper"))
import scrape_hlj as s  # noqa: E402

SEARCH = """<html><body><div class="search-widget-blocks">
<div class="item"><a href="/plamatea-pre-war-military-t-60-power-armor-fallout-gsc59075">
<img src="https://www.hlj.com/productthumbs/gsc/gsc59075_0_1792640455.jpg?v=1792640455" alt="PLAMATEA Pre-war Military T-60 Power Armor (Fallout)"></a>
<p><a href="/plamatea-pre-war-military-t-60-power-armor-fallout-gsc59075">PLAMATEA Pre-war Military T-60 Power Armor (Fallout)</a></p></div>
<div class="item"><a href="https://www.hlj.com/1-20-scale-p-k-a-ausf-l-leopard-reissue-wavmk-085"><img data-src="/productthumbs/wav/wavmk-085_0_1792634454.jpg"></a>
<a href="https://www.hlj.com/1-20-scale-p-k-a-ausf-l-leopard-reissue-wavmk-085">1/20 P.K.A. Ausf L Leopard (Reissue)</a></div>
<img src="/images/logo.png"></div></body></html>"""

DETAIL = """<html><head><title>PLAMATEA Pre-war Military T-60 Power Armor (Fallout) | HLJ.com</title></head><body><h1>After click Buy Now</h1><h1>PLAMATEA Pre-war Military T-60 Power Armor (Fallout)</h1>
<div class="price">$67.90 USD</div>
<p>This item is expected to release in June 2027. Preorder yours now!</p>
<ul><li>Code: GSC59075</li><li>JAN Code: 4570232590755</li><li>Release Date: 2027/06/30</li>
<li>Category: Science-Fiction</li><li>Cancellation Deadline: 2026-11-26</li><li>Series: Fallout</li>
<li>Item Type: Injection Kits</li><li>Manufacturer: Good Smile Company</li></ul>
<div class="related">$12.00 USD</div></body></html>"""

DETAIL2 = """<html><head><title>HG 1/144 Gundam X | HLJ.com</title></head><body><dl><dt>Release Date</dt><dd>2026/12/31</dd>
<dt>Item Type</dt><dd>High Grade Kits</dd><dt>Manufacturer</dt><dd>Bandai</dd></dl>
<span>$25.50</span></body></html>"""


def test_search():
    items = s.parse_search(SEARCH)
    assert [i["code"] for i in items] == ["gsc59075", "wavmk-085"], items
    assert items[0]["image_url"] == "https://www.hlj.com/productimages/gsc/gsc59075_0.jpg"
    assert items[0]["url"].endswith("gsc59075") and items[0]["url"].startswith("https://www.hlj.com/")
    assert items[1]["name"] == "1/20 P.K.A. Ausf L Leopard (Reissue)"


def test_detail():
    d = s.parse_detail(DETAIL)
    assert d["name"] == "PLAMATEA Pre-war Military T-60 Power Armor (Fallout)", d["name"]
    assert d["price_usd"] == 67.90 and d["release_date"] == "2027-06-30"
    assert d["release_text"] == "2027年6月" and d["cancel_deadline"] == "2026-11-26"
    assert d["maker"] == "Good Smile Company" and d["jan"] == "4570232590755"
    assert s.is_kit(d["item_type"])


def test_detail_dl():
    d = s.parse_detail(DETAIL2)
    assert d["release_date"] == "2026-12-31" and d["maker"] == "Bandai" and d["price_usd"] == 25.50
    assert s.is_kit(d["item_type"]) and not s.is_kit("Completed Models")


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); print("ok", k)
