"""Rightmove scraper — direct HTML via Apify residential proxy.

All Apify actors for Rightmove are paid (epctex, dhrumil both require
rental after free trial). This module bypasses actors entirely:

  1. Fetches the standard Rightmove search-results HTML page
     through Apify's residential proxy to bypass Akamai IP blocks
  2. Parses property data from the embedded __NEXT_DATA__ JSON blob
     (fallback: window.jsonModel, then BeautifulSoup card parsing)

Cost: Apify proxy bandwidth only (~$0.60/GB datacenter, ~$3/GB residential).
A single search page is ~300-500 KB, so 18 searches costs <<$0.01 per cycle.

Requires APIFY_API_KEY environment variable.
"""

import json
import os
import re
from typing import List, Optional

import requests

from ..models import Property, Search
from .rightmove import _build_url as _rm_build_url

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

_RM_BASE = "https://www.rightmove.co.uk"


# ── proxy fetch ───────────────────────────────────────────────────────────────

def _proxy_get(url: str, timeout: int = 30) -> requests.Response:
    """GET url via Apify proxy (residential → auto → direct fallback)."""
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
                print(f"  [proxy/{group}] {resp.status_code} for {url[:80]}")
            except Exception as exc:
                print(f"  [proxy/{group}] failed: {exc}")
    return requests.get(url, headers=_HEADERS, timeout=timeout)


# ── Rightmove HTML → property list ────────────────────────────────────────────

def _looks_valid(html: str) -> bool:
    return any(m in html for m in ("__NEXT_DATA__", "jsonModel", "propertyCard"))


def _deep_find(obj, key: str):
    """Recursively find first occurrence of key in nested dict/list."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = _deep_find(v, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _deep_find(item, key)
            if result is not None:
                return result
    return None


def _parse_rightmove_html(html: str) -> list:
    """Extract raw property dicts from Rightmove HTML.

    Tries in order: __NEXT_DATA__ JSON → window.jsonModel → empty list.
    """
    # ── __NEXT_DATA__ (Next.js) ───────────────────────────────────────────────
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
        html, re.DOTALL,
    )
    if m:
        try:
            data = json.loads(m.group(1))
            search_props = (
                data.get("props", {})
                    .get("pageProps", {})
                    .get("searchPageProps", {})
            )
            props = search_props.get("properties") or search_props.get("results")
            if not props:
                props = _deep_find(data, "properties")
            if props and isinstance(props, list):
                return props
        except Exception as exc:
            print(f"  [Rightmove] __NEXT_DATA__ parse error: {exc}")

    # ── legacy window.jsonModel ───────────────────────────────────────────────
    for pattern in (
        r"window\['jsonModel'\]\s*=\s*(\{.+?\});\s*(?:window|</script>)",
        r"window\.jsonModel\s*=\s*(\{.+?\});\s*(?:window|</script>)",
    ):
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                props = json.loads(m.group(1)).get("properties", [])
                if props:
                    return props
            except Exception:
                pass

    return []


def _extract_price(raw) -> int:
    try:
        if isinstance(raw, dict):
            val = raw.get("amount") or 0
            if not val:
                dp = raw.get("displayPrices", [{}])
                val = dp[0].get("displayPrice", "0") if dp else "0"
            return int(re.sub(r"[^\d]", "", str(val)) or "0")
        return int(re.sub(r"[^\d]", "", str(raw)) or "0")
    except (ValueError, TypeError):
        return 0


def _postcode(address: str) -> Optional[str]:
    m = re.search(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", address or "")
    return m.group() if m else None


def _extract_image(prop: dict) -> Optional[str]:
    """Pull first image URL from Rightmove property dict."""
    images = prop.get("propertyImages", {})
    if isinstance(images, dict):
        imgs = images.get("images", [])
    else:
        imgs = []
    for img in imgs:
        if isinstance(img, dict):
            url = img.get("srcUrl") or img.get("url") or img.get("src")
            if url:
                return url
        elif isinstance(img, str):
            return img
    return None


def _rm_location_id_via_proxy(location: str) -> Optional[str]:
    """Call Rightmove typeahead through Apify proxy to resolve locationIdentifier."""
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        return None
    for group in ("groups-RESIDENTIAL", "auto"):
        proxy_url = f"http://{group}:{api_key}@proxy.apify.com:8000"
        try:
            r = requests.get(
                "https://www.rightmove.co.uk/typeAhead/uknostreetphoto",
                params={"query": location, "limit": 1},
                headers=_HEADERS,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=15, verify=False,
            )
            results = r.json().get("typeAheadLocations", [])
            if results:
                loc_id = results[0]["locationIdentifier"]
                print(f"  [Rightmove] proxy resolved '{location}' → {loc_id}")
                return loc_id
            print(f"  [Rightmove] proxy ({group}) empty for '{location}' (status {r.status_code})")
        except Exception as exc:
            print(f"  [Rightmove] proxy ({group}) failed: {exc}")
    return None


# ── public scraper ────────────────────────────────────────────────────────────

def scrape_rightmove(search: Search) -> List[Property]:
    """Scrape Rightmove via direct HTML + Apify residential proxy.

    No Apify actor used — fetches search-results HTML, parses __NEXT_DATA__ JSON.
    """
    url = search.rightmove_url or (_rm_build_url(search) if search.location else None)
    if not url and search.location:
        loc_id = _rm_location_id_via_proxy(search.location)
        if loc_id:
            path = "property-to-rent" if search.listing_type == "rent" else "property-for-sale"
            params = {
                "locationIdentifier": loc_id, "sortType": "6",
                **({} if not search.min_bedrooms else {"minBedrooms": str(search.min_bedrooms)}),
                **({} if not search.max_bedrooms else {"maxBedrooms": str(search.max_bedrooms)}),
                **({} if not search.min_price    else {"minPrice":    str(search.min_price)}),
                **({} if not search.max_price    else {"maxPrice":    str(search.max_price)}),
            }
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{_RM_BASE}/{path}/find.html?{qs}"
        else:
            print(f"  [Rightmove] could not resolve '{search.location}' — skipping")
            return []
    if not url:
        return []

    listing_type = "rent" if "to-rent" in url else "sale"

    resp = _proxy_get(url)
    if resp.status_code != 200:
        raise RuntimeError(f"Rightmove returned HTTP {resp.status_code}")

    if not _looks_valid(resp.text):
        raise RuntimeError(
            "Rightmove returned a bot-detection page (no __NEXT_DATA__ or propertyCard found). "
            "Residential proxy may be needed — check APIFY_API_KEY and proxy credit."
        )

    raw_props = _parse_rightmove_html(resp.text)
    if not raw_props:
        print("  [Rightmove] page valid but 0 properties parsed (empty search results?)")
        return []

    results: List[Property] = []
    for prop in raw_props:
        try:
            prop_id_str = str(prop.get("id", prop.get("propertyId", "")))
            if not prop_id_str:
                continue

            prop_url = f"{_RM_BASE}/properties/{prop_id_str}"
            prop_id = f"rightmove_{prop_id_str}"
            bedrooms = int(prop.get("bedrooms", 0) or 0)
            prop_type = str(
                prop.get("propertySubType")
                or prop.get("propertyTypeFullDescription")
                or prop.get("propertyType", "")
            )
            address = str(prop.get("displayAddress", prop.get("address", "")))
            agent = prop.get("customer", {})
            agent_name = (
                agent.get("brandPlusDisplayName") or agent.get("brandDisplayName")
                if isinstance(agent, dict) else None
            )

            results.append(Property(
                id=prop_id,
                source="rightmove",
                listing_type=listing_type,
                url=prop_url,
                price=_extract_price(prop.get("price", 0)),
                bedrooms=bedrooms,
                property_type=prop_type,
                address=address,
                title=f"{bedrooms} bed {prop_type}".strip() or address,
                postcode=_postcode(address),
                image_url=_extract_image(prop),
                agent_name=str(agent_name) if agent_name else None,
            ))
        except Exception:
            continue

    print(f"  [Rightmove/proxy] {len(results)} listings fetched")
    return results


# ── disabled paid actors (kept for reference) ─────────────────────────────────

def scrape_zoopla(search: Search) -> List[Property]:
    print("  [Zoopla] disabled — actor is paid, no free alternative found")
    return []


def scrape_onthemarket(search: Search) -> List[Property]:
    print("  [OnTheMarket] disabled — actor is paid, no free alternative found")
    return []
