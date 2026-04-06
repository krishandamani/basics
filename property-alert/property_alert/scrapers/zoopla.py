"""Zoopla scraper using BeautifulSoup + JSON-LD embedded in page."""
import logging
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from ..models import Property, SearchCriteria
from .base import BaseScraper, make_session, polite_delay

log = logging.getLogger(__name__)

PROPERTY_TYPE_MAP = {
    "flat": "flats",
    "apartment": "flats",
    "terraced": "houses",
    "semi-detached": "houses",
    "detached": "houses",
    "bungalow": "bungalows",
}


class ZooplaScraper(BaseScraper):
    name = "zoopla"

    def _scrape(self, criteria: SearchCriteria) -> list[Property]:
        session = make_session()
        session.headers.update({
            "Referer": "https://www.zoopla.co.uk/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        action = "to-rent" if criteria.listing_type == "rent" else "for-sale"
        location_slug = criteria.location.lower().replace(" ", "-").replace(",", "")

        properties = []
        page = 1

        while page <= 5:  # cap at 5 pages
            url = f"https://www.zoopla.co.uk/{action}/property/{location_slug}/"
            params = self._build_params(criteria, page)
            try:
                resp = session.get(url, params=params, timeout=20)
                if resp.status_code == 403:
                    log.warning("[zoopla] Blocked (403) — skipping")
                    break
                resp.raise_for_status()
            except Exception as e:
                log.error(f"[zoopla] Request failed page {page}: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            listings = self._extract_listings(soup, criteria.listing_type)
            if not listings:
                break

            properties.extend(listings)
            page += 1
            polite_delay()

        log.info(f"[zoopla] Found {len(properties)} listings")
        return properties

    def _build_params(self, criteria: SearchCriteria, page: int) -> dict:
        params = {"pn": page}
        if criteria.radius_miles:
            params["radius"] = criteria.radius_miles
        if criteria.min_price is not None:
            params["price_min"] = criteria.min_price
        if criteria.max_price is not None:
            params["price_max"] = criteria.max_price
        if criteria.min_bedrooms is not None:
            params["beds_min"] = criteria.min_bedrooms
        if criteria.max_bedrooms is not None:
            params["beds_max"] = criteria.max_bedrooms
        if criteria.property_types:
            # Zoopla uses property_type param — pick first matching category
            pt = criteria.property_types[0].lower()
            if pt in PROPERTY_TYPE_MAP:
                params["property_type"] = PROPERTY_TYPE_MAP[pt]
        return params

    def _extract_listings(self, soup: BeautifulSoup, listing_type: str) -> list[Property]:
        """Extract listings from Zoopla's embedded __NEXT_DATA__ JSON blob."""
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if not script:
            log.warning("[zoopla] Could not find __NEXT_DATA__ — page structure may have changed")
            return self._fallback_extract(soup, listing_type)

        try:
            data = json.loads(script.string)
            listings_raw = (
                data.get("props", {})
                    .get("pageProps", {})
                    .get("regularListingsFormatted", [])
            )
            return [p for p in (self._parse(item, listing_type) for item in listings_raw) if p]
        except Exception as e:
            log.debug(f"[zoopla] JSON parse error: {e}")
            return []

    def _fallback_extract(self, soup: BeautifulSoup, listing_type: str) -> list[Property]:
        """Fallback: parse visible HTML cards."""
        props = []
        cards = soup.select("div[data-testid='regular-listings'] article")
        for card in cards:
            try:
                link = card.select_one("a[href*='/details/']")
                if not link:
                    continue
                href = link["href"]
                prop_id = re.search(r"/(\d+)/?$", href)
                if not prop_id:
                    continue

                price_el = card.select_one("[data-testid='listing-price']")
                beds_el = card.select_one("[data-testid='beds-label']")
                addr_el = card.select_one("address")

                price_text = price_el.get_text(strip=True) if price_el else ""
                price_num = int(re.sub(r"[^\d]", "", price_text)) if price_text else None

                props.append(Property(
                    id=f"zoopla:{prop_id.group(1)}",
                    source="zoopla",
                    listing_type=listing_type,
                    url=f"https://www.zoopla.co.uk{href}",
                    title="",
                    price=price_num,
                    price_frequency="pcm" if listing_type == "rent" else None,
                    bedrooms=int(re.sub(r"\D", "", beds_el.get_text())) if beds_el else None,
                    bathrooms=None,
                    property_type=None,
                    address=addr_el.get_text(strip=True) if addr_el else "",
                    postcode=None,
                    latitude=None,
                    longitude=None,
                    description="",
                    features=[],
                    images=[],
                ))
            except Exception:
                continue
        return props

    def _parse(self, item: dict, listing_type: str) -> Optional[Property]:
        try:
            prop_id = str(item.get("listingId", ""))
            if not prop_id:
                return None

            price = item.get("price", {})
            raw_price = price.get("value")
            coords = item.get("coordinates", {})
            images = [img.get("url", "") for img in item.get("images", [])[:5]]

            return Property(
                id=f"zoopla:{prop_id}",
                source="zoopla",
                listing_type=listing_type,
                url=f"https://www.zoopla.co.uk/details/{prop_id}",
                title=item.get("title", ""),
                price=int(raw_price) if raw_price else None,
                price_frequency="pcm" if listing_type == "rent" else None,
                bedrooms=item.get("beds"),
                bathrooms=item.get("baths"),
                property_type=item.get("propertyType", "").lower(),
                address=item.get("address", ""),
                postcode=item.get("postcode"),
                latitude=coords.get("lat"),
                longitude=coords.get("lng"),
                description=item.get("shortDescription", ""),
                features=item.get("tags", []),
                images=images,
                agent_name=item.get("branch", {}).get("name", ""),
                listed_date=item.get("publishedOn", ""),
            )
        except Exception as e:
            log.debug(f"[zoopla] Parse error: {e}")
            return None
