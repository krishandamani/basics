"""Average sold price enrichment from the Land Registry SPARQL API (free)."""
import logging
import requests
from ..models import Property

log = logging.getLogger(__name__)

SPARQL_URL = "https://landregistry.data.gov.uk/landregistry/query"


def enrich_sold_prices(prop: Property) -> None:
    """Attach average recent sold price for the postcode district to prop."""
    if not prop.postcode:
        return

    # Use only the outward code (e.g. "SW1A" from "SW1A 1AA")
    district = prop.postcode.split()[0] if " " in prop.postcode else prop.postcode[:4]

    query = f"""
    PREFIX lrppi: <http://landregistry.data.gov.uk/def/ppi/>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    SELECT (AVG(?amount) AS ?avg) WHERE {{
      ?trans lrppi:pricePaid ?amount ;
             lrppi:propertyAddress/lrcommon:postcode ?pc .
      FILTER(STRSTARTS(STR(?pc), "{district}"))
      FILTER(?amount > 0)
    }} LIMIT 200
    """

    try:
        resp = requests.get(
            SPARQL_URL,
            params={"query": query, "output": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        bindings = data.get("results", {}).get("bindings", [])
        if bindings and bindings[0].get("avg", {}).get("value"):
            prop.avg_sold_price = int(float(bindings[0]["avg"]["value"]))
    except Exception as e:
        log.debug(f"[sold_prices] Enrichment failed for {prop.id}: {e}")
