-- 模型雷達 Kit Radar — 第 1 期資料表
-- 在 Supabase → SQL Editor 貼上整段執行一次即可（可重複執行）

create table if not exists public.kits (
  id               text primary key,          -- 例：'1999:11474729'
  source           text not null default '1999',
  source_item_id   text not null,
  category         text,                      -- 1999 分類：gundam / mecha / figure
  listing          text,                      -- reserve（預約品）/ new（新商品）
  name             text not null,
  maker            text,
  series           text,
  original_work    text,                      -- 原作
  scale            text,
  jan              text,
  image_url        text,
  product_url      text not null,
  price_jpy        integer,                   -- 1999 販售價
  list_price_jpy   integer,                   -- 廠商定價
  release_text     text,                      -- 原始文字，例：2027年4月
  release_date     date,                      -- 排序用（上旬=1日、中旬=11日、下旬=21日）
  preorder_start   date,
  cancel_deadline  date,                      -- 1999 可取消期限＝實際「決定期限」
  is_rerelease     boolean not null default false,
  sold_out         boolean not null default false,
  list_fingerprint text,
  first_seen       timestamptz not null default now(),
  last_seen        timestamptz not null default now(),
  detail_fetched_at timestamptz,
  updated_at       timestamptz not null default now()
);

create index if not exists kits_release_idx  on public.kits (release_date);
create index if not exists kits_deadline_idx on public.kits (cancel_deadline);
create index if not exists kits_jan_idx      on public.kits (jan);
create index if not exists kits_maker_idx    on public.kits (maker);

create table if not exists public.scrape_runs (
  id            bigserial primary key,
  source        text not null,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  ok            boolean,
  items_found   integer default 0,
  items_new     integer default 0,
  items_updated integer default 0,
  errors        integer default 0,
  per_category  jsonb,                        -- 例：{"gundam:reserve": 312, ...}
  message       text
);

create index if not exists scrape_runs_started_idx on public.scrape_runs (started_at desc);

-- 權限：網頁（anon key）只能讀；寫入只靠 GitHub Actions 的 service key
alter table public.kits        enable row level security;
alter table public.scrape_runs enable row level security;

drop policy if exists "public read kits" on public.kits;
create policy "public read kits" on public.kits for select using (true);

drop policy if exists "public read runs" on public.scrape_runs;
create policy "public read runs" on public.scrape_runs for select using (true);
