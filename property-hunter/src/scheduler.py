"""Runs the search cycle on a repeating schedule using the 'schedule' library."""

import time
from datetime import datetime
from typing import List

import schedule

from .database import init_db, is_new, mark_sent, save_property
from .enricher import enrich
from .matcher import filter_properties
from .models import Search
from .notifier import send_digest
from . import scrapers


def run_cycle(config: dict, searches: List[Search]) -> None:
    """One full search cycle: scrape → filter → deduplicate → enrich → email."""
    now = datetime.now().strftime("%H:%M on %d %b")
    print(f"\n{'='*55}")
    print(f"  Property Hunter — running at {now}")
    print(f"{'='*55}")

    all_new_matches = []

    for search in searches:
        print(f"\n» Search: {search.name}")

        raw: list = []
        if search.rightmove_url:
            raw += scrapers.rightmove.scrape(search)
        if search.zoopla_url:
            raw += scrapers.zoopla.scrape(search)
        if search.onthemarket_url:
            raw += scrapers.onthemarket.scrape(search)
        if search.openrent_url:
            raw += scrapers.openrent.scrape(search)

        print(f"  Total scraped: {len(raw)}")

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
        print(f"  Sending digest with {len(all_new_matches)} new propert"
              f"{'ies' if len(all_new_matches) != 1 else 'y'}…")
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

    # Run immediately on startup so you don't wait hours for the first result
    run_cycle(config, searches)

    schedule.every(interval_hours).hours.do(run_cycle, config=config, searches=searches)

    while True:
        schedule.run_pending()
        time.sleep(60)
