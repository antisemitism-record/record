"""Register agent: FBI hate crime master file.

Reads every incident US police agencies reported to the FBI with an
anti-Jewish bias finding, places it, and upserts it as a register row.
Re-running is safe: rows are keyed on the FBI incident id.

  python -m agents.register.fbi            write to the database
  python -m agents.register.fbi --dry-run  parse only, print a summary
"""
import csv
import io
import sys
import zipfile
import collections
import requests

from lib import db
from lib.geo_us import Gazetteer

SIGNED = "https://cde.ucr.cjis.gov/LATEST/s3/signedurl?key=additional-datasets/hate-crime/hate_crime.zip"
SOURCE_TITLE = "FBI Hate Crime Statistics, incident-level master file"
SOURCE_URL = "https://cde.ucr.cjis.gov/LATEST/webapp/#/pages/downloads"
STATE_FIX = {"NB": "NE"}

# First match wins, most serious first: (words to look for, type, severity, label)
OFFENCES = [
    (("murder", "manslaughter"), "murder", 5, "Murder"),
    (("rape", "sexual", "sodomy", "fondling"), "assault", 4, "Sexual assault"),
    (("aggravated assault",), "assault", 4, "Aggravated assault"),
    (("kidnap", "abduct"), "assault", 4, "Kidnapping"),
    (("arson",), "arson", 3, "Arson"),
    (("weapon",), "threat", 3, "Weapons offence"),
    (("simple assault",), "assault", 3, "Assault"),
    (("robbery",), "assault", 3, "Robbery"),
    (("burglary", "breaking"), "property", 2, "Burglary"),
    (("intimidation",), "threat", 2, "Intimidation"),
    (("vandalism", "destruction"), "vandalism", 1, "Vandalism"),
    (("larceny", "theft", "stolen", "shoplift", "embezzl", "fraud", "extortion"), "property", 2, "Theft"),
]

TARGETS = {
    "Church/Synagogue/Temple/Mosque": "synagogue", "School/College": "school",
    "School-Elementary/Secondary": "school", "School-College/University": "school",
    "Residence/Home": "home", "Commercial/Office Building": "business", "Restaurant": "business",
    "Specialty Store": "business", "Grocery/Supermarket": "business", "Community Center": "community building",
    "Daycare Facility": "school",
}


def classify(offence):
    o = offence.lower()
    for words, kind, sev, label in OFFENCES:
        if any(w in o for w in words):
            return kind, sev, label
    return "harassment", 1, "Other offence"


def download():
    signed = requests.get(SIGNED, timeout=60).json()
    url = next(iter(signed.values()))
    z = zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=600).content))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    return io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace", newline="")


def build_rows(fh, gaz, run_id=None):
    rows, stats = [], collections.Counter()
    for d in csv.DictReader(fh):
        if "Jewish" not in d["bias_desc"]:
            continue
        state = STATE_FIX.get(d["state_abbr"], d["state_abbr"])
        agency = d["pug_agency_name"].rstrip(":, ")
        where = gaz.locate(agency, state)
        if not where:
            stats["unplaced"] += 1
            continue
        kind, sev, label = classify(d["offense_name"])
        loc = d["location_name"]
        other_bias = [b for b in d["bias_desc"].split(";") if "Jewish" not in b]
        summary = f"{label} reported by {agency} ({state}) to the FBI with an anti-Jewish bias finding."
        if loc and loc != "Other/Unknown":
            summary += f" Location type: {loc}."
        if other_bias:
            summary += f" Also recorded: {', '.join(other_bias)}."
        rows.append({
            "event_date": d["incident_date"], "date_precision": "day",
            "lat": round(where[0], 4), "lon": round(where[1], 4), "geo_precision": where[2],
            "place": agency, "admin1": d["state_name"], "country": "US",
            "type": kind, "target": TARGETS.get(loc), "severity": sev, "summary": summary,
            "tier": "register", "basis": "incident", "status": "active",
            "agent": "register", "run_id": run_id,
            "external_source": "fbi", "external_key": d["incident_id"],
            "inclusion_tag": "A", "inclusion_rule_version": db.RULE_VERSION,
            "inclusion_basis": "Police recorded an anti-Jewish bias motivation",
        })
        stats[where[2]] += 1
        stats["year_" + d["data_year"]] += 1
    return rows, stats


def main():
    dry = "--dry-run" in sys.argv
    local = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--csv=")), None)
    geo = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--geonames=")), None)
    run_id = None if dry else db.start_run("register:fbi")
    try:
        gaz = Gazetteer(geo)
        fh = open(local, newline="", encoding="utf-8", errors="replace") if local else download()
        rows, stats = build_rows(fh, gaz, run_id)
        years = sorted(k[5:] for k in stats if k.startswith("year_"))
        summary = {"rows": len(rows), "unplaced": stats["unplaced"], "city": stats["city"],
                   "region": stats["region"], "state": stats["state"],
                   "first_year": years[0], "last_year": years[-1]}
        print(summary)
        if dry:
            return
        src = db.source(SOURCE_TITLE, SOURCE_URL, "dataset", publisher="Federal Bureau of Investigation")
        written = db.write_register_rows(rows, src)
        db.finish_run(run_id, True, rows_written=written, log=summary)
        print("written", written)
    except Exception as e:
        if run_id:
            db.finish_run(run_id, False, error=str(e)[:2000])
        raise


if __name__ == "__main__":
    main()
