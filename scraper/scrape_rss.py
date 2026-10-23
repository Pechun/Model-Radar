"""
模型雷達 Kit Radar — 模型新聞 RSS 來源

流程：
  1. 讀 RSS（目前：Hobby Watch）
  2. 只保留組裝模型新聞（標題/摘要含プラモデル、ガンプラ等關鍵字，排除拍賣/一番賞/扭蛋…）
  3. 新文章才讀文章頁：抓主圖、拆出每個商品的【名稱】/發售日/價格
  4. upsert 到 Supabase 的 kits 表（source = 'hobbywatch'），寫一筆 scrape_runs

環境變數：SUPABASE_URL、SUPABASE_SERVICE_KEY
選用：DRY_RUN=1（不寫資料庫，只印結果）
Hobby Watch 的 RSS 是公開訂閱用途；文章頁每次請求間隔 2 秒，只讀新文章。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_1999 import Supa  # noqa: E402  共用 Supabase 寫入

FEEDS = [
    ("hobbywatch", "https://hobby.watch.impress.co.jp/data/rss/1.0/hbw/feed.rdf"),
]
DELAY_SEC = 2.0
KEEP_NO_DATE_DAYS = 120   # 沒有發售日的新聞保留天數
KEEP_AFTER_RELEASE_DAYS = 92

# 組裝模型關鍵字（任一命中才收）
KIT_WORDS = [
    "プラモデル", "プラモ", "ガンプラ", "組み立てキット", "組立キット", "組み立て式", "ガレージキット",
    "HG ", "HG1/", "MG ", "MGEX", "MGSD", "RG ", "PG ", "ENTRY GRADE", "SDガンダム", "SDW HEROES",
    "Figure-rise", "フィギュアライズ", "30 MINUTES", "30MM", "30MF", "MODEROID", "ポケプラ",
    "ポケモンプラモ", "フレームアームズ", "ヘキサギア", "ZOIDS", "ゾイド", "PLAMAX", "コトブキヤ",
]
# 不是新品情報的雜項（命中就跳過）
SKIP_WORDS = [
    "Amazonセール", "セール", "一番くじ", "ガチャ", "カプセルトイ", "プライズ", "まとめ",
    "レビュー", "作ってみた", "展示中", "イベント", "コンテスト", "抽選", "当選",
]
# 明顯不合口味的實車/艦船類（可自行增減）
STYLE_SKIP = ["ホンダ", "トヨタ", "日産", "スバル", "マツダ", "ヤマハ", "カワサキ", "スズキ", "ポルシェ",
              "フェラーリ", "ランボルギーニ", "戦艦", "駆逐艦", "空母", "鉄道"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 KitRadar-RSS/1.0")
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "ja,zh-TW;q=0.9"})
JST = timezone(timedelta(hours=9))

NS = {
    "rss": "http://purl.org/rss/1.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
}


# ---------------------------------------------------------------- RSS

def parse_feed(xml_text: str) -> list[dict]:
    """支援 RSS 1.0 (RDF) 與 RSS 2.0"""
    root = ET.fromstring(xml_text)
    out = []
    items = root.findall("rss:item", NS) or root.findall("./channel/item")
    for it in items:
        def t(tag):
            el = it.find(tag, NS)
            return (el.text or "").strip() if el is not None and el.text else ""
        title = t("rss:title") or t("title")
        link = t("rss:link") or t("link")
        desc = t("rss:description") or t("description")
        when = t("dc:date") or t("pubDate")
        if title and link:
            out.append({"title": title, "link": link, "desc": BeautifulSoup(desc, "html.parser").get_text(" "),
                        "date": when})
    return out


def is_kit_news(title: str, desc: str) -> bool:
    text = f"{title} {desc}"
    if any(w in title for w in SKIP_WORDS):
        return False
    if any(w in text for w in STYLE_SKIP):
        return False
    return any(w in text for w in KIT_WORDS)


def article_id(url: str) -> str:
    m = re.search(r"/(\d{5,})\.html", url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]


# ---------------------------------------------------------------- 文章頁

DATE_LINE = re.compile(r"(発売|出荷|予約開始|受注|発送|お届け)")
PRICE_RE = re.compile(r"価格\s*[：:]\s*([\d,]+)\s*円")
BRACKET = re.compile(r"^【(.+?)】$")


def parse_release(text: str, today: date) -> date | None:
    """2027年3月発売予定 / 11月28日発売 / 2026年12月下旬 → date（旬：上=1, 中=11, 下=21）"""
    if not text:
        return None
    m = re.search(r"(?:(\d{4})年)?\s*(\d{1,2})月\s*(?:(\d{1,2})日|(上旬|中旬|下旬))?", text)
    if not m:
        return None
    y = int(m.group(1)) if m.group(1) else None
    mon = int(m.group(2))
    if not 1 <= mon <= 12:
        return None
    day = int(m.group(3)) if m.group(3) else {"上旬": 1, "中旬": 11, "下旬": 21}.get(m.group(4), 1)
    if y is None:
        y = today.year
        # 沒寫年份且月份已過很久 → 明年
        if mon < today.month - 2:
            y += 1
    try:
        return date(y, mon, day)
    except ValueError:
        return date(y, mon, 1)


def maker_from_title(title: str) -> str | None:
    m = re.match(r"^([^、「『]{2,20})、", title)
    return m.group(1).strip() if m else None


def clean_name(title: str) -> str:
    m = re.search(r"「(.+?)」", title)
    return (m.group(1) if m else title).strip()


def parse_article(html: str, title: str, today: date) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:image")
    image = og["content"] if og and og.get("content") else None
    ogt = soup.find("meta", property="og:title")
    full_title = ogt["content"].strip() if ogt and ogt.get("content") else title

    body = soup.find("article") or soup.find("div", class_=re.compile("main|article|body")) or soup
    lines = [re.sub(r"\s+", " ", x).strip() for x in body.get_text("\n").split("\n")]
    lines = [x for x in lines if x]

    products: list[dict] = []
    cur = None
    for ln in lines:
        b = BRACKET.match(ln)
        if b:
            cur = {"name": b.group(1).strip(), "release_text": None, "price": None}
            products.append(cur)
            continue
        if cur is None:
            continue
        if cur["release_text"] is None and DATE_LINE.search(ln) and re.search(r"\d{1,2}月", ln) and len(ln) < 60:
            cur["release_text"] = ln
        pm = PRICE_RE.search(ln)
        if pm and cur["price"] is None:
            cur["price"] = int(pm.group(1).replace(",", ""))

    if not products:
        # 沒有規格區塊 → 從標題/內文找日期與價格
        rel = next((x for x in lines if DATE_LINE.search(x) and re.search(r"\d{1,2}月", x) and len(x) < 60), None)
        pm = next((PRICE_RE.search(x) for x in lines if PRICE_RE.search(x)), None)
        products = [{"name": clean_name(full_title), "release_text": rel,
                     "price": int(pm.group(1).replace(",", "")) if pm else None}]

    for p in products:
        p["release_date"] = parse_release(p["release_text"] or "", today)
    return {"image": image, "title": full_title, "maker": maker_from_title(full_title), "products": products}


# ---------------------------------------------------------------- 主程式

def fetch(url: str) -> str:
    r = session.get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def main() -> int:
    dry = os.environ.get("DRY_RUN") == "1"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.astimezone(JST).date()

    db = None
    run_id = None
    if not dry:
        db = Supa(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        run_id = db.insert("scrape_runs", {"source": "rss"})["id"]

    errors: list[str] = []
    per_feed: dict[str, int] = {}
    rows: list[dict] = []
    n_new = 0

    for src, url in FEEDS:
        try:
            entries = parse_feed(fetch(url))
        except Exception as e:
            errors.append(f"feed {src}: {e}")
            per_feed[f"{src}:news"] = 0
            continue
        kit_entries = [e for e in entries if is_kit_news(e["title"], e["desc"])]
        print(f"[feed] {src}: 共 {len(entries)} 則，模型相關 {len(kit_entries)} 則")
        per_feed[f"{src}:news"] = len(kit_entries)

        known: set[str] = set()
        if db:
            for r in db.select("kits", {"select": "source_item_id", "source": f"eq.{src}"}):
                known.add(r["source_item_id"].split("#")[0])

        for e in kit_entries:
            aid = article_id(e["link"])
            if aid in known:
                continue
            time.sleep(DELAY_SEC)
            try:
                a = parse_article(fetch(e["link"]), e["title"], today)
            except Exception as ex:
                errors.append(f"article {aid}: {ex}")
                continue
            for i, p in enumerate(a["products"][:8]):
                rd = p["release_date"]
                upcoming = (rd is not None and rd > today) or "予約" in (p["release_text"] or "") \
                    or "予約" in e["title"]
                rows.append({
                    "id": f"{src}:{aid}#{i}",
                    "source": src,
                    "source_item_id": f"{aid}#{i}",
                    "category": "news",
                    "listing": "reserve" if upcoming else "new",
                    "name": p["name"][:200],
                    "maker": a["maker"],
                    "image_url": a["image"],
                    "product_url": e["link"],
                    "list_price_jpy": p["price"],
                    "release_text": p["release_text"],
                    "release_date": rd.isoformat() if rd else None,
                    "is_rerelease": "再販" in (p["name"] + e["title"]),
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                    "detail_fetched_at": now_iso,
                    "updated_at": now_iso,
                })
                n_new += 1

    print(f"[rss] 新增 {n_new} 筆，錯誤 {len(errors)}")

    if dry:
        print(json.dumps(rows[:10], ensure_ascii=False, indent=2))
    elif db:
        if rows:
            db.upsert("kits", rows)
        cutoff = (today - timedelta(days=KEEP_AFTER_RELEASE_DAYS)).isoformat()
        db.delete("kits", {"source": "neq.1999", "release_date": f"lt.{cutoff}"})
        old = (now - timedelta(days=KEEP_NO_DATE_DAYS)).isoformat()
        db.delete("kits", {"source": "neq.1999", "release_date": "is.null", "first_seen": f"lt.{old}"})

    ok = sum(per_feed.values()) >= 0 and not any(x.startswith("feed ") for x in errors)
    if db:
        db.update("scrape_runs", {"id": run_id}, {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok, "items_found": sum(per_feed.values()), "items_new": n_new, "items_updated": 0,
            "errors": len(errors), "per_category": per_feed,
            "message": "\n".join(errors[:20]) or None,
        })
    for e in errors[:20]:
        print("[error]", e, file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
