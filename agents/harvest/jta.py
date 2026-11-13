"""Harvester: the Jewish Telegraphic Agency archive, 1923 to today.

JTA reported daily on Jewish communities worldwide, and its whole archive
is online. This agent reads it month by month, article by article, and
records every antisemitic incident an article reports, with the article's
link and a passage copied from it. The passage is checked against the
article text before the row goes live.

  python -m agents.harvest.jta minutes=300          work the queue for up to 300 minutes
  python -m agents.harvest.jta only=193304,197506   work the named months
"""
import calendar
import html
import random
import re
import sys
import time
import requests

from lib import db, llm
from lib.geo_world import World, norm

API = "https://www.jta.org/wp-json/wp/v2/archive"
UA = {"User-Agent": "Mozilla/5.0 (compatible; AntisemitismRecord/0.1; +https://github.com/antisemitism-record/record)"}
TYPES = {"murder", "assault", "arson", "vandalism", "desecration", "threat", "harassment", "terror", "riot",
         "expulsion", "property", "discrimination", "blood libel", "plot"}
HINT = re.compile(
    r"attack|assault|beat|riot|pogrom|mob|desecrat|swastika|bomb|arson|fire|burn|kill|murder|shot|stab|"
    r"vandal|cemeter|tombstone|grave|expel|expuls|loot|plunder|stoned|smash|window|blood libel|ritual murder|"
    r"anti-semit|antisemit|anti-jewish|nazi|fascist|hooligan|terror|outrage|excess|wounded|injur|slay|slain|"
    r"lynch|molest|threat|boycott|daub|defac|hurl|raid|massacre|victim", re.I)
BATCH_CHARS = 26000
ARTICLE_CHARS = 2200

SYSTEM = """You extract antisemitic incidents from news articles of the Jewish Telegraphic Agency for an open archive. The archive only records what the source reports. It never adds claims of its own.

An incident is a specific act at a specific place: an attack on a person, a riot or pogrom, a bombing, arson, a synagogue, cemetery, school, shop or home attacked or desecrated, a blood libel, an expulsion, a threat, an act of harassment, a discriminatory act against named people or a named community. Include small local incidents. One article may report several incidents; list each.

Do not list: general political news, speeches, laws and decrees as such, statistics, opinion, appeals, relief work, court proceedings with no act described, or anything where the article does not say or show that Jews or a Jewish place were the target. Do not list Holocaust camps, ghettos or deportations; those are recorded separately. Local pogroms, shootings and attacks in those years are included.

For each incident give:
- article_id: the id of the article it comes from
- event_date: YYYY-MM-DD if the article states or clearly implies the day (for example "yesterday" relative to the article date); otherwise null
- place: the town or city as the article names it; null if only a country is given
- modern_name: today's usual English name of that town if different or if known, else the same as place
- admin1: region or province if stated
- country: two-letter ISO code of the country that place is in TODAY
- type: one of murder, assault, arson, vandalism, desecration, threat, harassment, terror, riot, expulsion, property, discrimination, blood libel
- target: person, synagogue, school, cemetery, business, home, memorial, community building or community
- killed, wounded: numbers if stated, else null. If the article gives a range, use the lower figure.
- summary: one or two plain neutral sentences, no adjectives, saying what the article reports
- inclusion_tag: A for an act against Jews or Jewish property; B for classic anti-Jewish hate such as blood libel, Nazi symbols, conspiracy claims; C for Israel-related speech
- inclusion_basis: a few words on what in the article shows a Jewish target was chosen
- passage: a passage copied word for word from the article, at most 280 characters, that states the incident

Never invent anything. If no article reports an incident, return an empty list. Output only:

<incidents>
[ {"article_id": 0, "event_date": null, "place": "", "modern_name": "", "admin1": "", "country": "", "type": "", "target": "", "killed": null, "wounded": null, "summary": "", "inclusion_tag": "A", "inclusion_basis": "", "passage": ""} ]
</incidents>"""


def clean(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def fetch_month(ym):
    y, m = divmod(ym, 100)
    last = calendar.monthrange(y, m)[1]
    params = {"after": f"{y:04d}-{m:02d}-01T00:00:00", "before": f"{y:04d}-{m:02d}-{last:02d}T23:59:59",
              "per_page": 100, "orderby": "date", "order": "asc", "_fields": "id,date,link,title,content"}
    arts, page = [], 1
    while True:
        for attempt in range(5):
            r = requests.get(API, params={**params, "page": page}, headers=UA, timeout=90)
            if r.status_code == 200 or (r.status_code == 400 and page > 1):
                break
            time.sleep(10 * (attempt + 1))
        if r.status_code != 200:
            if page > 1 and r.status_code == 400:
                break
            raise RuntimeError(f"JTA API {r.status_code} for {ym} page {page}")
        got = r.json()
        for a in got:
            arts.append({"id": a["id"], "date": a["date"][:10], "link": a["link"],
                         "title": clean(a["title"]["rendered"]), "text": clean(a["content"]["rendered"])})
        pages = int(r.headers.get("X-WP-TotalPages", "1"))
        if page >= pages or not got:
            break
        page += 1
        time.sleep(1)
    return arts


def batches(arts):
    cur, size = [], 0
    for a in arts:
        piece = f'[article {a["id"]}] date {a["date"]}\nTITLE: {a["title"]}\n{a["text"][:ARTICLE_CHARS]}\n'
        if cur and size + len(piece) > BATCH_CHARS:
            yield cur
            cur, size = [], 0
        cur.append((a, piece))
        size += len(piece)
    if cur:
        yield cur


def shingle_match(passage, text):
    w = re.findall(r"\w+", passage.lower())
    if len(w) < 5:
        return False
    page = " ".join(re.findall(r"\w+", text.lower()))
    sh = [" ".join(w[i:i + 4]) for i in range(len(w) - 3)]
    return sum(1 for s in sh if s in page) / len(sh) >= 0.7


def to_item(inc, by_id, world, run_id, n):
    a = by_id.get(inc.get("article_id"))
    if not a or not inc.get("passage") or not inc.get("summary"):
        return None
    cc = (inc.get("country") or "").upper()[:2]
    if not re.fullmatch(r"[A-Z]{2}", cc):
        cc = None
    place = (inc.get("place") or "").strip() or None
    lat = lon = None
    prec = "country"
    if cc:
        for name in (inc.get("modern_name"), place):
            if name:
                lat, lon, prec = world.locate(name, cc)
                if prec == "city":
                    break
    stated = inc.get("event_date")
    has_day = isinstance(stated, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", stated) and abs(int(stated[:4]) - int(a["date"][:4])) <= 1
    ok = shingle_match(inc["passage"], a["title"] + " " + a["text"])

    def num(v):
        return int(v) if isinstance(v, (int, float)) and 0 <= v < 10_000_000 else None

    row = {
        "event_date": stated if has_day else a["date"],
        "event_date_note": None if has_day else "Date of the report; the article does not give the day",
        "date_precision": "day", "lat": lat, "lon": lon, "geo_precision": prec,
        "place": place, "admin1": inc.get("admin1") or None, "country": cc,
        "type": inc.get("type") if inc.get("type") in TYPES else "harassment",
        "target": inc.get("target") or None, "severity": None,
        "killed": num(inc.get("killed")), "wounded": num(inc.get("wounded")),
        "summary": str(inc["summary"])[:1000],
        "tier": "documented", "basis": "incident", "status": "active" if ok else "candidate",
        "agent": "harvest:jta", "run_id": run_id,
        "external_source": "jta", "external_key": f'{a["id"]}:{n}',
        "inclusion_tag": inc.get("inclusion_tag") if inc.get("inclusion_tag") in ("A", "B", "C") else "A",
        "inclusion_rule_version": db.RULE_VERSION,
        "inclusion_basis": (inc.get("inclusion_basis") or "")[:500] or None,
    }
    src = {"title": a["title"][:300] or a["link"], "url": a["link"], "publisher": "Jewish Telegraphic Agency",
           "lang": "en", "kind": "news", "evidence": "press", "passage": str(inc["passage"])[:600]}
    return {"row": row, "sources": [src], "verified": ok}


def work_month(ym, world, run_id, usage):
    arts = fetch_month(ym)
    relevant = [a for a in arts if HINT.search(a["title"] + " " + a["text"][:ARTICLE_CHARS])]
    items, parse_fail, counters = [], 0, {}
    for batch in batches(relevant):
        by_id = {a["id"]: a for a, _ in batch}
        text = llm.ask("Articles:\n\n" + "\n".join(p for _, p in batch), system=SYSTEM, usage=usage)
        found, ok = llm.json_block(text)
        if not ok:
            parse_fail += 1
            continue
        for inc in found:
            aid = inc.get("article_id")
            counters[aid] = counters.get(aid, 0) + 1
            item = to_item(inc, by_id, world, run_id, counters[aid])
            if item:
                items.append(item)
    written = db.write_documented(items) if items else 0
    live = sum(1 for i in items if i["verified"])
    return {"articles": len(arts), "read": len(relevant), "incidents": written, "live": live,
            "held": written - live, "unparsed_batches": parse_fail,
            "country_level": sum(1 for i in items if i["row"]["geo_precision"] == "country")}


def claim_one():
    cells = db.select("cell", select="id,period_start", region="eq.jta", status="eq.pending",
                      order="priority.asc", limit=24)
    random.shuffle(cells)
    for c in cells:
        got = db._request("PATCH", "cell", params={"id": f"eq.{c['id']}", "status": "eq.pending", "select": "id"},
                          json={"status": "claimed", "claimed_by": "harvest:jta", "claimed_at": db.now()},
                          prefer="return=representation")
        if got:
            return c
    return None


def main():
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    deadline = time.time() + 60 * int(args.get("minutes", 20))
    world = World()
    usage = llm.Usage()
    run_id = db.start_run("harvest:jta")
    total = {"months": 0, "articles": 0, "read": 0, "incidents": 0, "live": 0, "held": 0}
    only = [int(x) for x in args.get("only", "").split(",") if x]

    def next_cell():
        if "only" in args:
            while only:
                ym = only.pop(0)
                got = db.select("cell", select="id,period_start", region="eq.jta", period_start=f"eq.{ym}")
                if got:
                    return got[0]
                print(ym, "-> not in the queue")
            return None
        return claim_one()

    try:
        while time.time() < deadline:
            cell = next_cell()
            if not cell:
                break
            ym = cell["period_start"]
            before = (usage.input_tokens, usage.output_tokens)
            try:
                res = work_month(ym, world, run_id, usage)
                note = (f'{res["articles"]} articles, {res["read"]} read, {res["incidents"]} incidents, {res["live"]} live, '
                        f'{res["held"]} held, {res["country_level"]} at country level, {res["unparsed_batches"]} unparsed batches; '
                        f'{usage.input_tokens - before[0]} in / {usage.output_tokens - before[1]} out tokens')
                db.upsert("cell_source_check", [{"cell_id": cell["id"], "source_name": "jta.org archive",
                                                 "outcome": "searched", "rows_found": res["incidents"], "note": note}],
                          "cell_id,source_name", "cell_id")
                db._request("PATCH", "cell", params={"id": f"eq.{cell['id']}"},
                            json={"status": "done", "worked_at": db.now(), "rows_written": res["incidents"], "notes": note})
                total["months"] += 1
                for k in ("articles", "read", "incidents", "live", "held"):
                    total[k] += res[k]
                print(ym, "->", note)
            except Exception as e:
                db._request("PATCH", "cell", params={"id": f"eq.{cell['id']}"},
                            json={"status": "blocked", "notes": str(e)[:500]})
                print(ym, "-> FAILED", str(e)[:300])
        total["input_tokens"], total["output_tokens"], total["calls"] = usage.input_tokens, usage.output_tokens, usage.calls
        db.finish_run(run_id, True, rows_written=total["incidents"], log=total,
                      input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        print(total)
    except Exception as e:
        db.finish_run(run_id, False, error=str(e)[:2000])
        raise


if __name__ == "__main__":
    main()
