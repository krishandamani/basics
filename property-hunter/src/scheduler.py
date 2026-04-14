"""Runs the search cycle on a repeating schedule using the 'schedule' library.

All scraper calls run in parallel (ThreadPoolExecutor) so a full cycle of
18 searches × 2 Apify actors takes ~3-5 min rather than 90+ min sequential.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List

import schedule

from .database import (
    get_stored_price, get_health_alert_sent, init_db,
    is_new, mark_sent, save_property, set_health_alert_sent,
)
from .enricher import enrich
from .matcher import filter_properties
from .models import Search
from .notifier import send_digest, send_health_alert, send_price_drop_alert, send_telegram_alert
from . import scrapers

_USE_APIFY = bool(os.environ.get("APIFY_API_KEY"))

# On cloud (Railway/GitHub Actions), Playwright scrapers OOM — Chromium uses
# 300-500 MB per instance. Only run them locally where memory is abundant and
# cloud IPs aren't blocked by Savills/Knight Frank anyway.
_USE_PLAYWRIGHT = not _USE_APIFY and not os.environ.get("RAILWAY_ENVIRONMENT")

# Keep workers low on cloud to avoid memory pressure from concurrent responses.
# Apify calls are pure network I/O so 6 workers is plenty.
_MAX_WORKERS = int(os.environ.get("SCRAPER_WORKERS", 6 if _USE_APIFY else 4))


def _apify_configured() -> bool:
    """Re-check at runtime in case the env var was set after process start."""
    return bool(os.environ.get("APIFY_API_KEY"))

# Minimum gap between health-alert emails for the same search.
# Stored in DB (not memory) so Railway restarts don't reset the clock.
_HEALTH_ALERT_MIN_INTERVAL = timedelta(hours=12)

# Diagnostics — populated each run_cycle(), read by /api/diagnostics
_last_run_stats: dict = {}


def _build_scraper_defs() -> list:
    if _apify_configured():
        # Cloud mode: Apify handles Rightmove/Zoopla/OTM; OpenRent is lightweight.
        # Savills/KnightFrank are Playwright-only — excluded here to avoid OOM.
        from .scrapers import apify_scraper
        return [
            ("Rightmove",   "rightmove_url",   apify_scraper.scrape_rightmove),
            ("Zoopla",      "zoopla_url",       apify_scraper.scrape_zoopla),
            ("OnTheMarket", "onthemarket_url",  apify_scraper.scrape_onthemarket),
            ("OpenRent",    "openrent_url",     scrapers.openrent.scrape),
        ]
    # Local mode: direct scrapers + Playwright for premium agents (if available).
    defs = [
        ("Rightmove",   "rightmove_url",   scrapers.rightmove.scrape),
        ("Zoopla",      "zoopla_url",      scrapers.zoopla.scrape),
        ("OnTheMarket", "onthemarket_url", scrapers.onthemarket.scrape),
        ("OpenRent",    "openrent_url",    scrapers.openrent.scrape),
    ]
    if _USE_PLAYWRIGHT:
        defs += [
            ("Savills",     "savills_url",     scrapers.savills.scrape),
            ("KnightFrank", "knightfrank_url", scrapers.knightfrank.scrape),
        ]
    return defs


def _do_scrape(scraper_fn, search: Search, source_name: str):
    """Worker: run one scraper, return (search_id, source_name, results, error_msg)."""
    try:
        results = scraper_fn(search)
        return search.id, source_name, results, None
    except Exception as exc:
        msg = f"{source_name}: {exc}"
        print(f"  [{source_name}] Unexpected error: {exc}")
        return search.id, source_name, [], msg


def run_cycle(config: dict, searches: List[Search]) -> None:
    """One full search cycle: scrape (parallel) → filter → deduplicate → enrich → email."""
    _last_run_stats.clear()
    backend = "apify" if _apify_configured() else "direct"
    _last_run_stats["backend"] = backend
    _last_run_stats["errors"] = []

    now = datetime.now().strftime("%H:%M on %d %b")
    print(f"\n{'='*60}")
    print(f"  Property Hunter — {now}")
    print(f"  Backend: {backend}  |  Searches: {len(searches)}")
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
                _, _, results, err = future.result()
                if err:
                    _last_run_stats["errors"].append(err)
            except Exception as exc:
                print(f"  [{source_name}] Error: {exc}")
                results = []
                _last_run_stats["errors"].append(f"{source_name}: {exc}")
            scraped.setdefault(search_id, {})[source_name] = results

    elapsed = time.monotonic() - t0
    total_raw = sum(len(r) for sd in scraped.values() for r in sd.values())
    _last_run_stats["total_raw"] = total_raw
    _last_run_stats["elapsed_s"] = round(elapsed, 1)
    print(f"  Done in {elapsed:.0f}s — {total_raw} total listings fetched\n")

    # ── Phase 2: filter, deduplicate, enrich, save ────────────────────────────
    all_new_matches: list = []
    price_drops: list = []   # (prop, old_price, search)
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

        # Health alert when every enabled source returned nothing.
        # Only fires when Apify IS configured — without it, 0 results from
        # cloud IPs is completely expected (sites block datacentre traffic).
        # Rate-limited to once per 12h per search to survive restarts better.
        if zero_sources and len(zero_sources) == enabled_count:
            if not _apify_configured():
                print(f"  ⚠ All sources returned 0 — suppressed (APIFY_API_KEY not set)")
            else:
                last_sent = get_health_alert_sent(search.id)
                if not last_sent or (datetime.now() - last_sent) > _HEALTH_ALERT_MIN_INTERVAL:
                    print(f"  ⚠ All sources returned 0 — sending health alert")
                    send_health_alert(search.name, zero_sources, config)
                    set_health_alert_sent(search.id)
                else:
                    mins = int((datetime.now() - last_sent).total_seconds() / 60)
                    print(f"  ⚠ All sources returned 0 — health alert suppressed (sent {mins}m ago)")

        matched = filter_properties(raw, search)
        print(f"  Matched criteria: {len(matched)}")

        new_this_search = []
        for prop in matched:
            stored_price = get_stored_price(prop.id)
            if is_new(prop.id, search.id):
                prop = enrich(prop)
                save_property(prop)
                mark_sent(prop.id, search.id)
                new_this_search.append((prop, search))
            elif stored_price and prop.price and prop.price < stored_price:
                # Price drop on a property we've already seen — alert immediately
                prop = enrich(prop)
                prop.previous_price = stored_price
                save_property(prop)
                price_drops.append((prop, stored_price, search))

        print(f"  New (not seen before): {len(new_this_search)}"
              + (f"  |  Price drops: {len([d for d in price_drops if d[2].id == search.id])}" if price_drops else ""))
        all_new_matches.extend(new_this_search)

    # ── Phase 3: send alerts ──────────────────────────────────────────────────
    print()
    if all_new_matches:
        n = len(all_new_matches)
        print(f"  Sending digest + Telegram for {n} new propert{'ies' if n != 1 else 'y'}…")
        send_digest(all_new_matches, config)
        send_telegram_alert(all_new_matches, config)
    else:
        print("  No new matches — no email sent.")

    if price_drops:
        n = len(price_drops)
        print(f"  Sending price drop alerts for {n} propert{'ies' if n != 1 else 'y'}…")
        send_price_drop_alert(price_drops, config)

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
