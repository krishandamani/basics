#!/usr/bin/env python3
"""Download Ofsted school ratings from GIAS for target postcode districts.

Run this from your local machine (requires internet access to .gov.uk):

    cd property-hunter
    python scripts/fetch_ofsted_data.py

It writes src/data/ofsted_by_district.json which is committed and used
by the enricher on Railway (where .gov.uk is DNS-blocked).

Refresh every few months to pick up new Ofsted inspections.
"""

import json
import os
import re
import time
from pathlib import Path

import requests

# All postcode districts covered by the commute table + surrounding areas
TARGET_DISTRICTS = [
    "AL1", "AL2", "AL3", "AL4", "AL5", "AL6", "AL7", "AL8", "AL9", "AL10",
    "SG1", "SG2", "SG3", "SG4", "SG5", "SG6", "SG7", "SG8", "SG9",
    "EN1", "EN2", "EN3", "EN4", "EN5", "EN6", "EN7", "EN8", "EN10", "EN11",
    "WD3", "WD4", "WD5", "WD6", "WD7", "WD17", "WD18", "WD19", "WD23", "WD24", "WD25",
    "HP1", "HP2", "HP3", "HP4", "HP5", "HP6", "HP7", "HP8",
    "HA1", "HA2", "HA3", "HA4", "HA5", "HA6", "HA7", "HA8",
    "LU1", "LU2", "LU3", "LU4", "LU5", "LU6", "LU7",
    "MK1", "MK2", "MK3", "MK4", "MK5", "MK6", "MK7", "MK8", "MK9",
    "MK10", "MK11", "MK12", "MK13", "MK14", "MK15", "MK16", "MK17", "MK18",
    "OX1", "OX2", "OX3", "OX4", "OX5", "OX7", "OX9",
    "RG1", "RG2", "RG4", "RG5", "RG6", "RG7", "RG8", "RG9", "RG10",
    "HP9", "HP10", "HP11", "HP12", "HP13", "HP14", "HP15", "HP16",
    "GU1", "GU2", "GU3", "GU4", "GU5",
    "KT1", "KT2", "KT3", "KT4", "KT5", "KT6",
    "TW1", "TW2", "TW3", "TW4", "TW5",
    "SW1", "SW2", "SW3", "SW4", "SW12", "SW13", "SW14", "SW15", "SW16", "SW17",
    "SE1", "SE2", "SE3", "SE4", "SE5", "SE6",
    "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N10", "N11", "N12", "N13",
    "N14", "N17", "N20", "N21",
    "NW1", "NW2", "NW3", "NW4", "NW5", "NW6", "NW7", "NW8", "NW9", "NW10", "NW11",
]

_OFSTED_LABELS = {"1": "Outstanding", "2": "Good", "3": "Requires improvement", "4": "Inadequate"}
_GIAS_URL = "https://api.get-information-about-schools.service.gov.uk/api/establishments"

OUT_FILE = Path(__file__).parent.parent / "src" / "ofsted_by_district.json"


def _proxies():
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        return None
    proxy_url = f"http://groups-RESIDENTIAL:{api_key}@proxy.apify.com:8000"
    return {"http": proxy_url, "https": proxy_url}


def fetch_district(district: str) -> list:
    proxies = _proxies()
    try:
        r = requests.get(
            _GIAS_URL,
            params={"nearestToPostCode": district, "radiusInMiles": 2},
            headers={"Accept": "application/json"},
            proxies=proxies,
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  {district}: HTTP {r.status_code}")
            return []
        data = r.json()
        if isinstance(data, dict):
            for k in ("Establishments", "establishments", "data", "results", "items"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
            else:
                data = []
        schools = []
        for s in (data or []):
            name = s.get("EstablishmentName", "")
            if not name:
                continue
            ofsted_raw = s.get("OfstedRating") or ""
            rc = (
                str(ofsted_raw.get("code", "") or ofsted_raw.get("value", ""))
                if isinstance(ofsted_raw, dict)
                else str(ofsted_raw)
            )
            rating = _OFSTED_LABELS.get(rc, "Not rated")
            phase_raw = s.get("PhaseOfEducation") or {}
            ps = (
                phase_raw.get("displayName", "") or phase_raw.get("value", "")
                if isinstance(phase_raw, dict)
                else str(phase_raw)
            )
            pl = ps.lower()
            phase = (
                "Primary" if any(w in pl for w in ("primary", "infant", "junior"))
                else "Secondary" if any(w in pl for w in ("secondary", "through"))
                else ps or "Other"
            )
            pc = s.get("Postcode", "") or ""
            district_key = re.sub(r"\s+", "", pc).upper()[:-3] if len(re.sub(r"\s+", "", pc)) >= 5 else district
            schools.append({
                "name": name,
                "rating": rating,
                "phase": phase,
                "urn": str(s.get("URN", "") or ""),
                "postcode": pc,
                "district": district_key or district,
            })
        return schools
    except Exception as exc:
        print(f"  {district}: {exc}")
        return []


def main():
    print(f"Fetching Ofsted data for {len(TARGET_DISTRICTS)} districts...")
    by_district: dict = {}
    seen_urns: set = set()

    for i, dist in enumerate(TARGET_DISTRICTS):
        print(f"  [{i+1}/{len(TARGET_DISTRICTS)}] {dist} ...", end=" ", flush=True)
        schools = fetch_district(dist)
        added = 0
        for s in schools:
            if s["urn"] and s["urn"] in seen_urns:
                continue
            if s["urn"]:
                seen_urns.add(s["urn"])
            key = s["district"] or dist
            by_district.setdefault(key, [])
            # Don't duplicate by name within same district
            if not any(x["name"] == s["name"] for x in by_district[key]):
                by_district[key].append({k: v for k, v in s.items() if k != "district"})
                added += 1
        print(f"{added} new schools")
        time.sleep(0.3)  # be polite

    total = sum(len(v) for v in by_district.values())
    outstanding = sum(1 for v in by_district.values() for s in v if s["rating"] == "Outstanding")
    print(f"\nTotal: {total} schools across {len(by_district)} districts ({outstanding} Outstanding)")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(by_district, indent=2, ensure_ascii=False))
    print(f"Saved → {OUT_FILE}")
    print("\nNow commit and push:")
    print("  git add property-hunter/src/data/ofsted_by_district.json")
    print("  git commit -m 'Add Ofsted school ratings data'")
    print("  git push origin main && git push origin main:claude/property-search-automation-DJjr9")


if __name__ == "__main__":
    main()
