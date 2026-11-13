"""Register agent: NYPD hate crime complaints with an anti-Jewish motive.

New York also reports to the FBI, so to count nothing twice this agent
only loads complaints dated after the last day the FBI file covers.
"""
import sys
import requests
from lib import db

API = "https://data.cityofnewyork.us/resource/bqiq-cu78.json"
SOURCE_TITLE = "NYPD Hate Crimes, NYC Open Data"
SOURCE_URL = "https://data.cityofnewyork.us/Public-Safety/NYPD-Hate-Crimes/bqiq-cu78"
BOROUGH = {"KINGS": ("Brooklyn", 40.6782, -73.9442), "NEW YORK": ("Manhattan", 40.7831, -73.9712),
           "QUEENS": ("Queens", 40.7282, -73.7949), "BRONX": ("Bronx", 40.8448, -73.8648),
           "RICHMOND": ("Staten Island", 40.5795, -74.1502)}
CITY = ("New York City", 40.7128, -74.0060)


def classify(text):
    t = text.lower()
    if "murder" in t:
        return "murder", 5, "Murder"
    if "felony assault" in t or "assault 1" in t or "assault 2" in t:
        return "assault", 4, "Felony assault"
    if "assault" in t:
        return "assault", 3, "Assault"
    if "robbery" in t:
        return "assault", 3, "Robbery"
    if "arson" in t:
        return "arson", 3, "Arson"
    if "menacing" in t or "harassment" in t or "threat" in t:
        return "threat", 2, "Aggravated harassment"
    if "graffiti" in t or "mischief" in t:
        return "vandalism", 1, "Criminal mischief"
    if "burglary" in t or "larceny" in t:
        return "property", 2, "Burglary or theft"
    return "harassment", 1, "Other offence"


def fbi_last_day():
    r = db.select("incident", select="event_date", external_source="eq.fbi", order="event_date.desc", limit=1)
    return r[0]["event_date"] if r else "1900-01-01"


def build(after, run_id=None):
    data = requests.get(API, params={"$where": "bias_motive_description='ANTI-JEWISH'", "$limit": 50000},
                        timeout=120).json()
    rows, seen = [], set()
    for d in data:
        day = d["record_create_date"][:10]
        key = d["full_complaint_id"]
        if day <= after or key in seen:
            continue
        seen.add(key)
        name, lat, lon = BOROUGH.get(d.get("county", "").upper(), CITY)
        precision = "region" if d.get("county", "").upper() in BOROUGH else "city"
        offence = d.get("pd_code_description") or d.get("offense_description") or ""
        kind, sev, label = classify(offence)
        precinct = d.get("complaint_precinct_code")
        rows.append({
            "event_date": day, "date_precision": "day", "event_date_note": "Date the complaint was recorded",
            "lat": lat, "lon": lon, "geo_precision": precision,
            "place": f"{name}, precinct {precinct}" if precinct else name,
            "admin1": "New York", "country": "US",
            "type": kind, "target": None, "severity": sev,
            "summary": f"{label} complaint recorded by the NYPD with an anti-Jewish bias motive. "
                       f"Offence as recorded: {offence.title()}.",
            "tier": "register", "basis": "incident", "status": "active",
            "agent": "register", "run_id": run_id,
            "external_source": "nypd", "external_key": key,
            "inclusion_tag": "A", "inclusion_rule_version": db.RULE_VERSION,
            "inclusion_basis": "Police recorded an anti-Jewish bias motivation",
        })
    return rows


def main():
    run_id = db.start_run("register:nypd")
    try:
        after = fbi_last_day()
        rows = build(after, run_id)
        print({"after": after, "rows": len(rows)})
        src = db.source(SOURCE_TITLE, SOURCE_URL, "dataset", publisher="New York City Police Department")
        n = db.write_register_rows(rows, src)
        db.finish_run(run_id, True, rows_written=n, log={"after": after})
        print("written", n)
    except Exception as e:
        db.finish_run(run_id, False, error=str(e)[:2000])
        raise


if __name__ == "__main__":
    main()
