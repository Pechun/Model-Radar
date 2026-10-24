"""
模型雷達 Kit Radar — HLJ（Hobby Link Japan）來源 + CLIP 外型評分

流程：
  1. 讀 HLJ 搜尋結果：預購中（All Future Release）＋近 30 天新增，分類 Gundam / Science-Fiction / Figures，
     只收組裝套件（Item type = 各種 Kits）
  2. 新商品或超過 7 天沒更新 → 讀商品頁：發售日、預購截止日、價格、廠商、系列、JAN
  3. 沒有外型向量的商品 → 下載主圖跑 CLIP，跟 scraper/taste.json 的描述比對得到 style_score
  4. upsert 到 Supabase kits 表（source = 'hlj'），寫一筆 scrape_runs

環境變數：SUPABASE_URL、SUPABASE_SERVICE_KEY
選用：DRY_RUN=1、NO_CLIP=1、RESCORE=1（全部重算 style_score）、MAX_DETAIL=數字
HLJ robots.txt：User-agent: * Allow: /（只擋 /account/ 等），本程式請求間隔 1.5 秒。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_1999 import Supa  # noqa: E402

BASE = "https://www.hlj.com"
GENRES = ["Gundam", "Science-Fiction", "Figures"]
KIT_TYPES = ["Injection Kits", "High Grade Kits", "Master Grade Kits", "Real Grade Kits",
             "Perfect Grade Kits", "Entry Grade Kits", "Resin Kits"]
MODES = {
    "reserve": {"StockLevel": "All Future Release"},
    "new": {"dateAdded2": "-30"},
}
MAX_PAGES = 15
DELAY = 1.5
DETAIL_REFRESH_DAYS = 7
KEEP_AFTER_RELEASE_DAYS = 92
TASTE = Path(__file__).resolve().parent / "taste.json"
JST = timezone(timedelta(hours=9))

session = requests.Session()
session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 KitRadar/1.0"),
    "Accept-Language": "en-US,en;q=0.9",
})

THUMB_RE = re.compile(r"/productthumbs/([\w-]+)/(?:thumb_)?([\w-]+?)_0_\d+\.(?:jpg|png|webp)", re.I)


def fetch(url: str) -> str:
    last = None
    for i in range(3):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 404, 410):
                break
        except requests.RequestException as e:
            last = str(e)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


# ---------------------------------------------------------------- 解析

def search_url(genre: str, kit_type: str, mode: str, page: int) -> str:
    q = {"GenreCode2": genre, "MacroType2": kit_type, **MODES[mode], "Page": page}
    return f"{BASE}/search/?{urlencode(q)}"


def parse_search(html: str) -> list[dict]:
    """回傳 [{code, prefix, url, name, image_url}]，依商品縮圖辨識每個商品"""
    soup = BeautifulSoup(html, "html.parser")
    texts: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        h = a["href"].split("?")[0]
        t = a.get_text(" ", strip=True)
        if len(t) > len(texts.get(h, "")):
            texts[h] = t
    out: dict[str, dict] = {}
    scope = soup.select_one(".search-widget-blocks") or soup
    for img in scope.find_all("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("src") or ""
        m = THUMB_RE.search(src)
        if not m:
            continue
        prefix, code = m.group(1).lower(), m.group(2).lower()
        if code in out:
            continue
        a = img.find_parent("a", href=True)
        if not a:
            continue
        href = a["href"].split("?")[0]
        name = (img.get("alt") or img.get("title") or "").strip() or texts.get(href, "")
        url = href if href.startswith("http") else BASE + "/" + href.lstrip("/")
        out[code] = {
            "code": code, "prefix": prefix, "url": url, "name": name,
            "image_url": f"{BASE}/productimages/{prefix}/{code}_0.jpg",
        }
    return list(out.values())


def result_count(html: str) -> int | None:
    m = re.search(r"Showing\s+([\d,]+)\s+results?", BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    return int(m.group(1).replace(",", "")) if m else None


def _field(text: str, *labels: str) -> str | None:
    """找「標籤:」後面的值（同一行或下一行），值太長就當成誤抓"""
    for lb in labels:
        for m in re.finditer(rf"(?:^|\n){re.escape(lb)}\s*:\s*\n?\s*([^\n]+)", text, re.I):
            v = m.group(1).strip()
            if v and len(v) <= 80:
                return v
    return None


def _date(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    return None


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    name = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        name = og["content"]
    elif soup.title and soup.title.string:
        name = soup.title.string
    if name:
        name = re.sub(r"\s*\|\s*HLJ\.com\s*$", "", name).strip()
    rel = _date(_field(text, "Release Date"))
    rel_text = None
    m = re.search(r"expected to release in ([A-Za-z]+) (\d{4})", text)
    if m and m.group(1).lower() in MONTHS:
        rel_text = f"{m.group(2)}年{MONTHS[m.group(1).lower()]}月"
        if not rel:
            rel = date(int(m.group(2)), MONTHS[m.group(1).lower()], 1).isoformat()
    elif rel:
        y, mo, _ = rel.split("-")
        rel_text = f"{y}年{int(mo)}月"
    price = None
    pm = re.search(r"\$\s?([\d,]+\.\d{2})\s*USD", text) or re.search(r"\$\s?([\d,]+\.\d{2})", text)
    if pm:
        price = float(pm.group(1).replace(",", ""))
    item_type = _field(text, "Item Type")
    return {
        "name": name,
        "maker": _field(text, "Manufacturer", "Maker"),
        "series": _field(text, "Series"),
        "jan": ((_field(text, "JAN Code", "JAN") or "").split() or [None])[0],
        "release_date": rel,
        "release_text": rel_text,
        "cancel_deadline": _date(_field(text, "Cancellation Deadline")),
        "price_usd": price,
        "price_jpy": None,  # HLJ 只用美元價；明確清空舊的誤抓值
        "item_type": item_type,
        "sold_out": bool(re.search(r"\b(Sold Out|Discontinued)\b", text)),
    }


def is_kit(item_type: str | None) -> bool:
    return item_type is None or "kit" in item_type.lower()


# ---------------------------------------------------------------- CLIP

class Clip:
    def __init__(self):
        import torch
        from transformers import CLIPModel, CLIPProcessor
        self.torch = torch
        name = "openai/clip-vit-base-patch32"
        self.model = CLIPModel.from_pretrained(name).eval()
        self.proc = CLIPProcessor.from_pretrained(name)

    def _norm(self, x):
        x = x.detach().cpu().numpy().astype(np.float32)
        return x / np.linalg.norm(x, axis=-1, keepdims=True)

    def text(self, prompts):
        with self.torch.no_grad():
            return self._norm(self.model.get_text_features(**self.proc(text=prompts, return_tensors="pt", padding=True)))

    def image(self, raw: bytes):
        from PIL import Image
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            return None
        with self.torch.no_grad():
            return self._norm(self.model.get_image_features(**self.proc(images=im, return_tensors="pt")))[0]


def encode_emb(v: np.ndarray) -> str:
    q = np.clip(np.round(v / (np.abs(v).max() or 1) * 127), -127, 127).astype(np.int8)
    return base64.b64encode(q.tobytes()).decode()


def decode_emb(s: str) -> np.ndarray:
    v = np.frombuffer(base64.b64decode(s), dtype=np.int8).astype(np.float32)
    return v / (np.linalg.norm(v) or 1)


def style_score(emb, pos, neg) -> float:
    p = float(np.sort(pos @ emb)[-2:].mean())
    n = float(np.sort(neg @ emb)[-2:].mean())
    return round((p - n) * 100, 2)


# ---------------------------------------------------------------- 主程式

def main() -> int:
    dry = os.environ.get("DRY_RUN") == "1"
    use_clip = os.environ.get("NO_CLIP") != "1"
    rescore = os.environ.get("RESCORE") == "1"
    force = os.environ.get("FORCE_DETAIL") == "1"
    max_detail = int(os.environ.get("MAX_DETAIL", "700"))
    max_pages = int(os.environ.get("MAX_PAGES", str(MAX_PAGES)))
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.astimezone(JST).date()

    db = run_id = None
    if not dry:
        db = Supa(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        run_id = db.insert("scrape_runs", {"source": "hlj"})["id"]

    errors: list[str] = []
    per_cat: dict[str, int] = {}
    found: dict[str, dict] = {}

    # 1) 搜尋列表
    for mode in MODES:
        for genre in GENRES:
            n = 0
            for kt in KIT_TYPES:
                for page in range(1, max_pages + 1):
                    url = search_url(genre, kt, mode, page)
                    try:
                        html = fetch(url)
                    except Exception as e:
                        errors.append(f"search {genre}/{kt}/{mode} p{page}: {e}")
                        break
                    time.sleep(DELAY)
                    total = result_count(html)
                    if total is None or total > 5000:
                        break  # 沒有符合的商品時 HLJ 會改顯示全站商品，直接跳過
                    items = parse_search(html)
                    fresh = [i for i in items if i["code"] not in found]
                    for i in fresh:
                        found[i["code"]] = {**i, "genre": genre, "mode": mode}
                    n += len(fresh)
                    if not fresh or len(items) < 20:
                        break
            per_cat[f"{genre}:{mode}"] = n
            print(f"[search] {genre}/{mode}: {n}")
    print(f"[search] 共 {len(found)} 款")
    if not found:
        errors.append("搜尋結果 0 筆（HLJ 版面可能改了）")

    # 2) 詳細頁
    existing: dict[str, dict] = {}
    if db:
        for r in db.select("kits", {"select": "id,detail_fetched_at,style_emb,image_url", "source": "eq.hlj"}):
            existing[r["id"]] = r
    stale = now - timedelta(days=DETAIL_REFRESH_DAYS)
    rows: list[dict] = []
    budget = max_detail
    n_new = n_upd = 0
    for code, it in found.items():
        kid = f"hlj:{code}"
        prev = existing.get(kid)
        need = force or prev is None or not prev.get("detail_fetched_at") or \
            datetime.fromisoformat(prev["detail_fetched_at"].replace("Z", "+00:00")) < stale
        row = {"id": kid, "source": "hlj", "source_item_id": code, "category": it["genre"],
               "product_url": it["url"], "last_seen": now_iso, "updated_at": now_iso}
        if prev is None:
            row.update({"name": it["name"] or code, "image_url": it["image_url"], "first_seen": now_iso,
                        "listing": it["mode"]})
        if need and budget > 0:
            budget -= 1
            time.sleep(DELAY)
            try:
                d = parse_detail(fetch(it["url"]))
                itype = d.pop("item_type")
                if dry:
                    print(f"[detail] {code} type={itype} rel={d['release_date']} name={d['name']}")
                if not is_kit(itype):
                    continue
                if not d["name"]:
                    d["name"] = row.get("name") or it["name"] or code
                d["is_rerelease"] = "reissue" in (d["name"] or "").lower()
                d["listing"] = "reserve" if (d["release_date"] and d["release_date"] > today.isoformat()) \
                    or it["mode"] == "reserve" else "new"
                row.update({k: v for k, v in d.items() if v is not None or k == "price_jpy"})
                row["detail_fetched_at"] = now_iso
            except Exception as e:
                errors.append(f"detail {code}: {e}")
                if prev is None:
                    continue
        elif prev is None:
            continue  # 額度用完，新商品明天再抓
        if prev is None:
            n_new += 1
        elif need:
            n_upd += 1
        row["_prev_emb"] = (prev or {}).get("style_emb")
        rows.append(row)
    print(f"[detail] 新增 {n_new}、更新 {n_upd}")

    # 3) CLIP
    if use_clip and rows:
        taste = json.loads(TASTE.read_text(encoding="utf-8"))
        clip = Clip()
        pos, neg = clip.text(taste["positive"]), clip.text(taste["negative"])
        for row in rows:
            emb_s = row.get("_prev_emb")
            if not emb_s:
                img = row.get("image_url") or (existing.get(row["id"]) or {}).get("image_url")
                if img:
                    try:
                        r = session.get(img, timeout=30)
                        v = clip.image(r.content) if r.ok else None
                        if v is not None:
                            emb_s = encode_emb(v)
                            row["style_emb"] = emb_s
                    except requests.RequestException as e:
                        errors.append(f"image {row['id']}: {e}")
            if emb_s and (rescore or "style_emb" in row):
                row["style_score"] = style_score(decode_emb(emb_s), pos, neg)
        if rescore and db:
            # 這次沒出現在列表、但還在資料庫的也重算
            seen = {r["id"] for r in rows}
            extra = []
            for kid, prev in existing.items():
                if kid not in seen and prev.get("style_emb"):
                    extra.append({"id": kid, "style_score": style_score(decode_emb(prev["style_emb"]), pos, neg)})
            for i in range(0, len(extra), 200):
                for e in extra[i:i + 200]:
                    db.update("kits", {"id": e["id"]}, {"style_score": e["style_score"]})
    for row in rows:
        row.pop("_prev_emb", None)

    # 4) 寫入
    if dry:
        print(json.dumps([{k: v for k, v in r.items() if k != "style_emb"} for r in rows[:5]],
                         ensure_ascii=False, indent=2))
    elif db:
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault(tuple(sorted(r)), []).append(r)
        for batch in groups.values():
            db.upsert("kits", batch)
        cutoff = (today - timedelta(days=KEEP_AFTER_RELEASE_DAYS)).isoformat()
        db.delete("kits", {"source": "eq.hlj", "release_date": f"lt.{cutoff}"})

    total = sum(per_cat.values())
    ok = total > 0 and len(errors) < max(5, total // 20)
    if db:
        db.update("scrape_runs", {"id": run_id}, {
            "finished_at": datetime.now(timezone.utc).isoformat(), "ok": ok,
            "items_found": len(found), "items_new": n_new, "items_updated": n_upd,
            "errors": len(errors), "per_category": per_cat, "message": "\n".join(errors[:20]) or None,
        })
    for e in errors[:20]:
        print("[error]", e, file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
