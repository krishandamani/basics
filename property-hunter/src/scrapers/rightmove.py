"""Rightmove scraper — uses the rightmove-webscraper pip package (277 ⭐).

Builds the search URL automatically from search.location and criteria.
No need for the user to paste any URL.
"""

import re
from typing import List, Optional
from urllib.parse import quote

import requests

from ..models import Property, Search

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def _location_id(location: str) -> Optional[str]:
    """Look up Rightmove's internal location identifier from a plain place name."""
    try:
        r = requests.get(
            "https://www.rightmove.co.uk/typeAhead/uknostreetphoto",
            params={"query": location, "limit": 5},
            headers=_HEADERS,
            timeout=10,
        )
        results = r.json().get("typeAheadLocations", [])
        if results:
            return results[0]["locationIdentifier"]  # e.g. "REGION^92829"
    except Exception:
        pass
    return None


def _build_url(search: Search) -> Optional[str]:
    """Construct a Rightmove search URL from plain criteria."""
    loc_id = _location_id(search.location)
    if not loc_id:
        print(f"  [Rightmove] Could not find location: '{search.location}'")
        return None

    listing_type = search.listing_type if search.listing_type != "both" else "rent"
    path = "property-to-rent" if listing_type == "rent" else "property-for-sale"

    params = {
        "locationIdentifier": loc_id,
        "sortType": "6",          # newest first
    }
    if search.min_bedrooms:
        params["minBedrooms"] = str(search.min_bedrooms)
    if search.max_bedrooms:
        params["maxBedrooms"] = str(search.max_bedrooms)
    if search.min_price:
        params["minPrice"] = str(search.min_price)
    if search.max_price:
        params["maxPrice"] = str(search.max_price)

    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"https://www.rightmove.co.uk/{path}/find.html?{query}"


def scrape(search: Search) -> List[Property]:
    # Use explicit URL if provided, otherwise build from criteria
    url = search.rightmove_url or (_build_url(search) if search.location else None)
    if not url:
        return []

    try:
        from rightmove_webscraper import RightmoveData

        listing_type = "rent" if "to-rent" in url else "sale"
        rm = RightmoveData(url)
        df = rm.get_results

        properties: List[Property] = []
        for _, row in df.iterrows():
            prop_url = str(row.get("url", ""))
            if not prop_url:
                continue
            if prop_url.startswith("/"):
                prop_url = "https://www.rightmove.co.uk" + prop_url

            id_match = re.search(r"/properties/(\d+)", prop_url)
            prop_id = (
                f"rightmove_{id_match.group(1)}"
                if id_match
                else f"rightmove_{abs(hash(prop_url))}"
            )

            try:
                price = int(
                    str(row.get("price", 0))
                    .replace(",", "").replace("£", "").replace("pcm", "").strip()
                )
            except (ValueError, TypeError):
                price = 0

            try:
                bedrooms = int(row.get("number_bedrooms", 0))
            except (ValueError, TypeError):
                bedrooms = 0

            prop_type = str(row.get("type", ""))
            address = str(row.get("address", ""))
            postcode = str(row.get("postcode", "")) or None

            properties.append(
                Property(
                    id=prop_id,
                    source="rightmove",
                    listing_type=listing_type,
                    url=prop_url,
                    price=price,
                    bedrooms=bedrooms,
                    property_type=prop_type,
                    address=address,
                    title=f"{bedrooms} bed {prop_type} — {address}".strip(" —"),
                    postcode=postcode,
                )
            )

        print(f"  [Rightmove] {len(properties)} listings fetched")
        return properties

    except Exception as exc:
        print(f"  [Rightmove] Error: {exc}")
        return []
