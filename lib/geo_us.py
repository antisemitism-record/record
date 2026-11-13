"""Place a US police agency on the map using the GeoNames gazetteer.

An agency name such as "Boston Police Department" resolves to its city,
a sheriff's office to its county, and a statewide body to the state.
The precision is recorded on every row.
"""
import io
import re
import unicodedata
import zipfile
import requests

GEONAMES = "https://download.geonames.org/export/dump/US.zip"

STRIP = [r"\bpolice department\b", r"\bpolice dept\b", r"\bpolice\b", r"\bsheriff s? office\b",
         r"\bsheriff s? department\b", r"\bdepartment of public safety\b", r"\bpublic safety\b",
         r"\bmetropolitan transportation authority\b", r"\bdepartment\b", r"\bborough\b", r"\btownship\b",
         r"\btown of\b", r"\bcity of\b", r"\bvillage of\b", r"\btwp\b", r"\bdivision\b", r"\bbureau\b",
         r"\bconsolidated\b", r"\bregional\b", r"\bdistrict\b", r"\bcampus\b"]


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\bst\.?\b", "saint", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


class Gazetteer:
    def __init__(self, path=None):
        self.places, self.counties, self.states = {}, {}, {}
        if path:
            fh = open(path, encoding="utf-8")
        else:
            z = zipfile.ZipFile(io.BytesIO(requests.get(GEONAMES, timeout=600).content))
            fh = io.TextIOWrapper(z.open("US.txt"), encoding="utf-8")
        with fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) < 15:
                    continue
                fcl, fcode, st = p[6], p[7], p[10]
                if fcl == "P":
                    try:
                        pop = int(p[14] or 0)
                    except ValueError:
                        pop = 0
                    lat, lon = float(p[4]), float(p[5])
                    for nm in [p[1]] + (p[3].split(",") if p[3] else []):
                        k = (norm(nm), st)
                        if k[0] and (k not in self.places or pop > self.places[k][2]):
                            self.places[k] = (lat, lon, pop)
                elif fcl == "A" and fcode == "ADM2":
                    name = norm(p[1]).replace(" county", "").replace(" parish", "").strip()
                    self.counties[(name, st)] = (float(p[4]), float(p[5]))
                elif fcl == "A" and fcode == "ADM1":
                    self.states[st] = (float(p[4]), float(p[5]))

    def locate(self, agency, state):
        """Return (lat, lon, precision) or None. Precision is city, region or state."""
        n = norm(agency.split(",")[0] if "," in agency else agency)
        is_county = "county" in n or "parish" in n
        for pat in STRIP:
            n = re.sub(pat, " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        if is_county:
            c = self.counties.get((n.replace(" county", "").replace(" parish", "").strip(), state))
            if c:
                return (c[0], c[1], "region")
        if n:
            parts = n.split()
            for cut in range(len(parts), 0, -1):
                key = (" ".join(parts[:cut]), state)
                if key in self.places:
                    return (self.places[key][0], self.places[key][1], "city")
                if key in self.counties:
                    return (self.counties[key][0], self.counties[key][1], "region")
        s = self.states.get(state)
        return (s[0], s[1], "state") if s else None
