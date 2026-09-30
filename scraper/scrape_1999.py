"""
模型雷達 Kit Radar — 1999.co.jp（ホビーサーチ）爬蟲 第 1 期

流程：
  1. 逐頁讀取「予約品」「新商品」列表（ガンプラ他 / ロボット・特撮 / フィギュア）
  2. 只保留組裝模型（名稱含 (プラモデル) / (ガンプラ) / (組立キット)）
  3. 新商品、或列表文字有變動、或超過 7 天沒更新 → 讀詳細頁抓完整欄位
  4. upsert 到 Supabase，清掉發售超過 3 個月的舊資料，寫一筆 scrape_runs

環境變數：SUPABASE_URL、SUPABASE_SERVICE_KEY
選用：DRY_RUN=1（不寫資料庫，只印結果）、MAX_DETAIL=數字（單次最多抓幾個詳細頁）
robots.txt（2026-09-30 確認）：User-agent: * 無 Disallow。本爬蟲每次請求間隔 2 秒。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.1999.co.jp"
CATEGORIES = ["gundam", "mecha", "figure"]   # ガンプラ他 / ロボット・特撮 / フィギュア
LISTINGS = ["reserve", "new"]                # 予約品 / 新商品
MAX_PAGES = 20
DELAY_SEC = 2.0
DETAIL_REFRESH_DAYS = 7
KEEP_AFTER_RELEASE_DAYS = 92
ASSEMBLY_MARKERS = ("(プラモデル)", "（プラモデル）", "(ガンプラ)", "（ガンプラ）", "(組立キット)", "（組立キット）")
ITEM_HREF = re.compile(r"^(?:https?://www\.1999\.co\.jp)?/(\d{7,9})/?$")
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "debug"))

JST = timezone(timedelta(hours=9))

session = requests.Session()
session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 KitRadar/0.1 (personal use)"),
    "Accept-Language": "ja,en;q=0.8",
})


# ---------------------------------------------------------------- HTTP

def fetch(url: str) -> str:
    last_err = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                r.encoding = r.encoding or "utf-8"
                time.sleep(DELAY_SEC)
                return r.text
            last_err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        time.sleep(DELAY_SEC * (attempt + 2))
    raise RuntimeError(f"{url}: {last_err}")


def dump_debug(name: str, html: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / name).write_text(html, encoding="utf-8")


# ---------------------------------------------------------------- 日期解析

def today_jst() -> date:
    return datetime.now(JST).date()


_PART_DAY = {"上旬": 1, "中旬": 11, "下旬": 21}


def parse_release(text: str | None, today: date | None = None) -> date | None:
    """把 '2027年4月'、'11月下旬'、'2026/09/26' 轉成排序用日期。"""
    if not text:
        return None
    today = today or today_jst()
    t = re.sub(r"[（(][^）)]*予約開始[）)]", "", text)
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", t)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", t)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(上旬|中旬|下旬)?", t)
    if m:
        return _safe_date(int(m[1]), int(m[2]), _PART_DAY.get(m[3] or "", 1))
    m = re.search(r"(\d{1,2})月\s*(上旬|中旬|下旬)?", t)
    if m:
        d = _safe_date(today.year, int(m[1]), _PART_DAY.get(m[2] or "", 1))
        if d and d < today - timedelta(days=120):
            d = _safe_date(today.year + 1, int(m[1]), d.day)
        return d
    return None


def parse_month_day_future(month: int, day: int, today: date | None = None) -> date | None:
    """'10月28日' 這種沒有年份的期限 → 取最近的未來日期。"""
    today = today or today_jst()
    d = _safe_date(today.year, month, day)
    if d and d < today - timedelta(days=1):
        d = _safe_date(today.year + 1, month, day)
    return d


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ---------------------------------------------------------------- 列表頁

def parse_list_page(html: str) -> tuple[dict[str, dict], bool]:
    """回傳 ({item_id: {"texts": [...]}}, 是否有下一頁)"""
    soup = BeautifulSoup(html, "html.parser")
    items: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        m = ITEM_HREF.match(a["href"].strip())
        if not m:
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        if not text:
            continue
        entry = items.setdefault(m[1], {"texts": []})
        if text not in entry["texts"]:
            entry["texts"].append(text)
    has_next = any(
        "spage=" in a["href"] and a.get_text(strip=True).lower() == "next"
        for a in soup.find_all("a", href=True)
    )
    return items, has_next


def is_assembly(texts: list[str]) -> bool:
    joined = " ".join(texts)
    return any(mk in joined for mk in ASSEMBLY_MARKERS)


def fingerprint(texts: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(texts)).encode()).hexdigest()[:16]


def name_from_list(texts: list[str]) -> str:
    """列表第一段通常是 'NAME (プラモデル) - 予約品 - 在庫なし'。"""
    for t in texts:
        if "予約開始日" in t or "発売" in t[:20]:
            continue
        return re.split(r"\s+-\s+(?:予約品|新商品|再入荷|在庫なし|販売中|ゆうパケット)", t)[0].strip()
    return texts[0]


# ---------------------------------------------------------------- 詳細頁

def _lines(soup: BeautifulSoup) -> str:
    raw = soup.get_text("\n")
    return "\n".join(ln.strip() for ln in raw.splitlines() if ln.strip())


def _link_text(soup: BeautifulSoup, *targets: str) -> str | None:
    for tgt in targets:
        a = soup.find("a", href=re.compile(rf"target={tgt}(&|$)"))
        if a and a.get_text(strip=True):
            return a.get_text(strip=True)
    return None


def _int(s: str | None) -> int | None:
    return int(s.replace(",", "")) if s else None


def parse_detail(html: str, item_id: str, today: date | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = _lines(soup)

    og_title = soup.find("meta", property="og:title")
    name = og_title["content"] if og_title and og_title.get("content") else ""
    name = re.sub(r"\s*-\s*ホビーサーチ.*$", "", name).strip()

    og_img = soup.find("meta", property="og:image")
    image = og_img["content"] if og_img and og_img.get("content") else None

    def rx(pattern: str) -> re.Match | None:
        return re.search(pattern, text)

    price = rx(r"販売価格\s*¥\s*([\d,]+)")
    list_price = rx(r"メーカー希望小売価格\s*[：:]\s*¥\s*([\d,]+)")
    release = rx(r"発売(?:予定)?日\s*[：:]\s*([^\n]+)")
    pre = rx(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*予約開始")
    cancel = rx(r"(\d{1,2})月(\d{1,2})日までキャンセル可能")
    jan = rx(r"JANコード\s*\n?\s*(\d{8,13})")
    scale = rx(r"\nスケール\s*\n\s*([^\n]+)")

    release_text = None
    if release:
        release_text = re.sub(r"[（(][^）)]*予約開始[）)]", "", release[1]).strip()

    return {
        "name": name or None,
        "image_url": image,
        "maker": _link_text(soup, "Make"),
        "series": _link_text(soup, "ItemSeries", "Series"),
        "original_work": _link_text(soup, "SeriesTitle", "Serieshin"),
        "scale": scale[1].strip() if scale else None,
        "jan": jan[1] if jan else None,
        "price_jpy": _int(price[1]) if price else None,
        "list_price_jpy": _int(list_price[1]) if list_price else None,
        "release_text": release_text,
        "release_date": _iso(parse_release(release_text, today)),
        "preorder_start": _iso(_safe_date(int(pre[1]), int(pre[2]), int(pre[3]))) if pre else None,
        "cancel_deadline": _iso(parse_month_day_future(int(cancel[1]), int(cancel[2]), today)) if cancel else None,
        "is_rerelease": bool(release_text and "再販" in release_text),
    }


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------- Supabase

class Supa:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def select(self, table: str, params: dict) -> list[dict]:
        out, offset = [], 0
        while True:
            h = dict(self.h, Range=f"{offset}-{offset + 999}")
            r = requests.get(f"{self.url}/{table}", headers=h, params=params, timeout=60)
            r.raise_for_status()
            rows = r.json()
            out += rows
            if len(rows) < 1000:
                return out
            offset += 1000

    def upsert(self, table: str, rows: list[dict]) -> None:
        for i in range(0, len(rows), 200):
            r = requests.post(
                f"{self.url}/{table}", headers=dict(self.h, Prefer="resolution=merge-duplicates,return=minimal"),
                data=json.dumps(rows[i:i + 200]), timeout=60)
            if r.status_code >= 300:
                raise RuntimeError(f"upsert {table}: {r.status_code} {r.text[:300]}")

    def insert(self, table: str, row: dict) -> dict:
        r = requests.post(f"{self.url}/{table}", headers=dict(self.h, Prefer="return=representation"),
                          data=json.dumps(row), timeout=60)
        r.raise_for_status()
        return r.json()[0]

    def update(self, table: str, match: dict, row: dict) -> None:
        params = {k: f"eq.{v}" for k, v in match.items()}
        r = requests.patch(f"{self.url}/{table}", headers=self.h, params=params, data=json.dumps(row), timeout=60)
        r.raise_for_status()

    def delete(self, table: str, params: dict) -> None:
        r = requests.delete(f"{self.url}/{table}", headers=self.h, params=params, timeout=60)
        r.raise_for_status()


# ---------------------------------------------------------------- 主程式

def main() -> int:
    dry = os.environ.get("DRY_RUN") == "1"
    max_detail = int(os.environ.get("MAX_DETAIL", "400"))
    now_iso = datetime.now(timezone.utc).isoformat()
    today = today_jst()

    db = None
    run_id = None
    if not dry:
        db = Supa(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        run_id = db.insert("scrape_runs", {"source": "1999"})["id"]

    errors: list[str] = []
    per_cat: dict[str, int] = {}
    found: dict[str, dict] = {}   # item_id -> {texts, category, listing}

    # 1) 列表頁
    for cat in CATEGORIES:
        for listing in LISTINGS:
            key = f"{cat}:{listing}"
            count = 0
            for page in range(1, MAX_PAGES + 1):
                url = f"{BASE}/more/{listing}/{cat}" + (f"?spage={page}" if page > 1 else "")
                try:
                    html = fetch(url)
                except Exception as e:
                    errors.append(f"list {key} p{page}: {e}")
                    break
                items, has_next = parse_list_page(html)
                if page == 1 and not items:
                    dump_debug(f"list_{cat}_{listing}.html", html)
                    errors.append(f"list {key}: 第 1 頁沒解析到任何商品（版面可能改了）")
                for iid, info in items.items():
                    if not is_assembly(info["texts"]):
                        continue
                    count += 1
                    rec = found.setdefault(iid, {"texts": [], "category": cat, "listing": listing})
                    for t in info["texts"]:
                        if t not in rec["texts"]:
                            rec["texts"].append(t)
                if not has_next or not items:
                    break
            per_cat[key] = count
            print(f"[list] {key}: {count} 款組裝模型")

    # 2) 決定哪些要讀詳細頁
    existing: dict[str, dict] = {}
    if db:
        for row in db.select("kits", {"select": "id,list_fingerprint,detail_fetched_at", "source": "eq.1999"}):
            existing[row["id"]] = row

    stale_before = datetime.now(timezone.utc) - timedelta(days=DETAIL_REFRESH_DAYS)
    rows: list[dict] = []
    detail_budget = max_detail
    n_new = n_upd = 0

    for iid, info in found.items():
        kid = f"1999:{iid}"
        fp = fingerprint(info["texts"])
        joined = " ".join(info["texts"])
        prev = existing.get(kid)
        need_detail = (
            prev is None
            or prev.get("list_fingerprint") != fp
            or not prev.get("detail_fetched_at")
            or datetime.fromisoformat(prev["detail_fetched_at"].replace("Z", "+00:00")) < stale_before
        )
        row = {
            "id": kid,
            "source": "1999",
            "source_item_id": iid,
            "category": info["category"],
            "listing": info["listing"],
            "product_url": f"{BASE}/{iid}",
            "sold_out": "在庫なし" in joined,
            "list_fingerprint": fp,
            "last_seen": now_iso,
            "updated_at": now_iso,
        }
        if prev is None:
            row["name"] = name_from_list(info["texts"])
        if need_detail and detail_budget > 0:
            detail_budget -= 1
            try:
                html = fetch(f"{BASE}/{iid}")
                d = parse_detail(html, iid, today)
                if not d["image_url"] and not d["price_jpy"]:
                    dump_debug(f"detail_{iid}.html", html)
                    errors.append(f"detail {iid}: 抓不到圖片和價格（版面可能改了）")
                if not d["name"]:
                    d["name"] = row.get("name") or name_from_list(info["texts"])
                row.update(d)
                row["detail_fetched_at"] = now_iso
            except Exception as e:
                errors.append(f"detail {iid}: {e}")
        elif prev is None:
            continue  # 詳細頁額度用完的新商品留到明天
        if prev is None:
            n_new += 1
        elif need_detail:
            n_upd += 1
        rows.append(row)

    print(f"[detail] 新增 {n_new}、更新 {n_upd}、錯誤 {len(errors)}")

    # 3) 寫入 + 清理
    if dry:
        print(json.dumps(rows[:5], ensure_ascii=False, indent=2))
    else:
        # PostgREST 批次 upsert 要求同一批的欄位一致；merge-duplicates 只更新有給的欄位，
        # 所以「只有列表欄位」的舊商品不會蓋掉之前抓到的詳細欄位
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            groups.setdefault(tuple(sorted(r)), []).append(r)
        for batch in groups.values():
            db.upsert("kits", batch)
        cutoff = (today - timedelta(days=KEEP_AFTER_RELEASE_DAYS)).isoformat()
        db.delete("kits", {"release_date": f"lt.{cutoff}"})

    total = sum(per_cat.values())
    ok = total > 0 and len(errors) < max(5, total // 20)
    if db:
        db.update("scrape_runs", {"id": run_id}, {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok, "items_found": len(found), "items_new": n_new, "items_updated": n_upd,
            "errors": len(errors), "per_category": per_cat,
            "message": "\n".join(errors[:20]) or None,
        })
    for e in errors[:20]:
        print("[error]", e, file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
