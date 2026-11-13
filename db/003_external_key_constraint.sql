-- 003: the API's upsert needs a plain unique constraint, not a partial index.
-- Nulls stay distinct, so rows without an external key never collide.
drop index if exists incident_external_key_uq;
alter table incident
  add constraint incident_external_key_uq unique (external_source, external_key);
