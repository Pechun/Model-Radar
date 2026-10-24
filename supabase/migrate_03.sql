-- 模型雷達 第 3 期：👍👎 回饋（跨裝置同步）
-- 在 Supabase → SQL Editor 貼上整段執行一次（可重複執行）

create table if not exists public.kit_feedback (
  kit_id      text primary key,
  vote        smallint not null check (vote in (-1, 1)),
  name        text,
  image_url   text,
  product_url text,
  emb         text,          -- CLIP 向量：商品下架後仍可當學習樣本
  updated_at  timestamptz not null default now()
);

alter table public.kit_feedback enable row level security;

-- 個人用：網頁（anon key）可讀寫這張表；kits 等其他表仍維持唯讀
drop policy if exists "anon rw feedback" on public.kit_feedback;
create policy "anon rw feedback" on public.kit_feedback
  for all to anon using (true) with check (true);
