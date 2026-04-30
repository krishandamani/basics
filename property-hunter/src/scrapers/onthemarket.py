"""OnTheMarket scraper — HTML page + initialReduxState extraction.

OTM is a React/Redux app. __NEXT_DATA__ contains an empty pageProps but
the full server-rendered property list is in:
  props.initialReduxState.results.list

Field names confirmed from live data (Apr 2026):
  id, price (string "£1,100,000"), details-url, address, bedrooms,
  humanised-property-type, cover-image.default, agent.name, property-title
"""

import json
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_OTM_BASE = "https://www.onthemarket.com"


def _location_slug(location: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")


def _build_url(search: Search) -> str:
    slug = _location_slug(search.location)
    listing_type = search.listing_type if search.listing_type != "both" else "rent"
    path = "to-rent" if listing_type == "rent" else "for-sale"
    params = ["sort=recent"]
    if search.min_bedrooms:
        params.append(f"min-bedrooms={search.min_bedrooms}")
    if search.max_bedrooms:
        params.append(f"max-bedrooms={search.max_bedrooms}")
    if search.min_price:
        params.append(f"min-price={search.min_price}")
    if search.max_price:
        params.append(f"max-price={search.max_price}")
    return f"{_OTM_BASE}/{path}/property/{slug}/?{'&'.join(params)}"


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


def _parse_price(price_str) -> int:
    """Convert OTM price string '£1,100,000' → 1100000."""
    try:
        return int(re.sub(r"[^\d]", "", str(price_str)))
    except (ValueError, TypeError):
        return 0


def scrape(search: Search) -> List[Property]:
    url = search.onthemarket_url or (_build_url(search) if search.location else None)
    if not url:
        return []

    listing_type = "rent" if ("to-rent" in url or "to-let" in url) else "sale"

    try:
        resp = _get(url)
        if resp.status_code != 200:
            print(f"  [OnTheMarket] HTTP {resp.status_code} — skipping")
            return []

        m = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
            resp.text, re.DOTALL,
        )
        if not m:
            print("  [OnTheMarket] No __NEXT_DATA__ in response")
            return []

        nd = json.loads(m.group(1))
        listings_raw = (
            nd.get("props", {})
              .get("initialReduxState", {})
              .get("results", {})
              .get("list", [])
        )

        if not listings_raw:
            print("  [OnTheMarket] 0 listings in initialReduxState.results.list")
            return []

        properties: List[Property] = []
        for item in listings_raw:
            try:
                listing_id = str(item.get("id", ""))
                if not listing_id:
                    continue

                detail_url = item.get("details-url", f"/details/{listing_id}/")
                prop_url = (
                    f"{_OTM_BASE}{detail_url}"
                    if not detail_url.startswith("http")
                    else detail_url
                )

                cover = item.get("cover-image") or {}
                image_url = cover.get("default") or cover.get("webp") or None

                address = str(item.get("address", ""))
                title = str(item.get("property-title", address))
                prop_type = str(item.get("humanised-property-type", ""))
                bedrooms = int(item.get("bedrooms", 0) or 0)

                agent = item.get("agent") or {}
                agent_name = agent.get("name") if isinstance(agent, dict) else None

                postcode_match = re.search(
                    r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", title + " " + address
                )

                point = item.get("point") or {}
                lat = float(point["lat"]) if isinstance(point, dict) and point.get("lat") else None
                lng = float(point["lng"]) if isinstance(point, dict) and point.get("lng") else None

                properties.append(Property(
                    id=f"onthemarket_{listing_id}",
                    source="onthemarket",
                    listing_type=listing_type,
                    url=prop_url,
                    price=_parse_price(item.get("price", 0)),
                    bedrooms=bedrooms,
                    property_type=prop_type,
                    address=address,
                    title=title,
                    postcode=postcode_match.group() if postcode_match else None,
                    image_url=image_url,
                    agent_name=str(agent_name) if agent_name else None,
                    lat=lat,
                    lng=lng,
                ))
            except Exception:
                continue

        print(f"  [OnTheMarket] {len(properties)} listings fetched")
        return properties

    except Exception as exc:
        print(f"  [OnTheMarket] Error: {exc}")
        return []
