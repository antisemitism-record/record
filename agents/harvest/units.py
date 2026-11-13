"""Create the work queue for source harvesters: one unit per source per month.

Units live in the cell table with region set to the source name and the
period written as YYYYMM. Safe to re-run: existing units are left alone.
"""
import datetime
from lib import db

JTA_FIRST = (1923, 1)


def main():
    today = datetime.date.today()
    rows, y, m = [], *JTA_FIRST
    while (y, m) < (today.year, today.month):
        ym = y * 100 + m
        # Lower runs sooner. Oldest first, so the archive fills from 1923 forward.
        rows.append({"country": None, "region": "jta", "period_start": ym, "period_end": ym,
                     "granularity": "source-month", "priority": ym - 192300})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for i in range(0, len(rows), 500):
        db._request("POST", "cell", params={"on_conflict": "country,region,period_start,period_end"},
                    json=rows[i:i + 500], prefer="resolution=ignore-duplicates,return=minimal")
    print({"jta_months_defined": len(rows), "jta_months_in_database": db.count("cell", region="jta")})


if __name__ == "__main__":
    main()
