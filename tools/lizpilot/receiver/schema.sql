-- LizPilot receiver (index.ts): who sent (a random key per comma, never the dongle id) and what (one row per package).
-- Run once in the Supabase project's SQL editor. No policy on purpose: with row level security on and no policy, only the
-- service role (the Edge Function) reads or writes. The developers read with the service key (tools/lizpilot/envios.py).

create table if not exists lizpilot_devices (
  id text primary key check (id ~ '^[0-9a-f]{32}$'),
  key text not null check (key ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now()
);

create table if not exists lizpilot_shares (
  id text primary key,
  device text not null references lizpilot_devices(id) on delete cascade,
  mark_id text not null,
  created_at timestamptz not null default now(),
  brand text,
  model text,
  reason text,
  label text,
  note text not null default '',
  edition text,
  branch text,
  git_commit text,
  bytes integer not null,
  object text not null unique,
  manifest jsonb not null,
  unique (device, mark_id)
);
create index if not exists lizpilot_shares_created on lizpilot_shares (created_at desc);
create index if not exists lizpilot_shares_car on lizpilot_shares (brand, model);

alter table lizpilot_devices enable row level security;
alter table lizpilot_shares enable row level security;

-- The packages: private, 8 MB at most each (share.py MAX_BYTES).
insert into storage.buckets (id, name, public, file_size_limit)
values ('lizpilot-shares', 'lizpilot-shares', false, 8388608)
on conflict (id) do nothing;
