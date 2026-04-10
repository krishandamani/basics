"""OnTheMarket scraper — uses Playwright + __NEXT_DATA__ extraction.
OnTheMarket lists properties up to 24 h before Rightmove/Zoopla,
making it especially valuable for fast-moving markets.
"""

import json
import re
from typing import List

from ..models import Property, Search

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _find_listings(obj, depth: int = 0) -> list:
    """Recursively search for a list of listing dicts inside __NEXT_DATA__."""
    if depth > 7:
        return []
    if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
        # Heuristic: a listings array has 'id' or 'price' in each item
        if any(k in obj[0] for k in ("id", "price", "bedrooms", "propertyType")):
            return obj
    if isinstance(obj, dict):
        for v in obj.values():
            result = _find_listings(v, depth + 1)
            if result:
                return result
    return []


def scrape(search: Search) -> List[Property]:
    if not search.onthemarket_url:
        return []

    try:
        from playwright.sync_api import sync_playwright

        url = search.onthemarket_url
        listing_type = "rent" if ("to-rent" in url or "to-let" in url) else "sale"

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

                listings_raw = []
                if next_data_str:
                    try:
                        next_data = json.loads(next_data_str)
                        page_props = next_data.get("props", {}).get("pageProps", {})

                        # Try known OTM paths first
                        listings_raw = (
                            page_props.get("results", {}).get("results", [])
                            or page_props.get("properties", [])
                            or page_props.get("listings", [])
                            or _find_listings(page_props)
                        )
                    except json.JSONDecodeError:
                        pass

                if not listings_raw:
                    # Fallback: scrape the rendered HTML for property cards
                    html = page.content()
                    from bs4 import BeautifulSoup

                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.select("[data-testid='property-card'], .property-card, li.otm-PropertyCard")
                    for card in cards:
                        try:
                            link = card.find("a", href=True)
                            if not link:
                                continue
                            href = link["href"]
                            prop_url = (
                                f"https://www.onthemarket.com{href}"
                                if not href.startswith("http")
                                else href
                            )
                            id_match = re.search(r"/details/(\d+)", href)
                            prop_id = (
                                f"onthemarket_{id_match.group(1)}"
                                if id_match
                                else f"onthemarket_{abs(hash(prop_url))}"
                            )
                            price_el = card.find(string=re.compile(r"£"))
                            price_text = str(price_el) if price_el else "0"
                            price_match = re.search(r"[\d,]+", price_text.replace(",", ""))
                            price = int(price_match.group().replace(",", "")) if price_match else 0
                            bed_el = card.find(string=re.compile(r"\d+\s*bed", re.I))
                            bed_match = re.search(r"(\d+)", str(bed_el)) if bed_el else None
                            bedrooms = int(bed_match.group(1)) if bed_match else 0
                            title_el = card.find("h2") or card.find("h3") or link
                            title = title_el.get_text(strip=True) if title_el else ""
                            img = card.find("img")
                            image_url = img.get("src") or img.get("data-src") if img else None

                            listings_raw.append({
                                "_raw_html": True,
                                "id": prop_id.replace("onthemarket_", ""),
                                "prop_url": prop_url,
                                "price": price,
                                "bedrooms": bedrooms,
                                "title": title,
                                "image_url": image_url,
                            })
                        except Exception:
                            continue

            finally:
                browser.close()

        properties: List[Property] = []
        for item in listings_raw:
            try:
                if item.get("_raw_html"):
                    # Already parsed from HTML fallback
                    listing_id = str(item["id"])
                    prop_url = item["prop_url"]
                    price = item["price"]
                    bedrooms = item["bedrooms"]
                    title = item["title"]
                    image_url = item.get("image_url")
                    prop_type = ""
                    address = title
                else:
                    listing_id = str(item.get("id", ""))
                    price_info = item.get("price", {})
                    if isinstance(price_info, dict):
                        price = int(
                            price_info.get("amount", price_info.get("value", 0)) or 0
                        )
                    else:
                        price = int(price_info or 0)

                    detail_url = item.get("detailUrl", item.get("url", ""))
                    prop_url = (
                        f"https://www.onthemarket.com{detail_url}"
                        if detail_url and not detail_url.startswith("http")
                        else detail_url
                    )

                    images = item.get("images", [])
                    image_url = (
                        images[0].get("src", "")
                        if images and isinstance(images[0], dict)
                        else ""
                    ) or None

                    address = str(
                        item.get("address")
                        or item.get("location", {}).get("address", "")
                        or ""
                    )
                    title = str(item.get("title", address))
                    prop_type = str(item.get("propertyType", item.get("property_type", "")))
                    bedrooms = int(item.get("bedrooms", item.get("beds", 0)) or 0)

                postcode_match = re.search(
                    r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", title + " " + address
                )
                postcode = postcode_match.group() if postcode_match else None

                properties.append(
                    Property(
                        id=f"onthemarket_{listing_id}",
                        source="onthemarket",
                        listing_type=listing_type,
                        url=prop_url,
                        price=price,
                        bedrooms=bedrooms,
                        property_type=prop_type,
                        address=address,
                        title=title,
                        postcode=postcode,
                        image_url=image_url,
                    )
                )
            except Exception:
                continue

        print(f"  [OnTheMarket] {len(properties)} listings fetched")
        return properties

    except Exception as exc:
        print(f"  [OnTheMarket] Error: {exc}")
        return []
