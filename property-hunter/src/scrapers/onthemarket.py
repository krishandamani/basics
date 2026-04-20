"""OnTheMarket scraper — direct HTML via Apify residential proxy.

OnTheMarket lists properties up to 24h before Rightmove/Zoopla, and many
premium agents (Savills, Hamptons, Fine & Country) use the "One Other Portal"
scheme — exclusively OTM + one other — so OTM catches listings not on Rightmove.

Parses __NEXT_DATA__ JSON; falls back to BeautifulSoup card parsing.
"""

import json
import os
import re
from typing import List

import requests
from bs4 import BeautifulSoup

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
    params = []
    if search.min_bedrooms:
        params.append(f"min-bedrooms={search.min_bedrooms}")
    if search.max_bedrooms:
        params.append(f"max-bedrooms={search.max_bedrooms}")
    if search.min_price:
        params.append(f"min-price={search.min_price}")
    if search.max_price:
        params.append(f"max-price={search.max_price}")
    params.append("sort=newest")
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


_PROPERTY_KEYS = {
    "id", "listingId", "propertyId", "guid",
    "price", "priceInfo", "displayPrice",
    "bedrooms", "beds", "numBedrooms",
    "propertyType", "type", "subType",
    "address", "displayAddress",
    "detailUrl", "url", "href",
}


def _find_listings(obj, depth=0):
    """Recursively find a list of property dicts regardless of exact field names."""
    if depth > 8:
        return []
    if isinstance(obj, list) and len(obj) >= 1 and isinstance(obj[0], dict):
        if sum(1 for k in obj[0] if k in _PROPERTY_KEYS) >= 2:
            return obj
    if isinstance(obj, dict):
        for v in obj.values():
            result = _find_listings(v, depth + 1)
            if result:
                return result
    return []


def _parse_html_cards(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for card in soup.select(
        "[data-testid='property-card'], .property-card, li.otm-PropertyCard"
    ):
        try:
            link = card.find("a", href=True)
            if not link:
                continue
            href = link["href"]
            prop_url = f"{_OTM_BASE}{href}" if not href.startswith("http") else href
            id_match = re.search(r"/details/(\d+)", href)
            prop_id = id_match.group(1) if id_match else str(abs(hash(prop_url)))
            price_text = str(card.find(string=re.compile(r"£")) or "0")
            price_num = re.search(r"[\d,]+", price_text.replace(",", ""))
            price = int(price_num.group().replace(",", "")) if price_num else 0
            bed_el = card.find(string=re.compile(r"\d+\s*bed", re.I))
            bed_match = re.search(r"(\d+)", str(bed_el)) if bed_el else None
            title_el = card.find("h2") or card.find("h3") or link
            img = card.find("img")
            listings.append({
                "_html": True,
                "id": prop_id,
                "prop_url": prop_url,
                "price": price,
                "bedrooms": int(bed_match.group(1)) if bed_match else 0,
                "title": title_el.get_text(strip=True) if title_el else "",
                "image_url": img.get("src") if img else None,
            })
        except Exception:
            continue
    return listings


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

        listings_raw = []

        m = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
            resp.text, re.DOTALL,
        )
        if m:
            try:
                next_data = json.loads(m.group(1))
                page_props = next_data.get("props", {}).get("pageProps", {})
                results_obj = page_props.get("results") or {}
                # Try every plausible OTM path before falling back to deep search
                listings_raw = (
                    (results_obj.get("results", []) if isinstance(results_obj, dict) else [])
                    or (results_obj.get("properties", []) if isinstance(results_obj, dict) else [])
                    or page_props.get("properties", [])
                    or page_props.get("listings", [])
                    or page_props.get("propertiesForSale", [])
                    or page_props.get("propertiesForRent", [])
                    or (page_props.get("data") or {}).get("properties", [])
                    or (page_props.get("data") or {}).get("results", [])
                    or (page_props.get("initialData") or {}).get("properties", [])
                    or (page_props.get("searchResults") or {}).get("properties", [])
                    or _find_listings(page_props)
                )
            except json.JSONDecodeError:
                pass

        if not listings_raw:
            listings_raw = _parse_html_cards(resp.text)

        if not listings_raw:
            print("  [OnTheMarket] 0 listings parsed (empty results or structure changed)")
            return []

        properties: List[Property] = []
        for item in listings_raw:
            try:
                if item.get("_html"):
                    listing_id = str(item["id"])
                    prop_url = item["prop_url"]
                    price = item["price"]
                    bedrooms = item["bedrooms"]
                    title = item["title"]
                    image_url = item.get("image_url")
                    prop_type = ""
                    address = title
                    agent_name = None
                else:
                    listing_id = str(item.get("id", ""))
                    price_info = item.get("price", {})
                    price = int(
                        (price_info.get("amount", price_info.get("value", 0))
                         if isinstance(price_info, dict) else price_info) or 0
                    )
                    detail_url = item.get("detailUrl", item.get("url", ""))
                    prop_url = (
                        f"{_OTM_BASE}{detail_url}"
                        if detail_url and not detail_url.startswith("http")
                        else detail_url
                    )
                    images = item.get("images", [])
                    image_url = (
                        images[0].get("src", "") if images and isinstance(images[0], dict) else ""
                    ) or None
                    address = str(
                        item.get("address")
                        or (item.get("location") or {}).get("address", "")
                        or ""
                    )
                    title = str(item.get("title", address))
                    prop_type = str(item.get("propertyType", item.get("property_type", "")))
                    bedrooms = int(item.get("bedrooms", item.get("beds", 0)) or 0)
                    agent = item.get("agent") or {}
                    agent_name = agent.get("name") or None

                if not listing_id:
                    continue

                postcode_match = re.search(
                    r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", title + " " + address
                )
                properties.append(Property(
                    id=f"onthemarket_{listing_id}",
                    source="onthemarket",
                    listing_type=listing_type,
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
