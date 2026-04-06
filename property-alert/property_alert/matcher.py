"""Filter scraped properties against user-defined search criteria."""
import logging
from .models import Property, SearchCriteria

log = logging.getLogger(__name__)


def matches(prop: Property, criteria: SearchCriteria) -> bool:
    """Return True if the property satisfies all criteria."""

    # Listing type
    if criteria.listing_type != "both" and prop.listing_type != criteria.listing_type:
        return False

    # Price
    if prop.price is not None:
        if criteria.min_price is not None and prop.price < criteria.min_price:
            return False
        if criteria.max_price is not None and prop.price > criteria.max_price:
            return False

    # Bedrooms
    if prop.bedrooms is not None:
        if criteria.min_bedrooms is not None and prop.bedrooms < criteria.min_bedrooms:
            return False
        if criteria.max_bedrooms is not None and prop.bedrooms > criteria.max_bedrooms:
            return False

    # Property type
    if criteria.property_types and prop.property_type:
        normalised = prop.property_type.lower()
        if not any(t.lower() in normalised or normalised in t.lower() for t in criteria.property_types):
            return False

    # Keyword inclusion
    searchable = f"{prop.title} {prop.description} {' '.join(prop.features)}".lower()
    for kw in criteria.keywords_require:
        if kw.lower() not in searchable:
            return False

    # Keyword exclusion
    for kw in criteria.keywords_exclude:
        if kw.lower() in searchable:
            return False

    return True


def filter_properties(
    properties: list[Property],
    criteria: SearchCriteria,
) -> list[Property]:
    matched = [p for p in properties if matches(p, criteria)]
    log.info(f"Matcher: {len(matched)}/{len(properties)} passed for '{criteria.label}'")
    return matched
