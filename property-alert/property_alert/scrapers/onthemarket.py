"""OnTheMarket scraper — listings appear here up to 24h before Rightmove/Zoopla."""
import logging
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from ..models import Property, SearchCriteria
from .base import BaseScraper, make_session, polite_delay

log = logging.getLogger(__name__)


class OnTheMarketScraper(BaseScraper):
    name = "onthemarket"

    def _scrape(self, criteria: SearchCriteria) -> list[Property]:
        session = make_session()
        session.headers["Referer"] = "https://www.onthemarket.com/"

        action = "to-rent" if criteria.listing_type == "rent" else "for-sale"
        location_slug = criteria.location.lower().replace(" ", "-").replace(",", "")

        properties = []
        page = 1

        while page <= 5:
            url = f"https://www.onthemarket.com/{action}/{location_slug}/"
            params = self._build_params(criteria, page)
            try:
                resp = session.get(url, params=params, timeout=20)
                if resp.status_code in (403, 429):
                    log.warning(f"[onthemarket] Blocked ({resp.status_code})")
                    break
                resp.raise_for_status()
            except Exception as e:
                log.error(f"[onthemarket] Request failed page {page}: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            listings = self._extract(soup, criteria.listing_type)
            if not listings:
                break

            properties.extend(listings)
            page += 1
            polite_delay()

        log.info(f"[onthemarket] Found {len(properties)} listings")
        return properties

    def _build_params(self, criteria: SearchCriteria, page: int) -> dict:
        params = {"page": page, "view": "grid"}
        if criteria.radius_miles:
            params["radius"] = criteria.radius_miles
        if criteria.min_price:
            params["min-price"] = criteria.min_price
        if criteria.max_price:
            params["max-price"] = criteria.max_price
        if criteria.min_bedrooms:
            params["min-bedrooms"] = criteria.min_bedrooms
        if criteria.max_bedrooms:
            params["max-bedrooms"] = criteria.max_bedrooms
        return params

    def _extract(self, soup: BeautifulSoup, listing_type: str) -> list[Property]:
        """Try JSON data blob first, fall back to HTML parsing."""
        # OTM embeds listing data in a <script type="application/json"> tag
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string or "")
                if "properties" in data or "listings" in data:
                    raw = data.get("properties", data.get("listings", []))
                    return [p for p in (self._parse_json(item, listing_type) for item in raw) if p]
            except Exception:
                continue

        # HTML fallback
        return self._parse_html(soup, listing_type)

    def _parse_json(self, item: dict, listing_type: str) -> Optional[Property]:
        try:
            prop_id = str(item.get("id", ""))
            if not prop_id:
                return None
            price = item.get("price", {})
            return Property(
                id=f"onthemarket:{prop_id}",
                source="onthemarket",
                listing_type=listing_type,
                url=f"https://www.onthemarket.com/details/{prop_id}",
                title=item.get("summary", ""),
                price=price.get("amount"),
                price_frequency="pcm" if listing_type == "rent" else None,
                bedrooms=item.get("bedrooms"),
                bathrooms=item.get("bathrooms"),
                property_type=item.get("propertyType", "").lower(),
                address=item.get("address", {}).get("displayAddress", ""),
                postcode=item.get("address", {}).get("postcode"),
                latitude=item.get("location", {}).get("lat"),
                longitude=item.get("location", {}).get("lng"),
                description=item.get("description", ""),
                features=item.get("keyFeatures", []),
                images=[img.get("src", "") for img in item.get("images", [])[:5]],
                agent_name=item.get("agent", {}).get("name", ""),
            )
        except Exception as e:
            log.debug(f"[onthemarket] JSON parse error: {e}")
            return None

    def _parse_html(self, soup: BeautifulSoup, listing_type: str) -> list[Property]:
        props = []
        cards = soup.select("li.otm-PropertyCard, article.property-card")
        for card in cards:
            try:
                link = card.select_one("a[href*='/details/']")
                if not link:
                    continue
                href = link["href"]
                prop_id = re.search(r"/details/([^/]+)", href)
                if not prop_id:
                    continue

                price_el = card.select_one(".otm-Price, .property-price")
                beds_el = card.select_one(".otm-Bedrooms, [class*='bedroom']")
                addr_el = card.select_one(".otm-PropertyCardInfo, address")
                img_el = card.select_one("img")

                price_text = price_el.get_text(strip=True) if price_el else ""
                price_num = int(re.sub(r"[^\d]", "", price_text)) if price_text else None

                props.append(Property(
                    id=f"onthemarket:{prop_id.group(1)}",
                    source="onthemarket",
                    listing_type=listing_type,
                    url=f"https://www.onthemarket.com{href}" if href.startswith("/") else href,
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
                    images=[img_el["src"]] if img_el and img_el.get("src") else [],
                ))
            except Exception:
                continue
        return props
