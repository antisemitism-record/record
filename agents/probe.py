"""Check how the JTA archive can be read from the runner."""
import re
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; AntisemitismRecord/0.1; +https://github.com/antisemitism-record/record)"}
B = "https://www.jta.org/wp-json/wp/v2/"


def get(url):
    return requests.get(url, headers=UA, timeout=40)


def main():
    types = get(B + "types").json()
    print("types:", {k: v.get("rest_base") for k, v in types.items()})
    for k, v in types.items():
        base = v.get("rest_base")
        if not base or k in ("attachment", "wp_block", "wp_template", "wp_template_part", "wp_navigation", "nav_menu_item"):
            continue
        for rng in ("after=1933-04-01T00:00:00&before=1933-04-03T00:00:00", "after=1975-06-01T00:00:00&before=1975-06-03T00:00:00"):
            r = get(f"{B}{base}?per_page=2&{rng}&_fields=id,date,link,title")
            print(k, rng[6:16], r.status_code, "total=" + r.headers.get("X-WP-Total", "?"), re.sub(r"\s+", " ", r.text)[:330])
    r = get("https://www.jta.org/archive")
    links = re.findall(r'href="(https://www\.jta\.org/[^"]*archive[^"]*)"', r.text)
    print("archive links:", list(dict.fromkeys(links))[:25])
    r = get("https://www.jta.org/?s=pogrom&post_type=archive")
    print("search", r.status_code, list(dict.fromkeys(re.findall(r'href="(https://www\.jta\.org/(?:archive|\d{4})/[^"]+)"', r.text)))[:8])


if __name__ == "__main__":
    main()
