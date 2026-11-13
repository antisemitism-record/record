"""Put blocked and abandoned units back in the queue so they are tried again."""
import datetime
from lib import db


def main():
    stale = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=7)).isoformat()
    a = db._request("PATCH", "cell", params={"status": "eq.blocked", "region": "not.is.null", "select": "id"},
                    json={"status": "pending", "claimed_by": None, "notes": None}, prefer="return=representation")
    b = db._request("PATCH", "cell", params={"status": "eq.claimed", "claimed_at": f"lt.{stale}", "select": "id"},
                    json={"status": "pending", "claimed_by": None}, prefer="return=representation")
    print({"blocked_requeued": len(a), "abandoned_requeued": len(b)})


if __name__ == "__main__":
    main()
