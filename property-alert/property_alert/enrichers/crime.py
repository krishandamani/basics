"""Crime enrichment using the free police.uk Data API."""
import logging
import requests
from ..models import Property

log = logging.getLogger(__name__)

API_URL = "https://data.police.uk/api/crimes-street/all-crime"


def enrich_crime(prop: Property) -> None:
    """Fetch crime counts near the property and attach a summary to prop."""
    if not (prop.latitude and prop.longitude):
        return
    try:
        resp = requests.get(
            API_URL,
            params={"lat": prop.latitude, "lng": prop.longitude},
            timeout=10,
        )
        resp.raise_for_status()
        crimes = resp.json()

        if not isinstance(crimes, list):
            return

        total = len(crimes)
        by_cat: dict[str, int] = {}
        for c in crimes:
            cat = c.get("category", "other").replace("-", " ").title()
            by_cat[cat] = by_cat.get(cat, 0) + 1

        top = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:3]
        top_str = ", ".join(f"{cat} ({n})" for cat, n in top)

        if total < 20:
            prop.crime_score = "Low"
        elif total < 60:
            prop.crime_score = "Medium"
        else:
            prop.crime_score = "High"

        prop.crime_summary = f"{total} crimes last month — top: {top_str}"

    except Exception as e:
        log.debug(f"[crime] Enrichment failed for {prop.id}: {e}")
