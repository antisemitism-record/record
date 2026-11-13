"""Backfill worker: research one country and decade at a time.

For each cell it asks Claude, with web search, for antisemitic incidents
that are written down somewhere online, each with a link and a verbatim
passage. The worker then opens every link itself and checks the passage
is really on the page. A row with a confirmed passage goes live; a row
whose source could not be opened or matched is held as a candidate for
review. Nothing is dropped.

  python -m agents.research.worker cells=2
"""
import hashlib
import json
import os
import re
import sys
import requests

from lib import db
from agents.research.cells import COUNTRIES

MODEL = os.environ.get("RESEARCH_MODEL", "claude-sonnet-5-5")
API = "https://api.anthropic.com/v1/messages"
TYPES = {"murder", "assault", "arson", "vandalism", "desecration", "threat", "harassment", "terror",
         "riot", "expulsion", "property", "discrimination", "blood libel", "plot"}
EVIDENCE = {"official", "monitor", "press", "scholarly", "community", "testimony", "web"}
PRECISION = {"exact", "city", "region", "state", "country"}

PROMPT = """You are a research assistant for an open archive of antisemitic incidents. The archive only points to what others have already published. It never adds claims of its own.

Task: find antisemitic incidents that took place in {country} between {start} and {end}, using web search. Search in English and in the languages of {country}, including the Jewish press where relevant. Look for specific events at a specific place and time: attacks on people, synagogues, cemeteries, schools, shops and homes, riots and pogroms, blood libels, expulsions, bombings, arson, desecration, threats. Small local incidents matter as much as famous ones. Sources may be official records, monitoring reports, newspapers, scholarly works, community records such as memorial books, testimonies, or other websites.

Rules:
1. Include an incident only if you found it in a search result and can give the page URL.
2. For each source give a passage copied word for word from that page, in the page's own language, at most 300 characters, that states the incident. Do not paraphrase the passage.
3. Include an incident only if the source says or shows that Jews or a Jewish place were the target because they were Jewish. Never infer the motive yourself.
4. Write the summary in plain neutral English, one or two sentences, no adjectives, attributed to what the source reports.
5. If sources disagree on a date or a toll, use the lower figure and say so in date_note or the summary.
6. Do not include the Holocaust's camps, ghettos or deportations; those are recorded separately. Local pogroms and attacks in the same years are included.
7. If you find nothing, return an empty list. Never invent an incident, a URL or a passage.

Aim for up to {limit} distinct incidents, favouring ones that are well sourced. When you are done, output only this, with nothing after it:

<incidents>
[
  {{
    "date": "YYYY-MM-DD",
    "date_precision": "day | month | year",
    "date_note": "",
    "place": "town or city",
    "admin1": "region or province, if known",
    "lat": 0.0, "lon": 0.0,
    "geo_precision": "exact | city | region | country",
    "type": "murder | assault | arson | vandalism | desecration | threat | harassment | terror | riot | expulsion | property | discrimination | blood libel",
    "target": "person | synagogue | school | cemetery | business | home | memorial | community building | community",
    "killed": null, "wounded": null,
    "summary": "",
    "inclusion_tag": "A | B | C",
    "inclusion_basis": "what in the source shows a Jewish target was chosen",
    "sources": [
      {{"url": "", "title": "", "publisher": "", "lang": "en", "evidence": "official | monitor | press | scholarly | community | testimony | web", "passage": ""}}
    ]
  }}
]
</incidents>

Inclusion tags: A is an act against Jews or Jewish property. B is classic anti-Jewish hate such as blood libel, Nazi symbols, conspiracy claims or Holocaust denial. C is Israel-related speech. For an unknown day use the first of the month or year and set date_precision. Give the coordinates of the town."""


def ask(country, start, end, limit, searches):
    headers = {"x-api-key": "".join(os.environ["ANTHROPIC_API_KEY"].split()),
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": searches}]
    messages = [{"role": "user", "content": PROMPT.format(country=country, start=start, end=end, limit=limit)}]
    tokens_in = tokens_out = n_search = 0
    text = ""
    for _ in range(6):
        r = requests.post(API, headers=headers, timeout=900,
                          json={"model": MODEL, "max_tokens": 16000, "tools": tools, "messages": messages})
        if r.status_code >= 300:
            raise RuntimeError(f"Claude API {r.status_code}: {r.text[:400]}")
        body = r.json()
        u = body.get("usage", {})
        tokens_in += u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        tokens_out += u.get("output_tokens", 0)
        n_search += (u.get("server_tool_use") or {}).get("web_search_requests", 0)
        text += "".join(b.get("text", "") for b in body["content"] if b["type"] == "text")
        if body.get("stop_reason") != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": body["content"]}]
    m = re.search(r"<incidents>\s*(\[.*\])\s*</incidents>", text, re.S)
    found = json.loads(m.group(1)) if m else []
    return found, {"input_tokens": tokens_in, "output_tokens": tokens_out, "searches": n_search,
                   "parsed": bool(m)}


def words(s):
    return re.findall(r"\w+", s.lower())


def passage_on_page(passage, url):
    """Open the page and check the quoted passage is really there. Returns (ok, note)."""
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (compatible; archive-verifier)"})
    except Exception as e:
        return False, f"could not open: {type(e).__name__}"
    if r.status_code >= 400:
        return False, f"http {r.status_code}"
    page = " ".join(words(re.sub(r"<[^>]+>", " ", r.text)))
    w = words(passage)
    if len(w) < 5:
        return False, "passage too short"
    shingles = [" ".join(w[i:i + 4]) for i in range(len(w) - 3)]
    hit = sum(1 for s in shingles if s in page) / len(shingles)
    return hit >= 0.6, f"match {hit:.0%}"


def to_item(inc, code, run_id):
    date = str(inc.get("date", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return None, "bad date"
    sources, any_ok = [], False
    for s in inc.get("sources") or []:
        if not (s.get("url") or "").startswith("http") or not s.get("passage"):
            continue
        ok, note = passage_on_page(s["passage"], s["url"])
        any_ok = any_ok or ok
        sources.append({"title": (s.get("title") or s["url"])[:300], "url": s["url"],
                        "publisher": s.get("publisher"), "lang": (s.get("lang") or "")[:2] or None,
                        "kind": "web", "evidence": s.get("evidence") if s.get("evidence") in EVIDENCE else "web",
                        "passage": s["passage"][:600], "_ok": ok, "_note": note})
    if not sources:
        return None, "no usable source"
    sources.sort(key=lambda s: not s["_ok"])
    place = (inc.get("place") or "").strip()
    key = hashlib.sha1(f"{code}|{date}|{place.lower()}|{inc.get('type')}".encode()).hexdigest()[:20]
    tag = inc.get("inclusion_tag") if inc.get("inclusion_tag") in ("A", "B", "C") else "A"

    def num(v):
        return int(v) if isinstance(v, (int, float)) and v >= 0 else None

    def coord(v, lim):
        return round(float(v), 4) if isinstance(v, (int, float)) and abs(v) <= lim and v != 0 else None

    lat, lon = coord(inc.get("lat"), 90), coord(inc.get("lon"), 180)
    row = {
        "event_date": date, "event_date_note": inc.get("date_note") or None,
        "date_precision": inc.get("date_precision") if inc.get("date_precision") in ("day", "month", "year") else "day",
        "lat": lat, "lon": lon,
        "geo_precision": (inc.get("geo_precision") if inc.get("geo_precision") in PRECISION else "city")
        if lat is not None and lon is not None else "country",
        "place": place or None, "admin1": inc.get("admin1") or None, "country": code,
        "type": inc.get("type") if inc.get("type") in TYPES else "harassment",
        "target": inc.get("target") or None, "severity": None,
        "killed": num(inc.get("killed")), "wounded": num(inc.get("wounded")),
        "summary": (inc.get("summary") or "")[:1000],
        "tier": "documented", "basis": "incident",
        "status": "active" if any_ok else "candidate",
        "agent": "research", "run_id": run_id,
        "external_source": "research", "external_key": key,
        "inclusion_tag": tag, "inclusion_rule_version": db.RULE_VERSION,
        "inclusion_basis": (inc.get("inclusion_basis") or "")[:500] or None,
    }
    notes = "; ".join(f"{s['url']} -> {s['_note']}" for s in sources)
    for s in sources:
        s.pop("_ok"), s.pop("_note")
    return {"row": row, "sources": sources, "verified": any_ok, "notes": notes}, None


def claim(n):
    cells = db.select("cell", select="id,country,period_start,period_end", status="eq.pending",
                      order="priority.asc,period_start.desc", limit=n)
    mine = []
    for c in cells:
        got = db._request("PATCH", "cell", params={"id": f"eq.{c['id']}", "status": "eq.pending", "select": "id"},
                          json={"status": "claimed", "claimed_by": "research", "claimed_at": db.now()},
                          prefer="return=representation")
        if got:
            mine.append(c)
    return mine


def main():
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    n, limit, searches = int(args.get("cells", 1)), int(args.get("limit", 25)), int(args.get("searches", 12))
    run_id = db.start_run("research")
    total = {"cells": 0, "rows": 0, "live": 0, "held": 0, "input_tokens": 0, "output_tokens": 0, "searches": 0}
    try:
        for c in claim(n):
            name = COUNTRIES.get(c["country"], c["country"])
            label = f"{name} {c['period_start']}-{c['period_end']}"
            try:
                found, use = ask(name, c["period_start"], c["period_end"], limit, searches)
                items, dropped = [], []
                for inc in found:
                    item, why = to_item(inc, c["country"], run_id)
                    (items if item else dropped).append(item or why)
                seen, uniq = set(), []
                for it in items:
                    if it["row"]["external_key"] not in seen:
                        seen.add(it["row"]["external_key"])
                        uniq.append(it)
                written = db.write_documented(uniq) if uniq else 0
                held = [it for it in uniq if not it["verified"]]
                if held:
                    ids = db.select("incident", select="id,external_key", external_source="eq.research",
                                    external_key="in.(" + ",".join(it["row"]["external_key"] for it in held) + ")")
                    by = {x["external_key"]: x["id"] for x in ids}
                    db._request("POST", "review_queue", json=[
                        {"incident_id": by[it["row"]["external_key"]], "reason": "source-unverified",
                         "detail": it["notes"][:1000], "raised_by": "research"} for it in held
                        if it["row"]["external_key"] in by], prefer="return=minimal")
                note = (f"{len(found)} proposed, {written} written, {written - len(held)} live, {len(held)} held, "
                        f"{len(dropped)} unusable; {use['searches']} searches, "
                        f"{use['input_tokens']} in / {use['output_tokens']} out tokens")
                if not use["parsed"]:
                    note += "; reply could not be parsed"
                db.upsert("cell_source_check", [{"cell_id": c["id"], "source_name": "web search",
                                                 "outcome": "searched", "rows_found": written, "note": note}],
                          "cell_id,source_name", "cell_id")
                db._request("PATCH", "cell", params={"id": f"eq.{c['id']}"},
                            json={"status": "done" if use["parsed"] else "blocked", "worked_at": db.now(),
                                  "rows_written": written, "notes": note})
                total["cells"] += 1
                total["rows"] += written
                total["live"] += written - len(held)
                total["held"] += len(held)
                for k in ("input_tokens", "output_tokens", "searches"):
                    total[k] += use[k]
                print(label, "->", note)
            except Exception as e:
                db._request("PATCH", "cell", params={"id": f"eq.{c['id']}"},
                            json={"status": "blocked", "notes": str(e)[:500]})
                print(label, "-> FAILED", str(e)[:300])
        db.finish_run(run_id, True, rows_written=total["rows"], log=total,
                      input_tokens=total["input_tokens"], output_tokens=total["output_tokens"])
        print(total)
    except Exception as e:
        db.finish_run(run_id, False, error=str(e)[:2000])
        raise


if __name__ == "__main__":
    main()
