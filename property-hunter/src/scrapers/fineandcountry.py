"""Fine & Country scraper — premium estate agent with own portal.

F&C sometimes lists luxury properties on their own site before syndicating
to Rightmove. Scrapes fineandcountry.com via Apify proxy.
Sale only — F&C lettings are negligible volume at this price bracket.
"""

import json
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_FC_BASE = "https://www.fineandcountry.com"


def _build_url(search: Search) -> str:
    params = [f"location={quote(search.location)}"]
    if search.min_bedrooms:
        params.append(f"minBedrooms={search.min_bedrooms}")
    if search.min_price:
        params.append(f"minPrice={search.min_price}")
    if search.max_price:
        params.append(f"maxPrice={search.max_price}")
    return f"{_FC_BASE}/find-a-property/property-for-sale?{'&'.join(params)}"


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
                print(f"  [F&C/proxy/{group}] {resp.status_code}")
            except Exception as exc:
                print(f"  [F&C/proxy/{group}] failed: {exc}")
    return requests.get(url, headers=_HEADERS, timeout=timeout)


def _parse_price(raw) -> int:
    try:
        if isinstance(raw, dict):
            val = raw.get("amount") or raw.get("value") or 0
            return int(re.sub(r"[^\d]", "", str(val)) or "0")
        return int(re.sub(r"[^\d]", "", str(raw)) or "0")
    except (ValueError, TypeError):
        return 0


def _postcode(text: str):
    m = re.search(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", text or "")
    return m.group() if m else None


def _parse_html_cards(html: str, listing_type: str) -> List[Property]:
    """BeautifulSoup fallback — parses property cards from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    properties = []

    selectors = [
        ".property-card", "[class*='property-card']", "[class*='PropertyCard']",
        "[class*='property-listing']", "article", ".listing-card",
    ]
    cards = []
    for sel in selectors:
        cards = soup.select(sel)
        if len(cards) >= 2:
            break

    for card in cards:
        try:
            link = card.find("a", href=True)
            if not link:
                continue
            href = link["href"]
            if not href:
                continue
            prop_url = href if href.startswith("http") else f"{_FC_BASE}{href}"
            if "/property" not in prop_url and "/property-search" in prop_url:
                continue

            id_match = re.search(r"/(\d+)(?:[/?]|$)", prop_url)
            prop_id = (
                f"fineandcountry_{id_match.group(1)}"
                if id_match else f"fineandcountry_{abs(hash(prop_url))}"
            )

            price_el = card.find(string=re.compile(r"£"))
            price_match = re.search(r"[\d,]+", str(price_el or "").replace(",", ""))
            price = int(price_match.group().replace(",", "")) if price_match else 0

            bed_el = card.find(string=re.compile(r"\d+\s*bed", re.I))
            bed_match = re.search(r"(\d+)", str(bed_el)) if bed_el else None
            bedrooms = int(bed_match.group(1)) if bed_match else 0

            title_el = card.find("h2") or card.find("h3") or card.find("h4") or link
            title = title_el.get_text(strip=True) if title_el else ""

            img = card.find("img")
            image_url = (img.get("src") or img.get("data-src")) if img else None
            if image_url and not image_url.startswith("http"):
                image_url = f"{_FC_BASE}{image_url}"

            properties.append(Property(
                id=prop_id,
                source="fineandcountry",
                listing_type=listing_type,
                url=prop_url,
                price=price,
                bedrooms=bedrooms,
                property_type="",
                address=title,
                title=title,
                postcode=_postcode(title),
                image_url=image_url or None,
                agent_name="Fine & Country",
            ))
        except Exception:
            continue
    return properties


def scrape(search: Search) -> List[Property]:
    # F&C is a premium sales agent — skip rent searches
    if search.listing_type == "rent":
        return []

    url = search.fineandcountry_url or (_build_url(search) if search.location else None)
    if not url:
        return []

    try:
        resp = _get(url)
        if resp.status_code != 200:
            print(f"  [F&C] HTTP {resp.status_code} — skipping")
            return []

        # Try __NEXT_DATA__ JSON first
        m = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>',
            resp.text, re.DOTALL,
        )
        if m:
            try:
                data = json.loads(m.group(1))
                page_props = data.get("props", {}).get("pageProps", {})
                for key in ("properties", "listings", "results", "propertyList"):
                    props_raw = page_props.get(key)
                    if not props_raw and isinstance(page_props.get("data"), dict):
                        props_raw = page_props["data"].get(key)
                    if isinstance(props_raw, list) and props_raw:
                        properties: List[Property] = []
                        for item in props_raw:
                            try:
                                pid = str(item.get("id", item.get("propertyId", "")))
                                if not pid:
                                    continue
                                prop_url = item.get("url", item.get("propertyUrl", ""))
                                if prop_url and not prop_url.startswith("http"):
                                    prop_url = f"{_FC_BASE}{prop_url}"
                                address = str(item.get("address", item.get("displayAddress", "")))
                                title = str(item.get("title", address))
                                img = item.get("image", item.get("mainImage", {}))
                                image_url = (
                                    img.get("src") or img.get("url")
                                    if isinstance(img, dict) else img
                                ) or None
                                properties.append(Property(
                                    id=f"fineandcountry_{pid}",
                                    source="fineandcountry",
                                    listing_type="sale",
                                    url=prop_url,
                                    price=_parse_price(item.get("price", 0)),
                                    bedrooms=int(item.get("bedrooms", item.get("beds", 0)) or 0),
                                    property_type=str(item.get("propertyType", "")),
                                    address=address,
                                    title=title,
                                    postcode=_postcode(address),
                                    image_url=str(image_url) if image_url else None,
                                    agent_name="Fine & Country",
                                ))
                            except Exception:
                                continue
                        if properties:
                            print(f"  [F&C] {len(properties)} listings fetched (__NEXT_DATA__)")
                            return properties
            except Exception:
                pass

        # BeautifulSoup fallback
        properties = _parse_html_cards(resp.text, listing_type="sale")
        print(f"  [F&C] {len(properties)} listings fetched (HTML parse)")
        return properties

    except Exception as exc:
        print(f"  [F&C] Error: {exc}")
        return []
