"""Place a town on the map from its name and modern country, using GeoNames.

Historical and foreign-language names are matched through the gazetteer's
alternate names. If the town is not found, the incident is kept at country
level; nothing is dropped for lack of a location.
"""
import io
import re
import unicodedata
import zipfile
import collections
import requests

CITIES = "https://download.geonames.org/export/dump/cities500.zip"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


class World:
    def __init__(self, path=None):
        self.places = {}
        sums = collections.defaultdict(lambda: [0.0, 0.0, 0])
        if path:
            fh = open(path, encoding="utf-8")
        else:
            z = zipfile.ZipFile(io.BytesIO(requests.get(CITIES, timeout=600).content))
            fh = io.TextIOWrapper(z.open("cities500.txt"), encoding="utf-8")
        with fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) < 15:
                    continue
                cc, lat, lon = p[8], float(p[4]), float(p[5])
                pop = int(p[14] or 0)
                w = max(pop, 1)
                s = sums[cc]
                s[0] += lat * w
                s[1] += lon * w
                s[2] += w
                for nm in {p[1], p[2], *p[3].split(",")}:
                    k = (norm(nm), cc)
                    if k[0] and (k not in self.places or pop > self.places[k][2]):
                        self.places[k] = (lat, lon, pop)
        self.centres = {cc: (s[0] / s[2], s[1] / s[2]) for cc, s in sums.items()}

    def locate(self, place, country):
        """Return (lat, lon, precision). Precision is city or country; (None, None, 'country') if unknown."""
        n = norm(place)
        for cand in filter(None, [n, n.split(" near ")[-1], re.sub(r"\b(city|town|village|district) of\b", "", n).strip(),
                                  n.split(",")[0] if "," in (place or "") else "", norm((place or "").split("(")[0])]):
            hit = self.places.get((cand, country))
            if hit:
                return round(hit[0], 4), round(hit[1], 4), "city"
        c = self.centres.get(country)
        return (round(c[0], 4), round(c[1], 4), "country") if c else (None, None, "country")
