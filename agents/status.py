"""Write status/summary.json: what the archive holds and how recent runs went."""
import json
import os
import datetime
from lib import db


def main():
    out = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "incidents": {
            "total": db.count("incident"),
            "active": db.count("incident", status="active"),
            "candidate": db.count("incident", status="candidate"),
            "merged": db.count("incident", status="merged"),
            "documented": db.count("incident", tier="documented"),
            "register": db.count("incident", tier="register"),
            "country_level_only": db.count("incident", geo_precision="country"),
        },
        "by_origin": {s: db.count("incident", external_source=s) for s in ("fbi", "nypd", "seed", "research")},
        "cells": {s: db.count("cell", status=s) for s in ("pending", "claimed", "done", "blocked")},
        "review_queue_open": len(db.select("review_queue", select="id", resolved_at="is.null", limit=10000)),
        "recent_cells": db.select("cell", select="country,period_start,status,rows_written,notes",
                                  status="in.(done,blocked)", order="worked_at.desc.nullslast", limit=12),
        "sources": db.count("source"),
        "source_links": db.count("incident_source"),
        "recent_runs": db.select("agent_run", select="agent,started_at,finished_at,ok,rows_written,input_tokens,output_tokens,error",
                                 order="started_at.desc", limit=15),
    }
    os.makedirs("status", exist_ok=True)
    json.dump(out, open("status/summary.json", "w"), indent=1)
    print(json.dumps(out["incidents"]), json.dumps(out["by_origin"]))


if __name__ == "__main__":
    main()
