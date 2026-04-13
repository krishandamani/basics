"""Knight Frank scraper — Playwright + __NEXT_DATA__ / network interception.

Knight Frank lists prime properties before they hit Rightmove/Zoopla.
Requires playwright: pip install playwright && playwright install chromium
Falls back gracefully to empty list if playwright is not installed.
"""

import json
import re
from typing import List, Optional
from urllib.parse import urlencode

from ..models import Property, Search


def _build_url(search: Search) -> str:
    mode = "rent" if search.listing_type == "rent" else "buy"
    type_id = 2 if mode == "rent" else 1
    params: dict = {"typeId": type_id, "mode": mode, "location": search.location, "radius": 2}
    if search.min_bedrooms:
        params["minbedrooms"] = search.min_bedrooms
    if search.min_price:
        params["minprice"] = search.min_price
    if search.max_price:
        params["maxprice"] = search.max_price
    return f"https://www.knightfrank.co.uk/search/?{urlencode(params)}"


def _find_listings(obj, depth: int = 0) -> list:
    """Recursively search for a property-list array in JSON."""
    if depth > 8:
        return []
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        keys = obj[0].keys()
        if any(k in keys for k in ("propertyId", "listingId", "id", "PropertyId")):
            if any(k in keys for k in ("price", "Price", "askingPrice", "bedrooms", "beds")):
                return obj
    if isinstance(obj, list):
        for item in obj:
            r = _find_listings(item, depth + 1)
            if r:
                return r
    elif isinstance(obj, dict):
        for v in obj.values():
            r = _find_listings(v, depth + 1)
            if r:
                return r
    return []


def _parse_price(raw) -> int:
    if isinstance(raw, dict):
        return int(raw.get("value", raw.get("amount", 0)) or 0)
    return int(re.sub(r"[^\d]", "", str(raw)) or "0")


def scrape(search: Search) -> List[Property]:
    url = search.knightfrank_url or _build_url(search)
    listing_type = "rent" if search.listing_type == "rent" else "sale"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [Knight Frank] playwright not installed — skipping")
        return []

    intercepted: list = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            def _on_response(response):
                url_lower = response.url.lower()
                if response.status == 200 and any(
                    k in url_lower for k in ("search", "properties", "listings", "results")
                ):
                    try:
                        body = response.json()
                        items = _find_listings(body)
                        if items:
                            intercepted.extend(items)
                    except Exception:
                        pass

            page.on("response", _on_response)
            page.goto(url, wait_until="networkidle", timeout=60_000)

            # Fallback: __NEXT_DATA__
            if not intercepted:
                try:
                    raw = page.eval_on_selector("#__NEXT_DATA__", "el => el.textContent")
                    items = _find_listings(json.loads(raw))
                    if items:
                        intercepted.extend(items)
                except Exception:
                    pass

            browser.close()
    except Exception as exc:
        print(f"  [Knight Frank] Error: {exc}")
        return []

    results: List[Property] = []
    for item in intercepted:
        try:
            lid = str(
                item.get("propertyId") or item.get("id")
                or item.get("PropertyId") or item.get("listingId") or ""
            )
            prop_url = (
                item.get("url") or item.get("propertyUrl")
                or item.get("link") or url
            )
            if prop_url and not prop_url.startswith("http"):
                prop_url = "https://www.knightfrank.co.uk" + prop_url

            price = _parse_price(
                item.get("price") or item.get("Price") or item.get("askingPrice") or 0
            )
            beds = int(
                item.get("bedrooms") or item.get("Bedrooms")
                or item.get("beds") or 0
            )
            prop_type = str(
                item.get("propertyType") or item.get("PropertyType")
                or item.get("type") or ""
            )
            address = str(
                item.get("address") or item.get("Address")
                or item.get("displayAddress") or item.get("location") or ""
            )
            pc = re.search(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", address)

            images = (
                item.get("images") or item.get("Images")
                or item.get("photos") or []
            )
            image_url: Optional[str] = None
            if images and isinstance(images, list):
                first = images[0]
                image_url = (
                    first.get("url") or first.get("src")
                    if isinstance(first, dict) else str(first)
                )

            results.append(Property(
                id=f"knightfrank_{lid}" if lid else f"knightfrank_{abs(hash(prop_url))}",
                source="knightfrank",
                listing_type=listing_type,
                url=prop_url,
                price=price,
                bedrooms=beds,
                property_type=prop_type,
                address=address,
                title=f"{beds} bed {prop_type} — {address}",
                postcode=pc.group() if pc else None,
                image_url=image_url,
                agent_name="Knight Frank",
            ))
        except Exception:
            continue

    print(f"  [Knight Frank] {len(results)} listings fetched")
    return results
