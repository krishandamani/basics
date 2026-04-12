"""Runs the search cycle on a repeating schedule using the 'schedule' library."""

import os
import time
from datetime import datetime
from typing import List

import schedule

from .database import init_db, is_new, mark_sent, save_property
from .enricher import enrich
from .matcher import filter_properties
from .models import Search
from .notifier import send_digest, send_health_alert
from . import scrapers

_USE_APIFY = bool(os.environ.get("APIFY_API_KEY"))


def run_cycle(config: dict, searches: List[Search]) -> None:
    """One full search cycle: scrape → filter → deduplicate → enrich → email."""
    now = datetime.now().strftime("%H:%M on %d %b")
    print(f"\n{'='*55}")
    print(f"  Property Hunter — running at {now}")
    print(f"  Backend: {'Apify' if _USE_APIFY else 'direct scrapers (local)'}")
    print(f"{'='*55}")

    all_new_matches = []

    for search in searches:
        print(f"\n» Search: {search.name}")

        # Track which sources were enabled but returned nothing
        zero_sources = []

        raw: list = []
        if _USE_APIFY:
            from .scrapers import apify_scraper
            scraper_defs = [
                ("Rightmove", "rightmove_url", apify_scraper.scrape_rightmove),
                ("Zoopla",    "zoopla_url",    apify_scraper.scrape_zoopla),
                ("OpenRent",  "openrent_url",  scrapers.openrent.scrape),
            ]
        else:
            scraper_defs = [
                ("Rightmove",   "rightmove_url",   scrapers.rightmove.scrape),
                ("Zoopla",      "zoopla_url",      scrapers.zoopla.scrape),
                ("OnTheMarket", "onthemarket_url",  scrapers.onthemarket.scrape),
                ("OpenRent",    "openrent_url",    scrapers.openrent.scrape),
            ]

        for source_name, url_attr, scraper_fn in scraper_defs:
            # Run if a specific URL is set OR if a location is provided (auto-build URL)
            if getattr(search, url_attr) or search.location:
                results = scraper_fn(search)
                if len(results) == 0:
                    zero_sources.append(source_name)
                raw += results

        print(f"  Total scraped: {len(raw)}")

        # Health alert: if every enabled source returned zero, something is probably broken
        enabled_count = sum(
            1 for _, attr, _ in scraper_defs
            if getattr(search, attr) or search.location
        )
        if zero_sources and len(zero_sources) == enabled_count:
            print(f"  ⚠ All sources returned 0 — sending health alert")
            send_health_alert(search.name, zero_sources, config)

        matched = filter_properties(raw, search)
        print(f"  Match criteria: {len(matched)}")

        new_this_search = []
        for prop in matched:
            if is_new(prop.id, search.id):
                prop = enrich(prop)
                save_property(prop)
                mark_sent(prop.id, search.id)
                new_this_search.append((prop, search))

        print(f"  New (not seen before): {len(new_this_search)}")
        all_new_matches.extend(new_this_search)

    print()
    if all_new_matches:
        n = len(all_new_matches)
        print(f"  Sending digest with {n} new propert{'ies' if n != 1 else 'y'}…")
        send_digest(all_new_matches, config)
    else:
        print("  No new matches — no email sent.")

    next_run = schedule.next_run()
    if next_run:
        print(f"  Next check: {next_run.strftime('%H:%M on %d %b')}")


def start(config: dict, searches: List[Search]) -> None:
    """Initialise the database, run once immediately, then loop on schedule."""
    init_db()

    interval_hours = config.get("schedule", {}).get("interval_hours", 6)

    print(f"\nProperty Hunter started — checking every {interval_hours} hour(s).")
    print("Press Ctrl+C to stop.\n")

    # Run immediately on startup so you don't wait for the first result
    run_cycle(config, searches)

    schedule.every(interval_hours).hours.do(run_cycle, config=config, searches=searches)

    while True:
        schedule.run_pending()
        time.sleep(60)
