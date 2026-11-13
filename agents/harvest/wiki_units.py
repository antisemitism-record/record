"""Queue Wikipedia pages about antisemitic incidents, in every language.

Three ways in: the timeline and list pages, every article Wikidata classes
as a pogrom or as an attack whose target was Jews, and the English category
trees. For each page found, every other language version is queued too.
Units live in the cell table with region "wiki|<lang>|<title>".
"""
import time
import requests
from lib import db

UA = {"User-Agent": "AntisemitismRecord/0.1 (https://github.com/antisemitism-record/record)"}
SEEDS = ["Timeline of antisemitism in the 20th century", "Timeline of antisemitism in the 21st century",
         "Timeline of antisemitism in the 19th century", "Timeline of antisemitism", "Pogrom",
         "List of attacks on Jewish institutions", "List of attacks on Jewish institutions in the United States",
         "History of antisemitism", "Antisemitism in Europe", "Antisemitism in the Arab world",
         "Antisemitism in the United States", "History of antisemitism in the United States",
         "Antisemitism in the Soviet Union", "Antisemitism in Russia", "Antisemitism in Poland",
         "Antisemitism in France", "Antisemitism in the United Kingdom", "Antisemitism in Germany",
         "Antisemitism in Canada", "Antisemitism in Australia", "Antisemitism in Argentina",
         "Antisemitism in Turkey", "Antisemitism in Ukraine", "Antisemitism in Hungary", "Antisemitism in Romania",
         "Antisemitism in Sweden", "Antisemitism in Belgium", "Antisemitism in the Netherlands",
         "Antisemitism in Italy", "Antisemitism in Greece", "Antisemitism in Spain", "Antisemitism in South Africa",
         "Antisemitism in Venezuela", "Antisemitism in Iran", "Antisemitism in Iraq", "Antisemitism in Egypt",
         "Jewish exodus from the Muslim world", "Blood libel", "Pogroms during the Russian Civil War",
         "Anti-Jewish violence in Poland, 1944–1946", "Anti-Jewish violence in Central and Eastern Europe, 1944–1946",
         "Palestinian political violence", "List of Palestinian suicide attacks",
         "List of Israeli civilian casualties in the Second Intifada"]
CATEGORIES = ["Antisemitic attacks and incidents", "Pogroms", "Massacres of Jews", "Blood libel",
              "Attacks on synagogues and Jewish communal organizations", "Anti-Jewish pogroms by Muslims",
              "Antisemitic attacks and incidents in Europe", "Antisemitic attacks and incidents in the United States",
              "Terrorist attacks against Israelis", "Antisemitism in Europe", "20th-century attacks on synagogues and Jewish communal organizations",
              "21st-century attacks on synagogues and Jewish communal organizations"]
SPARQL = """SELECT DISTINCT ?article WHERE {
  { ?item wdt:P31/wdt:P279* wd:Q177716 } UNION { ?item wdt:P533 wd:Q7325 }
  ?article schema:about ?item . FILTER(CONTAINS(STR(?article), ".wikipedia.org/wiki/")) }"""


def api(lang, **params):
    for attempt in range(4):
        r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={"format": "json", **params},
                         headers=UA, timeout=60)
        if r.status_code == 200:
            return r.json()
        time.sleep(3 * (attempt + 1))
    return {}


def category_articles(root, depth=3, cap=4000):
    seen_c, todo, arts = {root}, [(root, 0)], set()
    while todo and len(arts) < cap:
        cat, d = todo.pop()
        cont = {}
        while True:
            j = api("en", action="query", list="categorymembers", cmtitle="Category:" + cat, cmlimit=500,
                    cmtype="page|subcat", **cont)
            for m in j.get("query", {}).get("categorymembers", []):
                if m["ns"] == 0:
                    arts.add(m["title"])
                elif m["ns"] == 14 and d < depth:
                    c = m["title"].split(":", 1)[1]
                    if c not in seen_c:
                        seen_c.add(c)
                        todo.append((c, d + 1))
            cont = j.get("continue") or {}
            if not cont:
                break
    return arts


def langlinks(titles):
    out = set()
    titles = sorted(titles)
    for i in range(0, len(titles), 40):
        cont = {}
        while True:
            j = api("en", action="query", prop="langlinks", lllimit=500, titles="|".join(titles[i:i + 40]), **cont)
            for p in j.get("query", {}).get("pages", {}).values():
                for ll in p.get("langlinks", []):
                    out.add((ll["lang"], ll["*"]))
            cont = j.get("continue") or {}
            if not cont:
                break
    return out


def main():
    en = set(SEEDS)
    for c in CATEGORIES:
        en |= category_articles(c)
    pages = {("en", t) for t in en} | langlinks(en)
    try:
        r = requests.get("https://query.wikidata.org/sparql", params={"query": SPARQL, "format": "json"},
                         headers=UA, timeout=180)
        for b in r.json()["results"]["bindings"]:
            url = b["article"]["value"]
            lang = url.split("//")[1].split(".")[0]
            pages.add((lang, requests.utils.unquote(url.split("/wiki/", 1)[1]).replace("_", " ")))
    except Exception as e:
        print("wikidata skipped:", type(e).__name__)
    rows = []
    for lang, title in sorted(pages):
        if len(lang) > 12 or len(title) > 200:
            continue
        # English first, then the large Jewish-history languages, then the rest.
        pr = 10 if lang == "en" else 20 if lang in ("he", "de", "fr", "pl", "ru", "uk", "es", "it", "hu", "yi") else 30
        rows.append({"country": None, "region": f"wiki|{lang}|{title}", "period_start": 0, "period_end": 0,
                     "granularity": "page", "priority": pr})
    for i in range(0, len(rows), 500):
        db._request("POST", "cell", params={"on_conflict": "country,region,period_start,period_end"},
                    json=rows[i:i + 500], prefer="resolution=ignore-duplicates,return=minimal")
    # The timeline and overview pages hold the most incidents: read them first.
    for t in SEEDS:
        db._request("PATCH", "cell", params={"region": f"eq.wiki|en|{t}", "status": "eq.pending"},
                    json={"priority": 1}, prefer="return=minimal")
    langs = {l for l, _ in pages}
    print({"english_pages": len(en), "pages_all_languages": len(rows), "languages": len(langs),
           "queued_in_database": db.count("cell", granularity="page")})


if __name__ == "__main__":
    main()
