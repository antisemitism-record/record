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


def write_documented(items, batch=200):
    """items: [{"row": incident dict, "sources": [{"title","url","kind","lang","publisher","evidence","passage"}]}]

    Upserts each incident on its external key, then every source, then the
    links. The first source listed is the main one; the rest confirm it.
    """
    written = 0
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        got = upsert("incident", [c["row"] for c in chunk], "external_source,external_key", "id,external_key")
        ids = {g["external_key"]: g["id"] for g in got}
        src_rows, seen = [], set()
        for c in chunk:
            for s in c["sources"]:
                k = (s.get("url"), s["title"])
                if k not in seen:
                    seen.add(k)
                    src_rows.append({"title": s["title"], "url": s.get("url"), "kind": s["kind"],
                                     "lang": s.get("lang"), "publisher": s.get("publisher")})
        src = upsert("source", src_rows, "url,title", "id,url,title")
        sid = {(x["url"], x["title"]): x["id"] for x in src}
        links, seen = [], set()
        for c in chunk:
            inc = ids[c["row"]["external_key"]]
            for n, s in enumerate(c["sources"]):
                k = (inc, sid[(s.get("url"), s["title"])])
                if k in seen:
                    continue
                seen.add(k)
                links.append({"incident_id": k[0], "source_id": k[1],
                              "role": "reporting" if n == 0 else "confirming",
                              "evidence": s.get("evidence"), "passage": s.get("passage")})
        upsert("incident_source", links, "incident_id,source_id", "incident_id")
        written += len(got)
    return written


def count(table, **filters):
    """Exact row count for a table with simple equality filters."""
    params = {k: f"eq.{v}" for k, v in filters.items()}
    params["select"] = "id" if table != "incident_source" else "incident_id"
    r = requests.head(f"{URL}/rest/v1/{table}", params=params,
                      headers=_headers("count=exact"), timeout=60)
    r.raise_for_status()
    return int(r.headers["content-range"].split("/")[-1])


def select(table, **params):
    return _request("GET", table, params=params)
