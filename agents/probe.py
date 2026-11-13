"""Check which sources can be reached from the runner. Reads data/probe.txt, one URL per line."""
import re
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; AntisemitismRecord/0.1; +https://github.com/antisemitism-record/record)"}


def main():
    for url in open("data/probe.txt", encoding="utf-8").read().split():
        try:
            r = requests.get(url, headers=UA, timeout=40)
            body = re.sub(r"\s+", " ", r.text)[:260]
            total = r.headers.get("X-WP-Total", "")
            print(f"{r.status_code} len={len(r.text)} wp_total={total} {url[:110]}\n   {body}")
        except Exception as e:
            print(f"ERR {type(e).__name__} {url[:110]}")


if __name__ == "__main__":
    main()
