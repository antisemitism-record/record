-- The Antisemitism Record: the archive schema.
--
-- Three principles are enforced here, not left to the agents:
--   1. Nothing is deleted. A duplicate is pointed at its survivor via merged_into.
--   2. Every incident carries two dates: when it happened, and when we learned of it.
--   3. A reported total is never an incident. It lives in its own table with its
--      definition attached, and no query may sum the two.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Controlled vocabularies
-- ---------------------------------------------------------------------------

create type tier as enum (
  'documented',   -- has its own reporting: article, police statement, reference work
  'register'      -- one line from an official dataset, no narrative
);

create type basis as enum (
  'incident',     -- a specific act at a specific place and time
  'aggregate'     -- a figure covering a period, kept out of the dot layer
);

create type row_status as enum (
  'active',
  'merged',       -- folded into another row; see merged_into
  'doubtful',     -- the Verifier could not stand the row up
  'withdrawn'     -- the source retracted, or it failed the inclusion rule
);

create type precision_level as enum ('exact','city','region','state','country');

-- ---------------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------------

create table source (
  id            uuid primary key default gen_random_uuid(),
  url           text,
  title         text not null,
  publisher     text,
  lang          char(2),
  published_on  date,
  retrieved_at  timestamptz not null default now(),
  -- A source may stop resolving. We record that rather than dropping it,
  -- because the citation stays valid even when the page is gone.
  last_checked  timestamptz,
  http_status   int,
  archive_url   text,                  -- Wayback or equivalent, once captured
  kind          text not null,         -- news | police | monitor | reference | archive | dataset | court
  unique nulls not distinct (url, title)
);

create index on source (kind);
create index on source (lang);

-- ---------------------------------------------------------------------------
-- Incidents
-- ---------------------------------------------------------------------------

create table incident (
  id              uuid primary key default gen_random_uuid(),

  event_date      date not null,
  event_date_note text,               -- 'month only', 'spring 1938', 'circa'
  date_precision  text not null default 'day',   -- day | month | year | decade
  first_recorded  timestamptz not null default now(),

  lat             double precision,
  lon             double precision,
  geo_precision   precision_level,
  place           text,
  admin1          text,               -- state, province, Bundesland
  country         char(2),            -- ISO 3166-1 alpha-2

  type            text not null,      -- assault | vandalism | arson | threat | harassment | terror | murder | desecration | expulsion | riot
  target          text,               -- person | synagogue | school | cemetery | business | home | memorial | community building
  severity        smallint,           -- 1..5
  killed          int,
  wounded         int,
  summary         text,

  tier            tier not null,
  basis           basis not null default 'incident',
  status          row_status not null default 'active',
  merged_into     uuid references incident(id),

  -- Which agent put this here, and in which run. Every row is traceable
  -- back to the code and the moment that created it.
  agent           text not null,
  run_id          uuid,

  -- For register rows: the native identifier in the upstream dataset, so a
  -- re-run updates in place instead of duplicating.
  external_key    text,
  external_source text,

  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),

  constraint merged_rows_point_somewhere
    check ((status = 'merged') = (merged_into is not null)),
  constraint killed_sane  check (killed  is null or killed  >= 0),
  constraint wounded_sane check (wounded is null or wounded >= 0),
  constraint severity_sane check (severity is null or severity between 1 and 5)
);

create unique index incident_external_key_uq
  on incident (external_source, external_key)
  where external_key is not null;

create index on incident (event_date);
create index on incident (country, event_date);
create index on incident (type);
create index on incident (status);
create index on incident (tier);
create index on incident (agent);
create index incident_live_geo on incident (lat, lon) where status = 'active';

-- The only view the site and the exports should ever read.
create view incident_active as
  select * from incident where status = 'active';

create table incident_source (
  incident_id uuid not null references incident(id) on delete cascade,
  source_id   uuid not null references source(id)   on delete cascade,
  -- Did this source establish the incident, or merely confirm it?
  role        text not null default 'reporting',  -- reporting | confirming | official
  primary key (incident_id, source_id)
);

-- A row with no source must never reach the archive.
create or replace function incident_requires_source() returns trigger as $$
begin
  if not exists (select 1 from incident_source where incident_id = new.id) then
    raise exception 'incident % has no source', new.id;
  end if;
  return new;
end $$ language plpgsql;

-- ---------------------------------------------------------------------------
-- Reported totals: never rows, never summed with rows
-- ---------------------------------------------------------------------------

create table reported_total (
  id           uuid primary key default gen_random_uuid(),
  jurisdiction text not null,         -- 'New York City', 'Germany', 'France'
  country      char(2),
  year         int  not null,
  body         text not null,         -- 'NYPD', 'ADL', 'CST', 'RIAS', 'SPCJ'
  -- The definition is mandatory. Two totals with different definitions are
  -- not comparable, and this column is what stops the site pretending they are.
  definition   text not null,
  total        int  not null,
  subcategory  text,                  -- 'assault', 'online harassment', if broken out
  source_id    uuid references source(id),
  first_recorded timestamptz not null default now(),
  unique nulls not distinct (jurisdiction, year, body, definition, subcategory)
);

create index on reported_total (country, year);

-- ---------------------------------------------------------------------------
-- Reconciliation: our coverage against what the official bodies published
-- ---------------------------------------------------------------------------

create table reconciliation (
  id              uuid primary key default gen_random_uuid(),
  jurisdiction    text not null,
  country         char(2),
  year            int not null,
  documented_rows int not null,           -- what we hold, active, in scope
  total_id        uuid references reported_total(id),
  matched_rows    int,                    -- rows we hold that appear in their list
  their_total     int,
  coverage        numeric(5,4),           -- documented_rows / their_total
  -- Lincoln-Petersen estimate of the true total, where both sides published
  -- incident-level data and the overlap is known. Null when not computable.
  estimated_true  int,
  method_note     text,
  computed_at     timestamptz not null default now(),
  unique nulls not distinct (jurisdiction, year, total_id)
);

-- ---------------------------------------------------------------------------
-- The backfill queue: country x decade cells
-- ---------------------------------------------------------------------------

create type cell_status as enum ('pending','claimed','done','blocked');

create table cell (
  id            uuid primary key default gen_random_uuid(),
  country       char(2),
  region        text,                  -- used instead of country for coarse pre-1900 cells
  period_start  int not null,          -- 1930
  period_end    int not null,          -- 1939
  granularity   text not null,         -- decade | century
  status        cell_status not null default 'pending',
  priority      int not null default 100,
  claimed_by    text,
  claimed_at    timestamptz,
  worked_at     timestamptz,
  rows_written  int,
  notes         text,
  unique nulls not distinct (country, region, period_start, period_end)
);

create index on cell (status, priority);

-- What each cell actually checked. This is what makes an empty cell
-- meaningful: 'we looked at these five sources and found nothing' is a
-- finding, 'nobody has looked' is not.
create table cell_source_check (
  cell_id    uuid not null references cell(id) on delete cascade,
  source_name text not null,           -- 'pl.wikipedia', 'Chronicling America', 'YIVO'
  outcome    text not null,            -- searched | unavailable | paywalled | no-coverage
  rows_found int not null default 0,
  note       text,
  checked_at timestamptz not null default now(),
  primary key (cell_id, source_name)
);

-- ---------------------------------------------------------------------------
-- Agent runs and what they cost
-- ---------------------------------------------------------------------------

create table agent_run (
  id              uuid primary key default gen_random_uuid(),
  agent           text not null,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  ok              boolean,
  rows_written    int not null default 0,
  rows_merged     int not null default 0,
  sources_fetched int not null default 0,
  input_tokens    bigint not null default 0,
  output_tokens   bigint not null default 0,
  usd             numeric(10,4) not null default 0,
  error           text,
  log             jsonb
);

create index on agent_run (agent, started_at desc);

-- Daily spend, which the Publisher writes and the internal page reads.
create view spend_by_day as
  select date_trunc('day', started_at)::date as day,
         agent,
         sum(usd)          as usd,
         sum(rows_written) as rows_written,
         count(*)          as runs
  from agent_run
  group by 1, 2
  order by 1 desc, 2;

-- ---------------------------------------------------------------------------
-- Review queue: anything a human should look at
-- ---------------------------------------------------------------------------

create table review_queue (
  id          uuid primary key default gen_random_uuid(),
  incident_id uuid references incident(id) on delete cascade,
  cell_id     uuid references cell(id)     on delete cascade,
  reason      text not null,       -- no-source | source-dead | definition-edge | geocode-failed | duplicate-suspect | casualty-conflict
  detail      text,
  raised_by   text not null,
  raised_at   timestamptz not null default now(),
  resolved_at timestamptz,
  resolution  text
);

create index on review_queue (resolved_at) where resolved_at is null;

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------

create or replace function touch_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end $$ language plpgsql;

create trigger incident_touch before update on incident
  for each row execute function touch_updated_at();
