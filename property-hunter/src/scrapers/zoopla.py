"""Zoopla scraper — uses Playwright to handle Cloudflare/JS rendering.

Builds the search URL automatically from search.location and criteria.
"""

import json
import re
from typing import List, Optional
from urllib.parse import quote

from ..models import Property, Search

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _location_slug(location: str) -> str:
    """Convert a place name to a Zoopla URL slug. e.g. 'East London' → 'east-london'"""
    return re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")


def _build_url(search: Search) -> str:
    """Construct a Zoopla search URL from plain criteria."""
    slug = _location_slug(search.location)
    listing_type = search.listing_type if search.listing_type != "both" else "rent"
    path = "to-rent" if listing_type == "rent" else "for-sale"

    params = []
    if search.min_bedrooms:
        params.append(f"beds_min={search.min_bedrooms}")
    if search.max_bedrooms:
        params.append(f"beds_max={search.max_bedrooms}")
    if search.min_price:
        params.append(f"price_min={search.min_price}")
    if search.max_price:
        params.append(f"price_max={search.max_price}")
    params.append("results_sort=newest_listings")

    qs = "&".join(params)
    return f"https://www.zoopla.co.uk/{path}/property/{slug}/?{qs}"


def _extract_listings(next_data: dict) -> list:
    page_props = next_data.get("props", {}).get("pageProps", {})
    candidates = [
        page_props.get("regularListingsFormatted"),
        page_props.get("listings"),
        page_props.get("properties"),
        page_props.get("searchResults", {}).get("listings"),
    ]
    for c in candidates:
        if isinstance(c, list) and c:
            return c

    def _find(obj, depth=0):
        if depth > 6:
            return None
        if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "id" in obj[0]:
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                result = _find(v, depth + 1)
                if result:
                    return result
        return None

    return _find(page_props) or []


def scrape(search: Search) -> List[Property]:
    url = search.zoopla_url or (_build_url(search) if search.location else None)
    if not url:
        return []

    listing_type = "rent" if "to-rent" in url else "sale"

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_UA)
            context.set_extra_http_headers({"Accept-Language": "en-GB,en;q=0.9"})
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2_000)

                next_data_str = page.evaluate(
                    "() => { const el = document.getElementById('__NEXT_DATA__'); "
                    "return el ? el.textContent : null; }"
                )
                if not next_data_str:
                    print("  [Zoopla] Could not find page data — site may have changed")
                    return []
                listings_raw = _extract_listings(json.loads(next_data_str))
            finally:
                browser.close()

        properties: List[Property] = []
        for item in listings_raw:
            try:
                listing_id = str(item.get("id", ""))
                price_data = item.get("price", {})
                if isinstance(price_data, dict):
                    price = int(price_data.get("value", price_data.get("amount", 0)) or 0)
                else:
                    price = int(price_data or 0)

                detail_uri = item.get("listingUris", {}).get("detail", "") or item.get("url", "")
                prop_url = (
                    f"https://www.zoopla.co.uk{detail_uri}"
                    if detail_uri and not detail_uri.startswith("http")
                    else detail_uri
                )

                img = item.get("image", {})
                image_url = img.get("src", "") if isinstance(img, dict) else ""

                address = str(item.get("address", ""))
                postcode_match = re.search(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", address)

                properties.append(
                    Property(
                        id=f"zoopla_{listing_id}",
                        source="zoopla",
                        listing_type=listing_type,
                        url=prop_url,
                        price=price,
                        bedrooms=int(item.get("beds", item.get("bedrooms", 0)) or 0),
                        property_type=str(item.get("propertyType", item.get("property_type", ""))),
                        address=address,
                        title=str(item.get("title", "")),
                        postcode=postcode_match.group() if postcode_match else None,
                        image_url=image_url or None,
                    )
                )
            except Exception:
                continue

        print(f"  [Zoopla] {len(properties)} listings fetched")
        return properties

    except Exception as exc:
        print(f"  [Zoopla] Error: {exc}")
        return []
