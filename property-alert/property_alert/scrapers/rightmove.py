"""Rightmove scraper using the internal /api/_search JSON endpoint."""
import logging
from datetime import datetime, timezone
from typing import Optional

from ..models import Property, SearchCriteria
from .base import BaseScraper, make_session, polite_delay

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.rightmove.co.uk/api/_search"

# Map generic property types to Rightmove's propertyTypes param values
PROPERTY_TYPE_MAP = {
    "flat": "FLAT",
    "apartment": "FLAT",
    "terraced": "TERRACED",
    "semi-detached": "SEMI_DETACHED",
    "detached": "DETACHED",
    "bungalow": "BUNGALOW",
    "land": "LAND",
    "park home": "PARK_HOME",
}


def _type_for_criteria(criteria: SearchCriteria) -> str:
    return "RENT" if criteria.listing_type == "rent" else "BUY"


class RightmoveScraper(BaseScraper):
    name = "rightmove"

    def _scrape(self, criteria: SearchCriteria) -> list[Property]:
        session = make_session()
        session.headers["Referer"] = "https://www.rightmove.co.uk/"

        location_id = self._resolve_location(session, criteria.location)
        if not location_id:
            log.warning(f"[rightmove] Could not resolve location: {criteria.location}")
            return []

        properties = []
        index = 0
        page_size = 24

        while True:
            params = self._build_params(criteria, location_id, index, page_size)
            try:
                resp = session.get(SEARCH_URL, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.error(f"[rightmove] Request failed at index {index}: {e}")
                break

            results = data.get("properties", [])
            if not results:
                break

            for item in results:
                prop = self._parse(item, criteria.listing_type)
                if prop:
                    properties.append(prop)

            total = data.get("resultCount", 0)
            index += page_size
            if index >= min(total, 120):  # cap at 5 pages for personal use
                break

            polite_delay()

        log.info(f"[rightmove] Found {len(properties)} listings")
        return properties

    def _resolve_location(self, session, location: str) -> Optional[str]:
        """Use Rightmove's typeahead API to resolve a location name to an identifier."""
        url = "https://www.rightmove.co.uk/house-prices/search.html"
        typeahead_url = "https://www.rightmove.co.uk/property-for-sale/search.html"
        suggest_url = f"https://www.rightmove.co.uk/api/_searchLocations?searchLocation={location}&apiApplication=ANDROID"
        try:
            resp = session.get(suggest_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            locations = data.get("typeAheadLocations", [])
            if locations:
                loc = locations[0]
                return loc.get("locationIdentifier", "")
        except Exception as e:
            log.warning(f"[rightmove] Location resolve failed: {e}")
        return None

    def _build_params(self, criteria: SearchCriteria, location_id: str, index: int, page_size: int) -> dict:
        params = {
            "locationIdentifier": location_id,
            "numberOfPropertiesPerPage": page_size,
            "index": index,
            "includeSSTC": "false",
            "viewType": "LIST",
            "channel": _type_for_criteria(criteria),
            "areaSizeUnit": "sqft",
            "currencyCode": "GBP",
            "isFetching": "false",
        }

        if criteria.min_price is not None:
            params["minPrice" if criteria.listing_type == "sale" else "minRent"] = criteria.min_price
        if criteria.max_price is not None:
            params["maxPrice" if criteria.listing_type == "sale" else "maxRent"] = criteria.max_price
        if criteria.min_bedrooms is not None:
            params["minBedrooms"] = criteria.min_bedrooms
        if criteria.max_bedrooms is not None:
            params["maxBedrooms"] = criteria.max_bedrooms
        if criteria.radius_miles:
            params["radius"] = criteria.radius_miles
        if criteria.property_types:
            rm_types = [PROPERTY_TYPE_MAP.get(t.lower(), t.upper()) for t in criteria.property_types]
            params["propertyTypes"] = ",".join(rm_types)

        return params

    def _parse(self, item: dict, listing_type: str) -> Optional[Property]:
        try:
            prop_id = str(item.get("id", ""))
            if not prop_id:
                return None

            price_info = item.get("price", {})
            raw_price = price_info.get("amount")
            frequency = price_info.get("frequency", "").lower() or None

            location = item.get("location", {})
            images = [img.get("srcUrl", "") for img in item.get("propertyImages", {}).get("images", []) if img.get("srcUrl")]

            return Property(
                id=f"rightmove:{prop_id}",
                source="rightmove",
                listing_type=listing_type,
                url=f"https://www.rightmove.co.uk/properties/{prop_id}",
                title=item.get("propertyTypeFullDescription", ""),
                price=int(raw_price) if raw_price else None,
                price_frequency=frequency,
                bedrooms=item.get("bedrooms"),
                bathrooms=item.get("bathrooms"),
                property_type=item.get("propertySubType", item.get("propertyType", "")).lower(),
                address=item.get("displayAddress", ""),
                postcode=None,
                latitude=location.get("latitude"),
                longitude=location.get("longitude"),
                description=item.get("summary", ""),
                features=item.get("listingUpdate", {}).get("listingUpdateReason", "").split(",") if item.get("listingUpdate") else [],
                images=images[:5],
                agent_name=item.get("customer", {}).get("branchDisplayName", ""),
                listed_date=item.get("addedOrReduced", ""),
            )
        except Exception as e:
            log.debug(f"[rightmove] Parse error: {e}")
            return None
