"""Apify-based scrapers for Rightmove, Zoopla, and OnTheMarket.

Handles anti-bot protection via Apify residential proxies.
Requires APIFY_API_KEY environment variable to be set.

Actors used:
  Rightmove:    dhrumil/rightmove-scraper
  Zoopla:       dhrumil/zoopla-scraper
  OnTheMarket:  dhrumil/onthemarket-scraper
"""

import os
import re
from typing import List, Optional
from urllib.parse import quote

from ..models import Property, Search
from .rightmove import _build_url as _rm_build_url
from .zoopla import _build_url as _z_build_url

_RIGHTMOVE_ACTOR = "dhrumil/rightmove-scraper"
_ZOOPLA_ACTOR = "dhrumil/zoopla-scraper"
_OTM_ACTOR = "dhrumil/onthemarket-scraper"


def _client():
    from apify_client import ApifyClient
    return ApifyClient(os.environ["APIFY_API_KEY"])


def _extract_price(raw) -> int:
    try:
        if isinstance(raw, str):
            return int(re.sub(r"[^\d]", "", raw) or "0")
        if isinstance(raw, dict):
            return int(raw.get("value", raw.get("amount", 0)) or 0)
        return int(raw or 0)
    except (ValueError, TypeError):
        return 0


def _extract_image(images) -> Optional[str]:
    if not images or not isinstance(images, list):
        return None
    first = images[0]
    if isinstance(first, dict):
        return first.get("url") or first.get("src")
    if isinstance(first, str):
        return first
    return None


def _postcode(address: str) -> Optional[str]:
    m = re.search(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", address)
    return m.group() if m else None


def _rm_location_id_via_proxy(location: str) -> Optional[str]:
    """Call Rightmove typeahead through Apify's residential proxy to bypass cloud IP blocks.
    Returns a locationIdentifier like 'REGION^72526', or None on failure."""
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        return None
    try:
        import requests as req
        proxies = {
            "http":  f"http://groups-RESIDENTIAL:{api_key}@proxy.apify.com:8000",
            "https": f"http://groups-RESIDENTIAL:{api_key}@proxy.apify.com:8000",
        }
        r = req.get(
            "https://www.rightmove.co.uk/typeAhead/uknostreetphoto",
            params={"query": location, "limit": 1},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-GB,en;q=0.9",
            },
            proxies=proxies,
            timeout=15,
        )
        results = r.json().get("typeAheadLocations", [])
        if results:
            return results[0]["locationIdentifier"]
    except Exception as exc:
        print(f"  [Rightmove] Proxy typeahead failed: {exc}")
    return None


def scrape_rightmove(search: Search) -> List[Property]:
    """Scrape Rightmove via Apify (bypasses Akamai bot detection)."""
    url = search.rightmove_url or (_rm_build_url(search) if search.location else None)
    if not url and search.location:
        # Direct Rightmove API is blocked from cloud IPs — route through Apify residential proxy.
        loc_id = _rm_location_id_via_proxy(search.location)
        if loc_id:
            # Build URL directly from the resolved locationIdentifier
            listing_type_path = "property-to-rent" if search.listing_type == "rent" else "property-for-sale"
            params = {"locationIdentifier": loc_id, "sortType": "6"}
            if search.min_bedrooms: params["minBedrooms"] = str(search.min_bedrooms)
            if search.max_bedrooms: params["maxBedrooms"] = str(search.max_bedrooms)
            if search.min_price:    params["minPrice"]    = str(search.min_price)
            if search.max_price:    params["maxPrice"]    = str(search.max_price)
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"https://www.rightmove.co.uk/{listing_type_path}/find.html?{qs}"
            print(f"  [Rightmove] Resolved via proxy: {loc_id} → {url}")
        else:
            print(f"  [Rightmove] Could not resolve location '{search.location}' — skipping")
            return []
    if not url:
        return []

    listing_type = "rent" if "to-rent" in url else "sale"
    client = _client()
    run = client.actor(_RIGHTMOVE_ACTOR).call(
        run_input={"listUrls": [{"url": url}], "maxItems": 40},
        timeout_secs=300,
    )
    if not run:
        raise RuntimeError(f"Rightmove Apify actor '{_RIGHTMOVE_ACTOR}' returned no run object")

    results: List[Property] = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        try:
            prop_url = str(item.get("url", ""))
            if not prop_url:
                continue
            lid = str(item.get("listingId", ""))
            prop_id = f"rightmove_{lid}" if lid else f"rightmove_{abs(hash(prop_url))}"
            bedrooms = int(item.get("bedrooms", 0) or 0)
            prop_type = str(item.get("propertyType", ""))
            address = str(item.get("address", ""))
            results.append(Property(
                id=prop_id,
                source="rightmove",
                listing_type=listing_type,
                url=prop_url,
                price=_extract_price(item.get("price", 0)),
                bedrooms=bedrooms,
                property_type=prop_type,
                address=address,
                title=str(item.get("propertyTitle", "")) or f"{bedrooms} bed {prop_type}",
                postcode=_postcode(address),
                image_url=_extract_image(item.get("images")),
                agent_name=str(item.get("agentName", "")) or None,
            ))
        except Exception:
            continue

    print(f"  [Rightmove/Apify] {len(results)} listings fetched")
    return results


def scrape_zoopla(search: Search) -> List[Property]:
    """Scrape Zoopla via Apify (bypasses Cloudflare/DataDome protection)."""
    url = search.zoopla_url or (_z_build_url(search) if search.location else None)
    if not url:
        return []

    listing_type = "rent" if "to-rent" in url else "sale"
    client = _client()
    run = client.actor(_ZOOPLA_ACTOR).call(
        run_input={"startUrls": [{"url": url}], "maxItems": 40},
        timeout_secs=300,
    )
    if not run:
        raise RuntimeError(f"Zoopla Apify actor '{_ZOOPLA_ACTOR}' returned no run object")

    results: List[Property] = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        try:
            prop_url = str(item.get("url", item.get("listingUrl", "")))
            if not prop_url:
                continue
            lid = str(item.get("id", item.get("listingId", "")))
            prop_id = f"zoopla_{lid}" if lid else f"zoopla_{abs(hash(prop_url))}"
            bedrooms = int(item.get("bedrooms", item.get("beds", 0)) or 0)
            prop_type = str(item.get("propertyType", item.get("type", "")))
            address = str(item.get("address", ""))
            results.append(Property(
                id=prop_id,
                source="zoopla",
                listing_type=listing_type,
                url=prop_url,
                price=_extract_price(item.get("price", 0)),
                bedrooms=bedrooms,
                property_type=prop_type,
                address=address,
                title=str(item.get("title", "")) or f"{bedrooms} bed {prop_type}",
                postcode=_postcode(address),
                image_url=_extract_image(item.get("images")),
                agent_name=str(item.get("agentName", "")) or None,
            ))
        except Exception:
            continue

    print(f"  [Zoopla/Apify] {len(results)} listings fetched")
    return results


def scrape_onthemarket(search: Search) -> List[Property]:
    """Scrape OnTheMarket via Apify — lists properties before Rightmove/Zoopla."""
    url = search.onthemarket_url or (_otm_build_url(search) if search.location else None)
    if not url:
        return []

    listing_type = "rent" if "to-rent" in url else "sale"
    client = _client()
    run = client.actor(_OTM_ACTOR).call(
        run_input={"startUrls": [{"url": url}], "maxItems": 40},
        timeout_secs=180,
    )
    if not run:
        raise RuntimeError(f"OnTheMarket Apify actor '{_OTM_ACTOR}' returned no run object")

    results: List[Property] = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        try:
            prop_url = str(item.get("url", item.get("listingUrl", item.get("link", ""))))
            if not prop_url:
                continue
            if not prop_url.startswith("http"):
                prop_url = "https://www.onthemarket.com" + prop_url
            lid = str(item.get("id", item.get("listingId", item.get("propertyId", ""))))
            prop_id = f"onthemarket_{lid}" if lid else f"onthemarket_{abs(hash(prop_url))}"
            bedrooms = int(item.get("bedrooms", item.get("beds", 0)) or 0)
            prop_type = str(item.get("propertyType", item.get("type", "")))
            address = str(item.get("address", item.get("displayAddress", "")))
            results.append(Property(
                id=prop_id,
                source="onthemarket",
                listing_type=listing_type,
                url=prop_url,
                price=_extract_price(item.get("price", 0)),
                bedrooms=bedrooms,
                property_type=prop_type,
                address=address,
                title=str(item.get("title", "")) or f"{bedrooms} bed {prop_type}",
                postcode=_postcode(address),
                image_url=_extract_image(item.get("images")),
                agent_name=str(item.get("agentName", "")) or None,
            ))
        except Exception:
            continue

    print(f"  [OnTheMarket/Apify] {len(results)} listings fetched")
    return results
