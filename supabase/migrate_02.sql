-- 模型雷達 第 2 期：HLJ 來源 + 外型評分
-- 在 Supabase → SQL Editor 貼上整段執行一次（可重複執行）

alter table public.kits add column if not exists price_usd   numeric;   -- HLJ 售價（美元）
alter table public.kits add column if not exists style_score real;      -- CLIP 外型分數（越高越符合偏好）
alter table public.kits add column if not exists style_emb   text;      -- CLIP 圖片向量（之後 👍👎 學習用）

create index if not exists kits_style_idx on public.kits (style_score desc);

-- 清掉 Hobby Watch 新聞來源的資料（重複、類型雜）
delete from public.kits where source = 'hobbywatch';
