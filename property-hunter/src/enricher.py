"""Optional enrichment — adds crime stats, EPC ratings, and nearest school
to matched properties. All sources are free public UK APIs.
Each enrichment is attempted independently; if any call fails, the property
is still included without that field.
"""

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


def _enrich_crime(prop: Property) -> Property:
    """Add a Low/Medium/High crime label using the police.uk public API."""
    if not prop.postcode:
        return prop
    lat, lng = _get_lat_lng(prop.postcode)
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
    """Find the nearest Good/Outstanding Ofsted-rated school within 1 mile.

    Uses the UK Government's Get Information About Schools (GIAS) API.
    Free, no API key required.

    Sets prop.nearest_school and prop.school_rating if found.
    """
    if not prop.postcode:
        return prop
    try:
        clean = re.sub(r"\s+", "", prop.postcode).upper()
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


# Approximate fast-train journey times to central London (minutes), by postcode district.
# Based on typical peak-time direct services. Updated: 2024.
_COMMUTE_BY_DISTRICT: dict = {
    # St Albans — Thameslink to City Thameslink / St Pancras
    "AL1": 20, "AL2": 23, "AL3": 23, "AL4": 22,
    # Welwyn Garden City / Hatfield — Thameslink to Kings Cross
    "AL7": 27, "AL8": 27, "AL9": 25, "AL10": 25,
    # Hitchin — Thameslink to Kings Cross
    "SG4": 35, "SG5": 35,
    # Potters Bar / Cuffley — Thameslink to Moorgate
    "EN6": 30,
    # Amersham — Metropolitan line to Baker Street
    "HP6": 55, "HP7": 52,
    # Northwood — Metropolitan line to Baker Street
    "HA6": 35,
    # Pinner — Metropolitan line to Baker Street
    "HA5": 30,
    # Bushey — Avanti West Coast to Euston
    "WD23": 25,
    # Croxley Green / Watford area — to Euston via Watford Junction
    "WD3": 28, "WD4": 28, "WD25": 25,
}


def _enrich_commute(prop: Property) -> Property:
    """Estimate train commute time to central London by postcode district (no API key needed)."""
    if not prop.postcode or prop.commute_minutes:
        return prop
    clean = re.sub(r"\s+", "", prop.postcode).upper()
    # Postcode district = everything before the final 3 chars (e.g. "AL1 1AA" → "AL1")
    district = clean[:-3] if len(clean) >= 5 else clean
    minutes = _COMMUTE_BY_DISTRICT.get(district)
    if minutes:
        prop.commute_minutes = minutes
    return prop


def enrich(prop: Property) -> Property:
    """Run all enrichments. Safe to call even if no postcode is available."""
    prop = _enrich_crime(prop)
    prop = _enrich_epc(prop)
    prop = _enrich_school(prop)
    prop = _enrich_commute(prop)
    return prop
