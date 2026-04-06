from .rightmove import RightmoveScraper
from .zoopla import ZooplaScraper
from .onthemarket import OnTheMarketScraper
from .openrent import OpenRentScraper

SCRAPERS = {
    "rightmove": RightmoveScraper,
    "zoopla": ZooplaScraper,
    "onthemarket": OnTheMarketScraper,
    "openrent": OpenRentScraper,
}
