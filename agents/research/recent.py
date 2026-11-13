"""Live worker: look for incidents reported in the last few weeks.

Each day it takes the next few countries in rotation and searches for
incidents from the trailing window, so late reports are still caught.

  python -m agents.research.recent countries=6 days=21
"""
import sys
import datetime
from lib import db
from agents.research.cells import COUNTRIES, FIRST
from agents.research.worker import ask, store


def main():
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    n, days = int(args.get("countries", 6)), int(args.get("days", 21))
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days)
    order = FIRST + [c for c in COUNTRIES if c not in FIRST]
    if "only" in args:
        todo = [c for c in args["only"].upper().split(",") if c in COUNTRIES]
    else:
        at = (today.toordinal() * n) % len(order)
        todo = [order[(at + i) % len(order)] for i in range(n)]
    run_id = db.start_run("recent")
    total = {"countries": 0, "rows": 0, "live": 0, "held": 0, "input_tokens": 0, "output_tokens": 0, "searches": 0}
    try:
        for code in todo:
            try:
                found, use = ask(COUNTRIES[code], start.strftime("%d %B %Y"), today.strftime("%d %B %Y"), 20, 8)
                written, held, _dropped = store(found, code, run_id)
                total["countries"] += 1
                total["rows"] += written
                total["live"] += written - held
                total["held"] += held
                for k in ("input_tokens", "output_tokens", "searches"):
                    total[k] += use[k]
                print(COUNTRIES[code], "->", len(found), "proposed,", written, "written,", held, "held")
            except Exception as e:
                print(COUNTRIES[code], "-> FAILED", str(e)[:300])
        db.finish_run(run_id, True, rows_written=total["rows"], log=total,
                      input_tokens=total["input_tokens"], output_tokens=total["output_tokens"])
        print(total)
    except Exception as e:
        db.finish_run(run_id, False, error=str(e)[:2000])
        raise


if __name__ == "__main__":
    main()
