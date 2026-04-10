"""Filter scraped properties against a Search's criteria."""

from typing import List

from .models import Property, Search


def _matches(prop: Property, search: Search) -> bool:
    # Price range
    if prop.price and search.min_price and prop.price < search.min_price:
        return False
    if prop.price and search.max_price and prop.price > search.max_price:
        return False

    # Bedrooms
    if prop.bedrooms and search.min_bedrooms and prop.bedrooms < search.min_bedrooms:
        return False
    if prop.bedrooms and search.max_bedrooms and prop.bedrooms > search.max_bedrooms:
        return False

    # Listing type (sale vs rent)
    if search.listing_type != "both" and prop.listing_type != search.listing_type:
        return False

    # Property types — empty list means accept everything
    if search.property_types:
        prop_type_lower = prop.property_type.lower()
        if not any(t.lower() in prop_type_lower for t in search.property_types):
            return False

    # Required keywords — ALL must appear somewhere in the listing text
    if search.keywords_required:
        text = f"{prop.title} {prop.address} {prop.description or ''}".lower()
        if not all(kw.lower() in text for kw in search.keywords_required):
            return False

    # Excluded keywords — NONE must appear
    if search.keywords_excluded:
        text = f"{prop.title} {prop.address} {prop.description or ''}".lower()
        if any(kw.lower() in text for kw in search.keywords_excluded):
            return False

    return True


def filter_properties(properties: List[Property], search: Search) -> List[Property]:
    """Return only the properties that match the search criteria."""
    return [p for p in properties if _matches(p, search)]
