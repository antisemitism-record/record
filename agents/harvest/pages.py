"""Harvester: reference pages in any language (Wikipedia first).

Reads each queued page in full, piece by piece, and records every
antisemitic incident the page describes from 1900 on, with the page link
and a passage copied from it in the page's own language. The passage is
checked against the page text before the row goes live.

  python -m agents.harvest.pages minutes=300
"""
import hashlib
import random
import re
import sys
import time
import requests

from lib import db, llm
from lib.geo_world import World

UA = {"User-Agent": "AntisemitismRecord/0.1 (https://github.com/antisemitism-record/record)"}
CHUNK = 22000
# A page with no word for "Jew" in any script, or no year from 1900 on, is not read by the model.
JEWISH = re.compile(r"jew|jud|j[uü]d|juif|juiv|[żz]yd|žid|евре|євре|יהוד|ייִד|يهود|یهود|ebre|zsid|evre[iu]|jood|joden|hebr|εβρα|yahud|juut|jød|judi|žyd|ユダヤ|犹太|猶太|유대", re.I)
MODERN = re.compile(r"\b(19\d\d|20[0-2]\d)\b")
TYPES = {"murder", "assault", "arson", "vandalism", "desecration", "threat", "harassment", "terror", "riot",
         "expulsion", "property", "discrimination", "blood libel", "plot"}

SYSTEM = """You extract antisemitic incidents from a reference text for an open archive. The text may be in any language. The archive only records what the source says. It never adds claims of its own.

An incident is a specific act at a specific place and time: an attack on a person, a riot or pogrom, a massacre, a bombing or shooting, arson, a synagogue, cemetery, school, shop or home attacked or desecrated, a blood libel, an expulsion, a threat, an act of harassment or discrimination against named people or a named community. List every one the text describes that happened in the year 1900 or later and for which the text gives at least the year and the country. One text may describe hundreds; list them all.

Do not list: general history, laws, ideas, publications, speeches, statistics, or anything where the text does not say or show that Jews or a Jewish place were the target. Do not list Holocaust camps, ghettos or deportations; those are recorded separately. Pogroms, shootings and local attacks in those years are included.

For each incident give:
- date: YYYY-MM-DD. If only the month or year is known use the first day and set date_precision.
- date_precision: day, month or year
- place: the town or city as the text names it; null if only a country is given
- modern_name: today's usual English name of that town
- admin1: region or province if stated
- country: two-letter ISO code of the country where it happened, using today's borders
- type: one of murder, assault, arson, vandalism, desecration, threat, harassment, terror, riot, expulsion, property, discrimination, blood libel
- target: person, synagogue, school, cemetery, business, home, memorial, community building or community
- killed, wounded: numbers if stated, else null. If the text gives a range, use the lower figure.
- summary: one or two plain neutral sentences in English, no adjectives, saying what the text reports
- inclusion_tag: A for an act against Jews or Jewish property; B for classic anti-Jewish hate such as blood libel, Nazi symbols, conspiracy claims; C for Israel-related speech
- inclusion_basis: a few words on what in the text shows a Jewish target was chosen
- passage: a passage copied word for word from the text, in its own language, at most 280 characters, that states the incident

Never invent anything. If the text describes no incident, return an empty list. Output only:

<incidents>
[ {"date": "", "date_precision": "day", "place": "", "modern_name": "", "admin1": "", "country": "", "type": "", "target": "", "killed": null, "wounded": null, "summary": "", "inclusion_tag": "A", "inclusion_basis": "", "passage": ""} ]
</incidents>"""


def fetch(lang, title):
    last = None
    for attempt in range(6):
        r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", headers=UA, timeout=90, params={
            "action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "format": "json",
            "titles": title, "maxlag": 5})
        if r.status_code == 200 and "error" not in r.text[:200]:
            page = next(iter(r.json().get("query", {}).get("pages", {}).values()), {})
            return page.get("extract") or ""
        last = f"{r.status_code} {r.text[:120]}"
        time.sleep(float(r.headers.get("Retry-After", 0) or 0) or 6 * (attempt + 1))
    raise RuntimeError(f"Wikipedia did not answer: {last}")


def chunks(text):
    parts, cur = [], ""
    for para in text.split("\n"):
        if cur and len(cur) + len(para) > CHUNK:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def match(passage, text):
    w = re.findall(r"\w+", passage.lower())
    if len(w) < 4:
        return False
    page = " ".join(re.findall(r"\w+", text.lower()))
    n = 3 if len(w) < 8 else 4
    sh = [" ".join(w[i:i + n]) for i in range(len(w) - n + 1)]
    return sum(1 for s in sh if s in page) / len(sh) >= 0.7


def to_item(inc, lang, title, url, text, world, run_id):
    date = str(inc.get("date") or "")
    cc = (inc.get("country") or "").upper()[:2]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or int(date[:4]) < 1900 or not re.fullmatch(r"[A-Z]{2}", cc):
        return None
    if not inc.get("passage") or not inc.get("summary"):
        return None
    if date[5:7] == "00" or date[8:] == "00":
        date = date[:4] + "-" + (date[5:7] if date[5:7] != "00" else "01") + "-01"
    place = (inc.get("place") or "").strip() or None
    lat = lon = None
    prec = "country"
    for name in (inc.get("modern_name"), place):
        if name:
            lat, lon, prec = world.locate(name, cc)
            if prec == "city":
                break
    if prec != "city":
        lat, lon, prec = world.locate("", cc)
    ok = match(str(inc["passage"]), text)

    def num(v):
        return int(v) if isinstance(v, (int, float)) and 0 <= v < 10_000_000 else None

    key = hashlib.sha1(f"{lang}|{title}|{date}|{(place or '').lower()}|{inc.get('type')}".encode()).hexdigest()[:20]
    row = {
        "event_date": date, "event_date_note": None,
        "date_precision": inc.get("date_precision") if inc.get("date_precision") in ("day", "month", "year") else "day",
        "lat": lat, "lon": lon, "geo_precision": prec, "place": place, "admin1": inc.get("admin1") or None,
        "country": cc, "type": inc.get("type") if inc.get("type") in TYPES else "harassment",
        "target": inc.get("target") or None, "severity": None,
        "killed": num(inc.get("killed")), "wounded": num(inc.get("wounded")),
        "summary": str(inc["summary"])[:1000], "tier": "documented", "basis": "incident",
        "status": "active" if ok else "candidate", "agent": "harvest:pages", "run_id": run_id,
        "external_source": "wiki", "external_key": key,
        "inclusion_tag": inc.get("inclusion_tag") if inc.get("inclusion_tag") in ("A", "B", "C") else "A",
        "inclusion_rule_version": db.RULE_VERSION,
        "inclusion_basis": (inc.get("inclusion_basis") or "")[:500] or None,
    }
    src = {"title": f"Wikipedia ({lang}): {title}"[:300], "url": url, "publisher": "Wikipedia", "lang": lang[:2],
           "kind": "reference", "evidence": "scholarly", "passage": str(inc["passage"])[:600]}
    return {"row": row, "sources": [src], "verified": ok}


def work(region, world, run_id, usage):
    _, lang, title = region.split("|", 2)
    url = f"https://{lang}.wikipedia.org/wiki/" + requests.utils.quote(title.replace(" ", "_"))
    text = fetch(lang, title)
    if not JEWISH.search(text) or not MODERN.search(text):
        return {"chars": len(text), "incidents": 0, "live": 0, "held": 0, "unparsed": 0, "skipped": True}
    items, seen, bad = [], set(), 0
    for part in chunks(text):
        found, ok = llm.json_block(llm.ask("Text:\n\n" + part, system=SYSTEM, usage=usage, max_tokens=16000))
        if not ok:
            bad += 1
            continue
        for inc in found:
            it = to_item(inc, lang, title, url, text, world, run_id)
            if it and it["row"]["external_key"] not in seen:
                seen.add(it["row"]["external_key"])
                items.append(it)
    written = db.write_documented(items) if items else 0
    live = sum(1 for i in items if i["verified"])
    return {"chars": len(text), "incidents": written, "live": live, "held": written - live, "unparsed": bad,
            "skipped": False}


def claim_one(max_priority):
    cells = db.select("cell", select="id,region", granularity="eq.page", status="eq.pending",
                      priority=f"lte.{max_priority}", order="priority.asc", limit=40)
    random.shuffle(cells)
    for c in cells:
        got = db._request("PATCH", "cell", params={"id": f"eq.{c['id']}", "status": "eq.pending", "select": "id"},
                          json={"status": "claimed", "claimed_by": "harvest:pages", "claimed_at": db.now()},
                          prefer="return=representation")
        if got:
            return c
    return None


def main():
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    deadline = time.time() + 60 * int(args.get("minutes", 20))
    limit = int(args.get("pages", 10 ** 9))
    max_priority = int(args.get("maxpriority", 20))
    world, usage = World(), llm.Usage()
    run_id = db.start_run("harvest:pages")
    total = {"pages": 0, "incidents": 0, "live": 0, "held": 0}
    try:
        while time.time() < deadline and total["pages"] < limit:
            cell = claim_one(max_priority)
            if not cell:
                break
            before = (usage.input_tokens, usage.output_tokens)
            try:
                res = work(cell["region"], world, run_id, usage)
                note = ("not read: no Jewish reference or no date from 1900 on; " if res["skipped"] else "") + (
                    f'{res["chars"]} characters, {res["incidents"]} incidents, {res["live"]} live, {res["held"]} held, '
                        f'{res["unparsed"]} unparsed pieces; {usage.input_tokens - before[0]} in / '
                        f'{usage.output_tokens - before[1]} out tokens')
                db._request("PATCH", "cell", params={"id": f"eq.{cell['id']}"},
                            json={"status": "done", "worked_at": db.now(), "rows_written": res["incidents"], "notes": note})
                total["pages"] += 1
                time.sleep(1.5)
                for k in ("incidents", "live", "held"):
                    total[k] += res[k]
                print(cell["region"][:90], "->", note)
            except llm.Stopped as e:
                db._request("PATCH", "cell", params={"id": f"eq.{cell['id']}"},
                            json={"status": "pending", "claimed_by": None})
                print("STOPPED", str(e)[:300])
                break
            except Exception as e:
                db._request("PATCH", "cell", params={"id": f"eq.{cell['id']}"},
                            json={"status": "blocked", "notes": str(e)[:500]})
                print(cell["region"][:90], "-> FAILED", str(e)[:200])
        total["input_tokens"], total["output_tokens"] = usage.input_tokens, usage.output_tokens
        db.finish_run(run_id, True, rows_written=total["incidents"], log=total,
                      input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        print(total)
    except Exception as e:
        db.finish_run(run_id, False, error=str(e)[:2000])
        raise


if __name__ == "__main__":
    main()
