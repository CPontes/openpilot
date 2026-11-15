-- LizPilot receiver, the error reports (ERR-01, openpilot/selfdrive/hexa/report.py). Run once in the project's SQL editor,
-- AFTER schema.sql and BEFORE deploying the index.ts that reads this table (its daily purge deletes from it).
-- One row per "Enviar para análise" the owner confirmed: a small JSON, no package in the bucket.

create table if not exists lizpilot_reports (
  id text primary key,
  device text not null references lizpilot_devices(id) on delete cascade, -- "apagar meus envios" takes them with the device
  created_at timestamptz not null default now(), -- the day only (handler.ts day())
  brand text,
  model text,
  edition text,
  branch text,
  git_commit text,
  version text,
  errors integer not null default 0 check (errors between 0 and 20),
  report jsonb not null
);
create index if not exists lizpilot_reports_created on lizpilot_reports (created_at desc);
create index if not exists lizpilot_reports_device on lizpilot_reports (device);

-- Row level security on and no policy: only the service role (the Edge Function) reads or writes.
alter table lizpilot_reports enable row level security;
revoke all on table lizpilot_reports from public, anon, authenticated;
grant select, insert, delete on table lizpilot_reports to service_role;

-- Retention: the same daily job of schema.sql (/v1/purge) deletes the reports older than 365 days. Nothing to schedule here.
