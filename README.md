# 模型雷達 Kit Radar — 第 1 期

每天自動抓 1999.co.jp（ホビーサーチ）的組裝模型新品與預約品，存進 Supabase，用手機網頁瀏覽。

```
GitHub Actions（每天 05:47）→ 爬 1999.co.jp → Supabase → index.html（GitHub Pages）
```

## 檔案

| 路徑 | 用途 |
|---|---|
| `supabase/schema.sql` | 建立資料表（kits、scrape_runs），網頁只能讀 |
| `scraper/scrape_1999.py` | 爬蟲 |
| `.github/workflows/daily.yml` | 每日排程 |
| `tests/test_parse.py` | 解析器自我測試（排程每次先跑） |
| `index.html` | 卡片頁 |

## 部署步驟

### A. Supabase
1. 建新專案（建議獨立一個，不和其他 app 共用）
2. SQL Editor → 貼上 `supabase/schema.sql` 全文 → Run
3. Project Settings → API，記下三個值：
   - Project URL
   - `anon` public key（給網頁）
   - `service_role` key（給排程，**不可公開**）

### B. GitHub
1. 新建 repo `kit-radar`（Public，免費帳號的 Pages 需要 Public）
2. 上傳本資料夾全部內容，保持資料夾結構（含 `.github/workflows/`）
3. Settings → Secrets and variables → Actions → New repository secret，新增兩個：
   - `SUPABASE_URL` ＝ Project URL
   - `SUPABASE_SERVICE_KEY` ＝ service_role key
4. Actions → 「模型雷達 每日更新」→ Run workflow（手動跑第一次，約 15–20 分鐘）

### C. 網頁
1. 打開 `index.html`，在 `<script>` 開頭填入 `SUPABASE_URL` 與 `SUPABASE_ANON_KEY`（anon key，不是 service key）
2. 部署：
   1. 檔名已是 `index.html`（若你另存成別的名字，先改回 `index.html`）
   2. 上傳到 GitHub 覆蓋原檔
   3. Settings → Pages → Branch: main → Save
3. 手機 Safari 開網址 → 分享 → 加入主畫面

## 使用說明

- **剩 N 天**：1999 的「可取消期限」，過了這天預約就不能取消，等於實際的決定期限（1999 不公布預約截止日）。7 天內標紅。
- **售完**：1999 目前「在庫なし」，可能之後恢復受注。
- 點右上「更新時間」可看各分類抓到幾筆、錯誤訊息；超過 36 小時沒成功更新會變紅。
- 發售超過 3 個月的商品每天自動清掉（第 3 期加入待買/已買後，標記過的會保留）。

## 範圍與已知限制

- 分類：ガンプラ他、ロボット・特撮、フィギュア（只留名稱含 プラモデル/ガンプラ/組立キット 者）。ミリタリー、カーモデル不抓（不合口味，省請求量）。
- 每次最多讀 400 個詳細頁（每次請求間隔 2 秒），第一次建庫約 2–3 天補齊，之後每天只抓新品與有變動的。
- 1999.co.jp 的 robots.txt 對一般爬蟲無限制（2026-09-30 確認）。
- 若排程失敗：Actions 會寄信給你；該次執行頁面底部有 `debug-html` 附件（1999 的網頁樣本），傳給 Claude 就能修解析器。

## 本機測試（選用）

```powershell
pip install -r scraper/requirements.txt
python tests/test_parse.py
$env:DRY_RUN="1"; $env:MAX_DETAIL="5"; python scraper/scrape_1999.py   # 不寫資料庫
```
