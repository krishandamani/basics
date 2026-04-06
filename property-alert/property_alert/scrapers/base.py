"""Shared scraper utilities: headers, rate limiting, retries."""
import time
import random
import logging
from abc import ABC, abstractmethod
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models import Property, SearchCriteria

log = logging.getLogger(__name__)

# Realistic browser headers — rotate User-Agent occasionally
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return session


def polite_delay(min_s: float = 1.5, max_s: float = 4.0) -> None:
    """Sleep a random amount to avoid triggering rate limits."""
    time.sleep(random.uniform(min_s, max_s))


class BaseScraper(ABC):
    name: str = "base"

    def scrape(self, criteria: SearchCriteria) -> list[Property]:
        """Entry point. Returns a list of Property objects matching the criteria."""
        log.info(f"[{self.name}] Scraping for: {criteria.label}")
        try:
            return self._scrape(criteria)
        except Exception as e:
            log.error(f"[{self.name}] Scrape failed: {e}")
            return []

    @abstractmethod
    def _scrape(self, criteria: SearchCriteria) -> list[Property]:
        ...
