"""Optional enrichment — adds crime stats, EPC ratings, nearest school,
and nearest railway station to matched properties. All sources are free
public UK APIs. Each enrichment is independent; failures are silent.
"""

import math
import re
import requests

from .models import Property

_TIMEOUT = 8  # seconds per API call


def _get_lat_lng(postcode: str):
    """Look up latitude/longitude from a UK postcode using postcodes.io (free, no key)."""
    try:
        clean = re.sub(r"\s+", "", postcode).upper()
        r = requests.get(f"https://api.postcodes.io/postcodes/{clean}", timeout=_TIMEOUT)
        if r.status_code == 200:
            data = r.json().get("result", {})
            return data.get("latitude"), data.get("longitude")
    except Exception:
        pass
    return None, None


def _reverse_geocode(lat: float, lng: float) -> Optional[str]:
    """Reverse geocode lat/lng to a UK postcode via postcodes.io."""
    try:
        r = requests.get(
            "https://api.postcodes.io/postcodes",
            params={"lon": lng, "lat": lat, "limit": 1},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            results = r.json().get("result") or []
            if results:
                return results[0].get("postcode")
    except Exception:
        pass
    return None


def _resolve_lat_lng(prop: "Property"):
    """Return (lat, lng) for a property: use stored coords, else geocode postcode."""
    if prop.lat and prop.lng:
        return prop.lat, prop.lng
    if prop.postcode:
        return _get_lat_lng(prop.postcode)
    return None, None


def _enrich_crime(prop: Property) -> Property:
    """Add a Low/Medium/High crime label using the police.uk public API."""
    lat, lng = _resolve_lat_lng(prop)
    if not lat:
        return prop
    try:
        r = requests.get(
            "https://data.police.uk/api/crimes-street/all-crime",
            params={"lat": lat, "lng": lng},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            count = len(r.json())
            prop.crime_rate = "Low" if count < 20 else ("Medium" if count < 50 else "High")
    except Exception:
        pass
    return prop


def _enrich_epc(prop: Property) -> Property:
    """Add EPC energy rating using the property-shared library (if installed)."""
    if not prop.postcode:
        return prop
    try:
        from property_core import epc_service  # type: ignore

        results = epc_service.get_by_postcode(prop.postcode)
        if results:
            prop.epc_rating = results[0].current_energy_rating
    except Exception:
        pass
    return prop


def _enrich_school(prop: Property) -> Property:
    """Find the nearest Good/Outstanding Ofsted-rated school within 1 mile."""
    postcode = prop.postcode
    if not postcode:
        if prop.lat and prop.lng:
            postcode = _reverse_geocode(prop.lat, prop.lng)
        if not postcode:
            return prop
    try:
        clean = re.sub(r"\s+", "", postcode).upper()
        # OfstedRating: 1=Outstanding, 2=Good
        r = requests.get(
            "https://api.get-information-about-schools.service.gov.uk/api/establishments",
            params={
                "nearestToPostCode": clean,
                "radiusInMiles": 1,
                "ofstedRating[]": ["1", "2"],
                "status": "Open",
                "fields": "EstablishmentName,OfstedRating",
            },
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            schools = r.json()
            if schools:
                first = schools[0]
                name = first.get("EstablishmentName", "")
                rating_code = str(first.get("OfstedRating", ""))
                rating_label = {"1": "Outstanding", "2": "Good"}.get(rating_code, "")
                if name and rating_label:
                    prop.nearest_school = name
                    prop.school_rating = rating_label
    except Exception:
        pass
    return prop


# Fastest realistic peak-time train to central London (minutes), by postcode district.
# Uses the best available service (not necessarily the most frequent).
_COMMUTE_BY_DISTRICT: dict = {
    # St Albans — Thameslink to City Thameslink / St Pancras (~18-22 min fast)
    "AL1": 20, "AL2": 22, "AL3": 23, "AL4": 22,
    # Welwyn Garden City / Hatfield — Thameslink to King's Cross (~24-27 min fast)
    "AL7": 26, "AL8": 26, "AL9": 25, "AL10": 25,
    # Hitchin — Thameslink to King's Cross (~28-34 min fast)
    "SG4": 32, "SG5": 32,
    # Potters Bar — Thameslink to King's Cross (~16-20 min)
    "EN6 3": 18,
    # Cuffley / Goffs Oak — Great Northern to Moorgate (~38-45 min)
    "EN6 4": 40, "EN6 5": 42, "EN6": 30,  # EN6 fallback (Potters Bar/Cuffley mixed)
    # Amersham — Chiltern Railways to Marylebone (~37-42 min, faster than Met line)
    "HP6": 40, "HP7": 43,
    # Northwood — Metropolitan line to Baker Street (~28-32 min)
    "HA6": 30,
    # Pinner — Metropolitan line to Baker Street (~26-30 min)
    "HA5": 28,
    # Bushey / Watford — London Overground/Avanti to Euston (~18-22 min)
    "WD23": 22, "WD18": 20, "WD17": 20,
    # Croxley Green / Chorleywood — Watford Junction to Euston (~30-38 min with connection)
    "WD3": 35, "WD4": 32, "WD25": 28,
    # Radlett / Borehamwood (Thameslink)
    "WD7": 22, "WD6": 25,
}


def _enrich_commute(prop: Property) -> Property:
    """Estimate train commute time to central London by postcode district."""
    if prop.commute_minutes:
        return prop

    postcode = prop.postcode
    if not postcode:
        if prop.lat and prop.lng:
            postcode = _reverse_geocode(prop.lat, prop.lng)
            if postcode:
                prop.postcode = postcode  # cache it so other enrichers can use it
        if not postcode:
            return prop

    clean = re.sub(r"\s+", "", postcode).upper()
    # Try sector first (e.g. "EN6 3"), then district (e.g. "EN6")
    sector   = clean[:-2] if len(clean) >= 6 else clean   # e.g. "EN63"
    sector_s = clean[:-2].rstrip("0123456789") + " " + clean[-3] if len(clean) >= 6 else ""
    district = clean[:-3] if len(clean) >= 5 else clean   # e.g. "EN6"

    # Normalise sector to "XX9 9" form for lookup
    sector_key = f"{district} {clean[-3]}" if len(clean) >= 5 else district

    minutes = (
        _COMMUTE_BY_DISTRICT.get(sector_key)
        or _COMMUTE_BY_DISTRICT.get(district)
    )
    if minutes:
        prop.commute_minutes = minutes
    return prop


def _haversine_miles(lat1, lng1, lat2, lng2) -> float:
    R = 3958.8  # Earth radius in miles
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _enrich_station(prop: Property) -> Property:
    """Find the nearest UK railway station via OpenStreetMap Overpass API (free, no key)."""
    if prop.nearest_station:
        return prop
    lat, lng = _resolve_lat_lng(prop)
    if not lat:
        return prop
    try:
        query = (
            f"[out:json][timeout:6];"
            f"node(around:3000,{lat},{lng})[railway=station][!'subway'];out body;"
        )
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=8,
        )
        if r.status_code != 200:
            return prop
        elements = r.json().get("elements", [])
        if not elements:
            return prop

        best = min(
            elements,
            key=lambda e: _haversine_miles(lat, lng, e["lat"], e["lon"]),
        )
        dist = _haversine_miles(lat, lng, best["lat"], best["lon"])
        name = best.get("tags", {}).get("name", "")
        if name and dist < 5:
            prop.nearest_station = name
            prop.station_distance_miles = round(dist, 1)
    except Exception:
        pass
    return prop


def enrich(prop: Property) -> Property:
    """Run all enrichments. Safe to call even if no postcode is available."""
    prop = _enrich_crime(prop)
    prop = _enrich_epc(prop)
    prop = _enrich_school(prop)
    prop = _enrich_commute(prop)
    prop = _enrich_station(prop)
    return prop
