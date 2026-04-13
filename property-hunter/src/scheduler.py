"""Runs the search cycle on a repeating schedule using the 'schedule' library.

All scraper calls run in parallel (ThreadPoolExecutor) so a full cycle of
18 searches × 2 Apify actors takes ~3-5 min rather than 90+ min sequential.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Max parallel scraper threads. 12 handles 18 searches × 2-3 scrapers well;
# Apify processes them on its own servers so we're mostly waiting on network.
_MAX_WORKERS = int(os.environ.get("SCRAPER_WORKERS", 12))


def _build_scraper_defs() -> list:
    if _USE_APIFY:
        from .scrapers import apify_scraper
        return [
            ("Rightmove", "rightmove_url", apify_scraper.scrape_rightmove),
            ("Zoopla",    "zoopla_url",    apify_scraper.scrape_zoopla),
            ("OpenRent",  "openrent_url",  scrapers.openrent.scrape),
        ]
    return [
        ("Rightmove",   "rightmove_url",   scrapers.rightmove.scrape),
        ("Zoopla",      "zoopla_url",      scrapers.zoopla.scrape),
        ("OnTheMarket", "onthemarket_url", scrapers.onthemarket.scrape),
        ("OpenRent",    "openrent_url",    scrapers.openrent.scrape),
    ]


def _do_scrape(scraper_fn, search: Search, source_name: str):
    """Worker: run one scraper, return (search_id, source_name, results)."""
    try:
        results = scraper_fn(search)
        return search.id, source_name, results
    except Exception as exc:
        print(f"  [{source_name}] Unexpected error: {exc}")
        return search.id, source_name, []


def run_cycle(config: dict, searches: List[Search]) -> None:
    """One full search cycle: scrape (parallel) → filter → deduplicate → enrich → email."""
    now = datetime.now().strftime("%H:%M on %d %b")
    print(f"\n{'='*60}")
    print(f"  Property Hunter — {now}")
    print(f"  Backend: {'Apify' if _USE_APIFY else 'direct (local)'}"
          f"  |  Searches: {len(searches)}")
    print(f"{'='*60}")

    scraper_defs = _build_scraper_defs()

    # ── Phase 1: fire all scraper calls in parallel ───────────────────────────
    # Build the full task list upfront (one entry per search × enabled scraper)
    all_tasks: list = []
    for search in searches:
        for source_name, url_attr, scraper_fn in scraper_defs:
            if getattr(search, url_attr) or search.location:
                all_tasks.append((scraper_fn, search, source_name))

    print(f"\n  Launching {len(all_tasks)} scraper calls in parallel…")
    t0 = time.monotonic()

    # {search_id: {source_name: [results]}}
    scraped: dict = {}
    workers = min(len(all_tasks), _MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_do_scrape, fn, s, name): (s.id, name)
            for fn, s, name in all_tasks
        }
        for future in as_completed(future_map):
            search_id, source_name = future_map[future]
            try:
                _, _, results = future.result()
            except Exception as exc:
                print(f"  [{source_name}] Error: {exc}")
                results = []
            scraped.setdefault(search_id, {})[source_name] = results

    elapsed = time.monotonic() - t0
    total_raw = sum(len(r) for sd in scraped.values() for r in sd.values())
    print(f"  Done in {elapsed:.0f}s — {total_raw} total listings fetched\n")

    # ── Phase 2: filter, deduplicate, enrich, save ────────────────────────────
    all_new_matches: list = []
    search_by_id = {s.id: s for s in searches}

    for search in searches:
        results_by_source = scraped.get(search.id, {})
        if not results_by_source:
            continue

        zero_sources = [src for src, res in results_by_source.items() if not res]
        enabled_count = len(results_by_source)

        raw: list = []
        for res in results_by_source.values():
            raw.extend(res)

        print(f"» {search.name}")
        print(f"  Scraped: {len(raw)}  |  zero sources: {zero_sources or 'none'}")

        # Health alert when every enabled source returned nothing
        if zero_sources and len(zero_sources) == enabled_count:
            print(f"  ⚠ All sources returned 0 — sending health alert")
            send_health_alert(search.name, zero_sources, config)

        matched = filter_properties(raw, search)
        print(f"  Matched criteria: {len(matched)}")

        new_this_search = []
        for prop in matched:
            if is_new(prop.id, search.id):
                prop = enrich(prop)
                save_property(prop)
                mark_sent(prop.id, search.id)
                new_this_search.append((prop, search))

        print(f"  New (not seen before): {len(new_this_search)}")
        all_new_matches.extend(new_this_search)

    # ── Phase 3: send digest ──────────────────────────────────────────────────
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

    run_cycle(config, searches)

    schedule.every(interval_hours).hours.do(run_cycle, config=config, searches=searches)

    while True:
        schedule.run_pending()
        time.sleep(60)
