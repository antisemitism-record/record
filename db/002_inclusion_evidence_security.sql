-- 002: inclusion rule tagging, evidence classes, supporting passages,
-- and row level security.

-- A row waits as a candidate until the inclusion rule has been applied to it.
alter type row_status add value if not exists 'candidate' before 'active';

-- ---------------------------------------------------------------------------
-- Inclusion rule
--   A  acts against Jews or Jewish property, including terror against Jews in Israel
--   B  classic hate: symbols, blood libel, conspiracy claims, Holocaust denial
--   C  Israel-related speech under the IHRA examples; readers can switch it off
-- The rule is versioned so it can change and be re-applied without re-collecting.
-- ---------------------------------------------------------------------------

alter table incident
  add column inclusion_tag          char(1),
  add column inclusion_rule_version text,
  add column inclusion_basis        text,   -- what in the source shows a Jewish target was chosen
  add constraint inclusion_tag_valid check (inclusion_tag is null or inclusion_tag in ('A','B','C')),
  add constraint tag_carries_version check ((inclusion_tag is null) = (inclusion_rule_version is null));

create index on incident (inclusion_tag);

-- ---------------------------------------------------------------------------
-- Evidence class and supporting passage, per source link
-- ---------------------------------------------------------------------------

create type evidence_class as enum (
  'official',     -- police, court, government record
  'monitor',      -- a monitoring body's report
  'press',        -- newspaper or broadcaster
  'scholarly',    -- academic or reference work
  'community',    -- yizkor book, community ledger, organization report
  'testimony',    -- survivor or witness account, memoir
  'web'           -- other website
);

alter table incident_source
  add column evidence   evidence_class,
  add column passage    text,          -- short excerpt from the source that supports the row
  add column passage_lang char(2),
  add constraint passage_short check (passage is null or length(passage) <= 600);

-- The view lists columns at creation time, so rebuild it to pick up the new ones.
create or replace view incident_active as
  select * from incident where status = 'active';

-- ---------------------------------------------------------------------------
-- Row level security
-- Supabase exposes this schema over its public API. With RLS on and no
-- policies, only the service key (used by the agents) can read or write.
-- Public read access is granted later, deliberately, through the site.
-- ---------------------------------------------------------------------------

alter table source            enable row level security;
alter table incident          enable row level security;
alter table incident_source   enable row level security;
alter table reported_total    enable row level security;
alter table reconciliation    enable row level security;
alter table cell              enable row level security;
alter table cell_source_check enable row level security;
alter table agent_run         enable row level security;
alter table review_queue      enable row level security;

alter view incident_active set (security_invoker = true);
alter view spend_by_day    set (security_invoker = true);
