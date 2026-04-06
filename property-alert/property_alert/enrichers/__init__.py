from .crime import enrich_crime
from .epc import enrich_epc
from .sold_prices import enrich_sold_prices
from .commute import enrich_commute
from .schools import enrich_schools


def enrich(prop, criteria, cfg: dict) -> None:
    """Run all enabled enrichers in-place on a Property object."""
    if criteria.include_crime and prop.latitude and prop.longitude:
        enrich_crime(prop)

    if criteria.include_epc and prop.postcode:
        enrich_epc(prop, cfg.get("epc_api_key", ""))

    enrich_sold_prices(prop)

    if criteria.include_commute and criteria.commute_to and prop.postcode:
        enrich_commute(prop, criteria.commute_to, cfg.get("google_maps_api_key", ""))

    if criteria.include_schools and prop.postcode:
        enrich_schools(prop)
