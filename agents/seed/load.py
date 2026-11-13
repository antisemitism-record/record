"""Load the hand-documented incidents gathered before the archive existed.

Each has its own reporting: a reference article, a press report or a
monitor statement. Safe to re-run: rows are keyed on their seed id.
"""
import json
import sys
from lib import db

ISO = {"Israel": "IL", "United States": "US", "France": "FR", "Germany": "DE", "Canada": "CA", "Turkey": "TR",
       "West Bank": "PS", "Poland": "PL", "United Kingdom": "GB", "Tunisia": "TN", "Egypt": "EG", "Austria": "AT",
       "Belgium": "BE", "Italy": "IT", "Australia": "AU", "Libya": "LY", "Syria": "SY", "Morocco": "MA",
       "Argentina": "AR", "Sweden": "SE", "Algeria": "DZ", "Iraq": "IQ", "Romania": "RO", "Ukraine": "UA",
       "Yemen": "YE", "Bahrain": "BH", "Kenya": "KE", "Russia": "RU", "India": "IN", "Bulgaria": "BG",
       "Denmark": "DK", "Iran": "IR", "Singapore": "SG", "Armenia": "AM", "Greece": "GR", "Switzerland": "CH"}
PRECISION = {"site": "exact", "area": "city", "city": "city", "region": "region"}


def build(path="data/seed/documented.json", run_id=None):
    items = []
    for r in json.load(open(path, encoding="utf-8")):
        (sid, date, basis, date_note, place, country, lat, lon, prec, kind, target,
         sev, killed, wounded, _status, _note, summary, _n, sources) = r
        row = {
            "event_date": date, "event_date_note": date_note or None,
            "date_precision": "day" if basis == "incident" else "year",
            "lat": lat, "lon": lon, "geo_precision": PRECISION.get(prec, "city"),
            "place": place, "admin1": None, "country": ISO[country],
            "type": kind, "target": target, "severity": sev,
            "killed": killed, "wounded": wounded, "summary": summary,
            "tier": "documented", "basis": "incident", "status": "active",
            "agent": "seed", "run_id": run_id,
            "external_source": "seed", "external_key": sid,
            "inclusion_tag": "A", "inclusion_rule_version": db.RULE_VERSION,
            "inclusion_basis": "The cited source describes the act as directed at Jews or a Jewish site",
        }
        srcs = []
        for title, lang, url in sources:
            wiki = "wikipedia.org" in (url or "")
            srcs.append({"title": title, "url": url, "lang": lang,
                         "kind": "reference" if wiki else "news",
                         "evidence": "scholarly" if wiki else "press",
                         "publisher": "Wikipedia" if wiki else title})
        items.append({"row": row, "sources": srcs})
    return items


def main():
    items = build()
    print({"incidents": len(items), "source_links": sum(len(i["sources"]) for i in items)})
    if "--dry-run" in sys.argv:
        return
    run_id = db.start_run("seed")
    try:
        for i in items:
            i["row"]["run_id"] = run_id
        n = db.write_documented(items)
        db.finish_run(run_id, True, rows_written=n)
        print("written", n)
    except Exception as e:
        db.finish_run(run_id, False, error=str(e)[:2000])
        raise


if __name__ == "__main__":
    main()
