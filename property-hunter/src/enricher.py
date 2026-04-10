"""Optional enrichment — adds crime stats and EPC ratings to matched properties.
All sources used here are free public UK APIs. Each enrichment is attempted
independently; if any call fails, the property is still included without that field.
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


def enrich(prop: Property) -> Property:
    """Run all enrichments. Safe to call even if no postcode is available."""
    prop = _enrich_crime(prop)
    prop = _enrich_epc(prop)
    return prop
