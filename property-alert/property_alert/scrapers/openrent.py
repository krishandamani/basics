"""OpenRent scraper — direct landlord listings, rent only."""
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from ..models import Property, SearchCriteria
from .base import BaseScraper, make_session, polite_delay

log = logging.getLogger(__name__)


class OpenRentScraper(BaseScraper):
    name = "openrent"

    def _scrape(self, criteria: SearchCriteria) -> list[Property]:
        if criteria.listing_type == "sale":
            log.info("[openrent] Skipping — OpenRent is rent-only")
            return []

        session = make_session()
        session.headers["Referer"] = "https://www.openrent.co.uk/"

        properties = []
        offset = 0
        limit = 20

        while offset < 100:  # cap at 100 results
            url = "https://www.openrent.co.uk/properties-to-rent/"
            params = self._build_params(criteria, offset, limit)
            try:
                resp = session.get(url, params=params, timeout=20)
                if resp.status_code in (403, 429):
                    log.warning(f"[openrent] Blocked ({resp.status_code})")
                    break
                resp.raise_for_status()
            except Exception as e:
                log.error(f"[openrent] Request failed offset {offset}: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            listings = self._extract(soup)
            if not listings:
                break

            properties.extend(listings)
            offset += limit
            polite_delay()

        log.info(f"[openrent] Found {len(properties)} listings")
        return properties

    def _build_params(self, criteria: SearchCriteria, offset: int, limit: int) -> dict:
        params = {
            "term": criteria.location,
            "within": criteria.radius_miles,
            "offset": offset,
            "limit": limit,
        }
        if criteria.min_price:
            params["minPrice"] = criteria.min_price
        if criteria.max_price:
            params["maxPrice"] = criteria.max_price
        if criteria.min_bedrooms:
            params["minBeds"] = criteria.min_bedrooms
        if criteria.max_bedrooms:
            params["maxBeds"] = criteria.max_bedrooms
        return params

    def _extract(self, soup: BeautifulSoup) -> list[Property]:
        props = []
        cards = soup.select(".property-details, [class*='pli '], .listing-item")
        for card in cards:
            prop = self._parse(card)
            if prop:
                props.append(prop)
        return props

    def _parse(self, card) -> Optional[Property]:
        try:
            link = card.select_one("a[href*='/property-to-rent/']")
            if not link:
                return None
            href = link["href"]
            prop_id = re.search(r"/property-to-rent/(\d+)", href)
            if not prop_id:
                return None

            price_el = card.select_one(".price, [class*='price']")
            beds_el = card.select_one("[class*='bed']")
            addr_el = card.select_one("h2, .address, [class*='address']")
            img_el = card.select_one("img")
            desc_el = card.select_one("p, [class*='description']")

            price_text = price_el.get_text(strip=True) if price_el else ""
            price_num = int(re.sub(r"[^\d]", "", price_text.split("p")[0])) if price_text else None

            beds_text = beds_el.get_text(strip=True) if beds_el else ""
            beds = int(re.sub(r"\D", "", beds_text)) if beds_text else None

            return Property(
                id=f"openrent:{prop_id.group(1)}",
                source="openrent",
                listing_type="rent",
                url=f"https://www.openrent.co.uk{href}" if href.startswith("/") else href,
                title=addr_el.get_text(strip=True) if addr_el else "",
                price=price_num,
                price_frequency="pcm",
                bedrooms=beds,
                bathrooms=None,
                property_type=None,
                address=addr_el.get_text(strip=True) if addr_el else "",
                postcode=None,
                latitude=None,
                longitude=None,
                description=desc_el.get_text(strip=True) if desc_el else "",
                features=[],
                images=[img_el["src"]] if img_el and img_el.get("src") else [],
                agent_name="OpenRent (Private Landlord)",
            )
        except Exception as e:
            log.debug(f"[openrent] Parse error: {e}")
            return None
