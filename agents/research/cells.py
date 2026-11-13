"""Create the backfill queue: one cell per country per decade.

Safe to re-run: existing cells are left exactly as they are.
"""
from lib import db

COUNTRIES = {
    "US": "United States", "CA": "Canada", "MX": "Mexico", "AR": "Argentina", "BR": "Brazil", "CL": "Chile",
    "UY": "Uruguay", "VE": "Venezuela", "CO": "Colombia", "PE": "Peru", "CU": "Cuba", "PA": "Panama",
    "GB": "United Kingdom", "IE": "Ireland", "FR": "France", "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
    "BE": "Belgium", "NL": "Netherlands", "LU": "Luxembourg", "ES": "Spain", "PT": "Portugal", "IT": "Italy",
    "GR": "Greece", "DK": "Denmark", "SE": "Sweden", "NO": "Norway", "FI": "Finland", "PL": "Poland",
    "CZ": "Czechia", "SK": "Slovakia", "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "RS": "Serbia",
    "HR": "Croatia", "BA": "Bosnia and Herzegovina", "MK": "North Macedonia", "SI": "Slovenia",
    "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia", "BY": "Belarus", "UA": "Ukraine", "MD": "Moldova",
    "RU": "Russia", "GE": "Georgia", "AM": "Armenia", "AZ": "Azerbaijan", "KZ": "Kazakhstan", "UZ": "Uzbekistan",
    "TR": "Turkey", "IL": "Israel", "PS": "West Bank and Gaza", "LB": "Lebanon", "SY": "Syria", "IQ": "Iraq",
    "IR": "Iran", "YE": "Yemen", "EG": "Egypt", "LY": "Libya", "TN": "Tunisia", "DZ": "Algeria", "MA": "Morocco",
    "ET": "Ethiopia", "ZA": "South Africa", "IN": "India", "CN": "China", "JP": "Japan", "AU": "Australia",
    "NZ": "New Zealand", "AF": "Afghanistan", "BH": "Bahrain", "SA": "Saudi Arabia", "JO": "Jordan",
    "SG": "Singapore", "KE": "Kenya",
}
FIRST = ["FR", "DE", "GB", "BE", "NL", "PL", "UA", "RU", "HU", "RO", "AT", "IT", "AR", "CA", "AU", "US", "IL"]


def main():
    rows = []
    for code in COUNTRIES:
        for start in range(1900, 2030, 10):
            # Lower number runs sooner: priority countries first, recent decades before old ones.
            pr = (0 if code in FIRST else 200) + (2020 - start) // 10 * 5 + (FIRST.index(code) if code in FIRST else 0)
            rows.append({"country": code, "region": None, "period_start": start, "period_end": start + 9,
                         "granularity": "decade", "priority": pr})
    for i in range(0, len(rows), 500):
        db._request("POST", "cell", params={"on_conflict": "country,region,period_start,period_end"},
                    json=rows[i:i + 500], prefer="resolution=ignore-duplicates,return=minimal")
    print({"cells_defined": len(rows), "cells_in_database": db.count("cell")})


if __name__ == "__main__":
    main()
