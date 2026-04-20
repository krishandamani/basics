"""PrimeLocation scraper (Zoopla sister site — identical platform, same listings).

Zoopla itself returns 403 even with residential proxy due to aggressive Cloudflare.
PrimeLocation shares the same Next.js codebase and __NEXT_DATA__ structure but
has weaker bot protection.  Cost: Apify proxy bandwidth only.
"""

import json
import os
import re
from typing import List, Optional

import requests

from ..models import Property, Search

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _location_slug(location: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")


_PL_BASE = "https://www.primelocation.com"


def _build_url(search: Search) -> str:
    """Build a PrimeLocation search URL (same params as Zoopla)."""
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
    return f"{_PL_BASE}/{path}/property/{slug}/?{'&'.join(params)}"


def _get(url: str, timeout: int = 30) -> requests.Response:
    api_key = os.environ.get("APIFY_API_KEY", "")
    if api_key:
        for group in ("groups-RESIDENTIAL", "auto"):
            proxy_url = f"http://{group}:{api_key}@proxy.apify.com:8000"
            try:
                resp = requests.get(
                    url, headers=_HEADERS,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=timeout, verify=False,
                )
                if resp.status_code == 200:
                    return resp
                print(f"  [Zoopla/proxy/{group}] {resp.status_code}")
            except Exception as exc:
                print(f"  [Zoopla/proxy/{group}] failed: {exc}")
    return requests.get(url, headers=_HEADERS, timeout=timeout)


def _extract_listings(next_data: dict) -> list:
    page_props = next_data.get("props", {}).get("pageProps", {})
    search_results = page_props.get("searchResults")
    candidates = [
        page_props.get("regularListingsFormatted"),
        page_props.get("listings"),
        page_props.get("properties"),
        search_results.get("listings") if isinstance(search_results, dict) else None,
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
        resp = _get(url)
        if resp.status_code != 200:
            print(f"  [Zoopla] HTTP {resp.status_code} — skipping")
            return []

        m = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
            resp.text, re.DOTALL,
        )
        if not m:
            print("  [PrimeLocation] No __NEXT_DATA__ found — page may be blocked")
            return []

        listings_raw = _extract_listings(json.loads(m.group(1)))
        if not listings_raw:
            print("  [PrimeLocation] 0 listings in __NEXT_DATA__ (empty results or structure changed)")
            return []

        properties: List[Property] = []
        for item in listings_raw:
            try:
                listing_id = str(item.get("id", ""))
                if not listing_id:
                    continue

                price_data = item.get("price", {})
                if isinstance(price_data, dict):
                    price = int(price_data.get("value", price_data.get("amount", 0)) or 0)
                else:
                    price = int(price_data or 0)

                listing_uris = item.get("listingUris") or {}
                detail_uri = listing_uris.get("detail", "") or item.get("url", "")
                prop_url = (
                    f"{_PL_BASE}{detail_uri}"
                    if detail_uri and not detail_uri.startswith("http")
                    else detail_uri
                )

                img = item.get("image", {})
                image_url = (img.get("src", "") if isinstance(img, dict) else "") or None

                address = str(item.get("address", ""))
                postcode_match = re.search(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", address)

                branch = item.get("branch") or {}
                agent_name = (
                    branch.get("name")
                    or item.get("agentName")
                    or None
                )

                properties.append(Property(
                    id=f"primelocation_{listing_id}",
                    source="zoopla",
                    listing_type=listing_type,
                    url=prop_url,
                    price=price,
                    bedrooms=int(item.get("beds", item.get("bedrooms", 0)) or 0),
                    property_type=str(item.get("propertyType", item.get("property_type", ""))),
                    address=address,
                    title=str(item.get("title", "")),
                    postcode=postcode_match.group() if postcode_match else None,
                    image_url=image_url,
                    agent_name=str(agent_name) if agent_name else None,
                ))
            except Exception:
                continue

        print(f"  [PrimeLocation] {len(properties)} listings fetched")
        return properties

    except Exception as exc:
        print(f"  [PrimeLocation] Error: {exc}")
        return []
