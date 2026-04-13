"""Savills scraper — Playwright-based (handles JS SPA).

Savills (and other premium agents) sometimes list properties 24–48h before
they appear on Rightmove. This gives an early-mover advantage.

Requires playwright: pip install playwright && playwright install chromium
Falls back gracefully to empty list if playwright is not installed.
"""

import json
import re
from typing import List, Optional
from urllib.parse import urlencode

from ..models import Property, Search


def _build_url(search: Search) -> str:
    listing_path = "to-rent" if search.listing_type == "rent" else "for-sale"
    instr = "Lettings" if search.listing_type == "rent" else "Sale"
    params: dict = {
        "SearchBy": "location",
        "Instruction_Type": instr,
        "SearchArea": search.location,
        "Distance": 2,
    }
    if search.min_bedrooms:
        params["MinBeds"] = search.min_bedrooms
    if search.min_price:
        params["MinPrice"] = search.min_price
    if search.max_price:
        params["MaxPrice"] = search.max_price
    return f"https://www.savills.co.uk/find-a-property/{listing_path}/?{urlencode(params)}"


def _find_listings(obj, depth: int = 0) -> list:
    """Recursively search for a property-list array in intercepted JSON."""
    if depth > 8:
        return []
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        keys = obj[0].keys()
        if any(k in keys for k in ("propertyId", "PropertyId", "listingId", "id")):
            if any(k in keys for k in ("price", "Price", "askingPrice", "bedrooms")):
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
    url = search.savills_url or _build_url(search)
    listing_type = "rent" if search.listing_type == "rent" else "sale"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [Savills] playwright not installed — skipping")
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
                if "api" in response.url and response.status == 200:
                    try:
                        body = response.json()
                        items = _find_listings(body)
                        if items:
                            intercepted.extend(items)
                    except Exception:
                        pass

            page.on("response", _on_response)
            page.goto(url, wait_until="networkidle", timeout=60_000)

            # Fallback: __NEXT_DATA__ or window.__data__
            if not intercepted:
                for selector in ("#__NEXT_DATA__", "script[id='__NEXT_DATA__']"):
                    try:
                        raw = page.eval_on_selector(selector, "el => el.textContent")
                        items = _find_listings(json.loads(raw))
                        if items:
                            intercepted.extend(items)
                            break
                    except Exception:
                        pass

            browser.close()
    except Exception as exc:
        print(f"  [Savills] Error: {exc}")
        return []

    results: List[Property] = []
    for item in intercepted:
        try:
            lid = str(
                item.get("propertyId") or item.get("PropertyId")
                or item.get("listingId") or item.get("id") or ""
            )
            prop_url = (
                item.get("url") or item.get("propertyUrl")
                or item.get("detailUrl") or url
            )
            if prop_url and not prop_url.startswith("http"):
                prop_url = "https://www.savills.co.uk" + prop_url

            price = _parse_price(
                item.get("price") or item.get("Price") or item.get("askingPrice") or 0
            )
            beds = int(item.get("bedrooms") or item.get("Bedrooms") or 0)
            prop_type = str(item.get("propertyType") or item.get("PropertyType") or "")
            address = str(
                item.get("address") or item.get("Address")
                or item.get("displayAddress") or ""
            )
            pc = re.search(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", address)

            images = item.get("images") or item.get("Images") or []
            image_url: Optional[str] = None
            if images and isinstance(images, list):
                first = images[0]
                image_url = (
                    first.get("url") or first.get("src")
                    if isinstance(first, dict) else str(first)
                )

            results.append(Property(
                id=f"savills_{lid}" if lid else f"savills_{abs(hash(prop_url))}",
                source="savills",
                listing_type=listing_type,
                url=prop_url,
                price=price,
                bedrooms=beds,
                property_type=prop_type,
                address=address,
                title=f"{beds} bed {prop_type} — {address}",
                postcode=pc.group() if pc else None,
                image_url=image_url,
                agent_name="Savills",
            ))
        except Exception:
            continue

    print(f"  [Savills] {len(results)} listings fetched")
    return results
