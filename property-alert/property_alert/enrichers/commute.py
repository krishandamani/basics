"""Commute time enrichment using the Google Maps Distance Matrix API.

Free tier: $200/month credit — covers ~40,000 requests/month.
Get an API key: console.cloud.google.com → Enable "Distance Matrix API"
"""
import logging
import requests
from ..models import Property

log = logging.getLogger(__name__)

API_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


def enrich_commute(prop: Property, destination: str, api_key: str) -> None:
    """Calculate transit commute time from the property postcode to destination."""
    if not prop.postcode or not api_key or not destination:
        return
    try:
        resp = requests.get(
            API_URL,
            params={
                "origins": prop.postcode,
                "destinations": destination,
                "mode": "transit",
                "units": "metric",
                "key": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("rows", [])
        if not rows:
            return
        elements = rows[0].get("elements", [])
        if not elements or elements[0].get("status") != "OK":
            return

        duration_secs = elements[0]["duration"]["value"]
        prop.commute_minutes = round(duration_secs / 60)

    except Exception as e:
        log.debug(f"[commute] Enrichment failed for {prop.id}: {e}")
