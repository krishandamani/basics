"""OpenRent scraper — direct landlord rentals, no agent fees.
Uses requests + BeautifulSoup (OpenRent is the simplest site to scrape).
"""

import re
from typing import List

import requests
from bs4 import BeautifulSoup

from ..models import Property, Search

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _parse_price(text: str) -> int:
    match = re.search(r"[\d,]+", text.replace(",", ""))
    return int(match.group().replace(",", "")) if match else 0


def scrape(search: Search) -> List[Property]:
    if not search.openrent_url:
        return []

    try:
        resp = requests.get(search.openrent_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        properties: List[Property] = []

        # OpenRent property cards use class "pli" (property list item)
        for card in soup.select(".pli, [data-property-id]"):
            try:
                link = card.find("a", href=True)
                if not link:
                    continue

                prop_url = link["href"]
                if not prop_url.startswith("http"):
                    prop_url = "https://www.openrent.co.uk" + prop_url

                # ID from URL path e.g. /properties/1234567
                id_match = re.search(r"/(\d+)(?:[/?]|$)", prop_url)
                prop_id = (
                    f"openrent_{id_match.group(1)}"
                    if id_match
                    else f"openrent_{abs(hash(prop_url))}"
                )

                # Price — look for £ text inside the card
                price_text = ""
                for el in card.find_all(string=re.compile(r"£")):
                    price_text = str(el)
                    break
                price = _parse_price(price_text)

                # Bedrooms
                bed_text = card.find(string=re.compile(r"\d+\s*bed", re.I))
                bed_match = re.search(r"(\d+)", str(bed_text)) if bed_text else None
                bedrooms = int(bed_match.group(1)) if bed_match else 0

                # Title / address
                title_el = (
                    card.find(class_=re.compile(r"title|name|heading", re.I))
                    or card.find("h2")
                    or card.find("h3")
                )
                title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

                # Image
                img = card.find("img")
                image_url = (
                    img.get("src") or img.get("data-src")
                    if img
                    else None
                )
                if image_url and not image_url.startswith("http"):
                    image_url = "https://www.openrent.co.uk" + image_url

                properties.append(
                    Property(
                        id=prop_id,
                        source="openrent",
                        listing_type="rent",  # OpenRent is rental-only
                        url=prop_url,
                        price=price,
                        bedrooms=bedrooms,
                        property_type="",
                        address=title,
                        title=title,
                        image_url=image_url or None,
                    )
                )
            except Exception:
                continue

        print(f"  [OpenRent] {len(properties)} listings fetched")
        return properties

    except Exception as exc:
        print(f"  [OpenRent] Error: {exc}")
        return []
