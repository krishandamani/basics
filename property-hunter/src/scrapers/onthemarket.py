"""OnTheMarket scraper — calls their async search API directly.

OTM is a React/Redux app: __NEXT_DATA__ is an empty shell, all property data
loads via XHR to /async-search/. We call that endpoint directly with JSON headers.
OTM lists properties up to 24h before Rightmove; many premium agents (Savills,
Hamptons, Fine & Country) use OTM's "One Other Portal" exclusivity scheme.
"""

import os
import re
from typing import List

import requests

from ..models import Property, Search

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
    "DNT": "1",
    "Referer": "https://www.onthemarket.com/",
}

_OTM_BASE = "https://www.onthemarket.com"


def _location_slug(location: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")


def _build_api_url(search: Search) -> str:
    slug = _location_slug(search.location)
    listing_type = search.listing_type if search.listing_type != "both" else "rent"
    search_type = "to-rent" if listing_type == "rent" else "for-sale"
    params = [
        f"channel=property",
        f"search-type={search_type}",
        f"location-id={slug}",
        "sort-field=recent",
        "direction=desc",
        "frame-size=40",
        "page=1",
    ]
    if search.min_bedrooms:
        params.append(f"min-bedrooms={search.min_bedrooms}")
    if search.max_bedrooms:
        params.append(f"max-bedrooms={search.max_bedrooms}")
    if search.min_price:
        params.append(f"min-price={search.min_price}")
    if search.max_price:
        params.append(f"max-price={search.max_price}")
    return f"{_OTM_BASE}/async-search/?{'&'.join(params)}"


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
                print(f"  [OnTheMarket/proxy/{group}] {resp.status_code}")
            except Exception as exc:
                print(f"  [OnTheMarket/proxy/{group}] failed: {exc}")
    return requests.get(url, headers=_HEADERS, timeout=timeout)


def scrape(search: Search) -> List[Property]:
    listing_type = search.listing_type if search.listing_type != "both" else "rent"
    listing_type_label = "rent" if listing_type == "rent" else "sale"

    # Use explicit URL as fallback API URL if provided, else build from location
    if search.onthemarket_url:
        # Convert HTML search URL to async API URL by extracting params
        url = search.onthemarket_url
        slug_m = re.search(r"/property/([^/?]+)", url)
        slug = slug_m.group(1) if slug_m else _location_slug(search.location)
        search_type = "to-rent" if "to-rent" in url else "for-sale"
        params = [
            "channel=property", f"search-type={search_type}",
            f"location-id={slug}", "sort-field=recent", "direction=desc",
            "frame-size=40", "page=1",
        ]
        if search.min_bedrooms:
            params.append(f"min-bedrooms={search.min_bedrooms}")
        if search.max_bedrooms:
            params.append(f"max-bedrooms={search.max_bedrooms}")
        if search.min_price:
            params.append(f"min-price={search.min_price}")
        if search.max_price:
            params.append(f"max-price={search.max_price}")
        api_url = f"{_OTM_BASE}/async-search/?{'&'.join(params)}"
    elif search.location:
        api_url = _build_api_url(search)
    else:
        return []

    try:
        resp = _get(api_url)
        if resp.status_code != 200:
            print(f"  [OnTheMarket] HTTP {resp.status_code} — skipping")
            return []

        data = resp.json()
        listings_raw = (
            data.get("properties")
            or data.get("results")
            or data.get("listings")
            or []
        )

        if not listings_raw:
            print(f"  [OnTheMarket] 0 listings in API response (keys: {list(data.keys())})")
            return []

        properties: List[Property] = []
        for item in listings_raw:
            try:
                listing_id = str(item.get("id", item.get("propertyId", "")))
                if not listing_id:
                    continue

                price_info = item.get("price", {})
                price = int(
                    (price_info.get("amount", price_info.get("value", 0))
                     if isinstance(price_info, dict) else price_info) or 0
                )

                detail_url = item.get("detailUrl", item.get("url", item.get("href", "")))
                prop_url = (
                    f"{_OTM_BASE}{detail_url}"
                    if detail_url and not detail_url.startswith("http")
                    else detail_url
                ) or f"{_OTM_BASE}/details/{listing_id}/"

                images = item.get("images", [])
                image_url = (
                    images[0].get("src", "") if images and isinstance(images[0], dict) else ""
                ) or item.get("mainImageSrc") or None

                address = str(
                    item.get("address")
                    or (item.get("location") or {}).get("address", "")
                    or ""
                )
                title = str(item.get("title", address))
                prop_type = str(item.get("propertyType", item.get("type", "")))
                bedrooms = int(item.get("bedrooms", item.get("beds", 0)) or 0)
                agent = item.get("agent") or item.get("branch") or {}
                agent_name = (agent.get("name") or agent.get("displayName")) if isinstance(agent, dict) else None

                postcode_match = re.search(
                    r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", title + " " + address
                )
                properties.append(Property(
                    id=f"onthemarket_{listing_id}",
                    source="onthemarket",
                    listing_type=listing_type_label,
                    url=prop_url,
                    price=price,
                    bedrooms=bedrooms,
                    property_type=prop_type,
                    address=address,
                    title=title,
                    postcode=postcode_match.group() if postcode_match else None,
                    image_url=image_url,
                    agent_name=str(agent_name) if agent_name else None,
                ))
            except Exception:
                continue

        print(f"  [OnTheMarket] {len(properties)} listings fetched")
        return properties

    except Exception as exc:
        print(f"  [OnTheMarket] Error: {exc}")
        return []
