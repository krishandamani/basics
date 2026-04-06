"""EPC enrichment using the free EPC Register API (epc.opendatacommunities.org)."""
import logging
import requests
from ..models import Property

log = logging.getLogger(__name__)

API_URL = "https://epc.opendatacommunities.org/api/v1/domestic/search"


def enrich_epc(prop: Property, api_key: str) -> None:
    """Attach EPC energy rating to prop. Requires a free EPC API key."""
    if not prop.postcode or not api_key:
        return
    try:
        resp = requests.get(
            API_URL,
            params={"postcode": prop.postcode, "size": 1},
            headers={
                "Authorization": f"Basic {api_key}",
                "Accept": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if rows:
            prop.epc_rating = rows[0].get("current-energy-rating", "")
    except Exception as e:
        log.debug(f"[epc] Enrichment failed for {prop.id}: {e}")
