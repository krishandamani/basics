"""Rightmove scraper — uses the rightmove-webscraper pip package (277 ⭐)."""

import re
from typing import List

from ..models import Property, Search


def scrape(search: Search) -> List[Property]:
    if not search.rightmove_url:
        return []

    try:
        from rightmove_webscraper import RightmoveData

        url = search.rightmove_url
        # Sort by newest first so we catch fresh listings
        if "sortType" not in url:
            url += ("&" if "?" in url else "?") + "sortType=6"

        listing_type = "rent" if "to-rent" in url or "property-to-rent" in url else "sale"

        rm = RightmoveData(url)
        df = rm.get_results

        properties: List[Property] = []
        for _, row in df.iterrows():
            prop_url = str(row.get("url", ""))
            if not prop_url:
                continue

            # Make URL absolute
            if prop_url.startswith("/"):
                prop_url = "https://www.rightmove.co.uk" + prop_url

            # Extract numeric ID from URL like /properties/12345678
            id_match = re.search(r"/properties/(\d+)", prop_url)
            prop_id = (
                f"rightmove_{id_match.group(1)}"
                if id_match
                else f"rightmove_{abs(hash(prop_url))}"
            )

            try:
                price = int(
                    str(row.get("price", 0))
                    .replace(",", "")
                    .replace("£", "")
                    .replace("pcm", "")
                    .strip()
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
