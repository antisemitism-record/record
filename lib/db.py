"""Thin client for the archive database (Supabase REST).

Every agent writes through this module, so the rules live in one place:
rows are upserted on their upstream key, every incident is linked to a
source, and every run is logged with what it wrote and what it cost.
"""
import os
import time
import datetime
import requests

URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
KEY = "".join(os.environ.get("SUPABASE_SERVICE_KEY", "").split())
RULE_VERSION = "v1"


def _headers(prefer=None):
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def _request(method, path, *, params=None, json=None, prefer=None):
    last = None
    for attempt in range(5):
        r = requests.request(method, f"{URL}/rest/v1/{path}", params=params, json=json,
                             headers=_headers(prefer), timeout=120)
        if r.status_code < 300:
            return r.json() if r.text else []
        last = f"{r.status_code} {r.text[:500]}"
        if r.status_code < 500 and r.status_code != 429:
            break
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{method} {path} failed: {last}")


def upsert(table, rows, on_conflict, returning="*"):
    if not rows:
        return []
    return _request("POST", table, params={"on_conflict": on_conflict, "select": returning}, json=rows,
                    prefer="resolution=merge-duplicates,return=representation")


def source(title, url, kind, publisher=None, lang="en"):
    """Create or fetch a source row and return its id."""
    row = {"title": title, "url": url, "kind": kind, "publisher": publisher, "lang": lang}
    return upsert("source", [row], "url,title", "id")[0]["id"]


def write_register_rows(rows, source_id, batch=500):
    """Upsert register incidents on (external_source, external_key) and link each to its source."""
    written = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        got = upsert("incident", chunk, "external_source,external_key", "id")
        links = [{"incident_id": g["id"], "source_id": source_id, "role": "official", "evidence": "official"}
                 for g in got]
        upsert("incident_source", links, "incident_id,source_id", "incident_id")
        written += len(got)
    return written


def start_run(agent):
    return _request("POST", "agent_run", params={"select": "id"}, json=[{"agent": agent}],
                    prefer="return=representation")[0]["id"]


def finish_run(run_id, ok, rows_written=0, error=None, log=None, usd=0):
    _request("PATCH", "agent_run", params={"id": f"eq.{run_id}"},
             json={"finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ok": ok, "rows_written": rows_written,
                   "error": error, "log": log, "usd": usd})
