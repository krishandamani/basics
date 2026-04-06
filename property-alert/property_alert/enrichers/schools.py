"""Nearest school enrichment from Ofsted open data (free, no API key needed).

Uses the UK Government's open Ofsted inspections dataset to find the nearest
school to a property and its most recent Ofsted rating.
"""
import logging
import requests
from ..models import Property

log = logging.getLogger(__name__)

# UK schools open data — postcode-level lookup via postcodes.io + Ofsted API
POSTCODES_URL = "https://api.postcodes.io/postcodes"
# Ofsted published data is available via the UK DCSF / Edubase REST API
SCHOOLS_URL = "https://get-information-schools.service.gov.uk/api/schools/search"


def enrich_schools(prop: Property) -> None:
    """Find the nearest school and its Ofsted rating for a property."""
    if not prop.postcode:
        return
    try:
        # Step 1: resolve postcode to coordinates (if not already known)
        lat, lng = prop.latitude, prop.longitude
        if not lat or not lng:
            r = requests.get(f"{POSTCODES_URL}/{prop.postcode.replace(' ', '')}", timeout=8)
            r.raise_for_status()
            result = r.json().get("result", {})
            lat = result.get("latitude")
            lng = result.get("longitude")

        if not lat or not lng:
            return

        # Step 2: search for nearest schools
        r = requests.get(
            SCHOOLS_URL,
            params={"lat": lat, "lon": lng, "radius": 1, "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        schools = data.get("Schools", [])
        if not schools:
            return

        school = schools[0]
        prop.nearest_school = school.get("EstablishmentName", "")

        # Map Ofsted rating codes to labels
        ofsted_map = {
            "1": "Outstanding",
            "2": "Good",
            "3": "Requires Improvement",
            "4": "Inadequate",
        }
        rating_code = str(school.get("OfstedRating", {}).get("Code", ""))
        prop.nearest_school_rating = ofsted_map.get(rating_code, "Not rated")

    except Exception as e:
        log.debug(f"[schools] Enrichment failed for {prop.id}: {e}")
