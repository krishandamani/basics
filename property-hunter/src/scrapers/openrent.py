"""OpenRent scraper — direct landlord rentals, no agent fees.

Builds the search URL automatically from search.location and criteria.
Uses requests + BeautifulSoup. Routes through Apify proxy on cloud (Railway)
because OpenRent blocks datacenter IPs, same as other UK property sites.
"""

import os
import re
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..models import Property, Search

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def _build_url(search: Search) -> str:
    params = [f"term={quote(search.location)}"]
    if search.min_bedrooms:
        params.append(f"minBedrooms={search.min_bedrooms}")
    if search.max_bedrooms:
        params.append(f"maxBedrooms={search.max_bedrooms}")
    if search.min_price:
        params.append(f"minPrice={search.min_price}")
    if search.max_price:
        params.append(f"maxPrice={search.max_price}")
    return "https://www.openrent.co.uk/properties-to-rent/?" + "&".join(params)


def _get(url: str) -> requests.Response:
    """Fetch URL, routing through Apify proxy if API key is set (cloud IPs are blocked)."""
    api_key = os.environ.get("APIFY_API_KEY", "")
    if api_key:
        proxy = f"http://auto:{api_key}@proxy.apify.com:8000"
        proxies = {"http": proxy, "https": proxy}
        try:
            resp = requests.get(url, headers=_HEADERS, proxies=proxies,
                                timeout=20, verify=False)
            if resp.status_code == 200:
                return resp
            print(f"  [OpenRent] Proxy returned {resp.status_code} — trying direct")
        except Exception as exc:
            print(f"  [OpenRent] Proxy failed ({exc}) — trying direct")
    return requests.get(url, headers=_HEADERS, timeout=15)


def scrape(search: Search) -> List[Property]:
    # OpenRent is rent-only — skip for sale searches
    if search.listing_type == "sale":
        return []

    url = search.openrent_url or (_build_url(search) if search.location else None)
    if not url:
        return []

    try:
        resp = _get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        properties: List[Property] = []
        for card in soup.select(".pli, [data-property-id]"):
            try:
                link = card.find("a", href=True)
                if not link:
                    continue

                prop_url = link["href"]
                if not prop_url.startswith("http"):
                    prop_url = "https://www.openrent.co.uk" + prop_url

                id_match = re.search(r"/(\d+)(?:[/?]|$)", prop_url)
                prop_id = (
                    f"openrent_{id_match.group(1)}"
                    if id_match else f"openrent_{abs(hash(prop_url))}"
                )

                price_text = ""
                for el in card.find_all(string=re.compile(r"£")):
                    price_text = str(el)
                    break
                price_match = re.search(r"[\d,]+", price_text.replace(",", ""))
                price = int(price_match.group().replace(",", "")) if price_match else 0

                bed_text = card.find(string=re.compile(r"\d+\s*bed", re.I))
                bed_match = re.search(r"(\d+)", str(bed_text)) if bed_text else None
                bedrooms = int(bed_match.group(1)) if bed_match else 0

                title_el = (
                    card.find(class_=re.compile(r"title|name|heading", re.I))
                    or card.find("h2") or card.find("h3")
                )
                title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

                img = card.find("img")
                image_url = (
                    (img.get("src") or img.get("data-src")) if img else None
                )
                if image_url and not image_url.startswith("http"):
                    image_url = "https://www.openrent.co.uk" + image_url

                properties.append(
                    Property(
                        id=prop_id,
                        source="openrent",
                        listing_type="rent",
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
