"""APScheduler-based scheduler: scrape → match → enrich → notify."""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import load_config, get_searches, get_email_config, get_digest_times, get_schedule_hours
from .database import init_db, save_property, is_notified, mark_notified
from .matcher import filter_properties
from .scrapers import SCRAPERS
from .enrichers import enrich
from .notifiers import send_digest

log = logging.getLogger(__name__)

_pending_digest: list[tuple] = []   # accumulates (Property, SearchCriteria) until digest time


def run_search_cycle(cfg: dict | None = None) -> int:
    """Run one full scrape → match → enrich cycle. Returns number of new matches."""
    global _pending_digest
    if cfg is None:
        cfg = load_config()

    searches = get_searches(cfg)
    if not searches:
        log.warning("No searches defined in config")
        return 0

    new_matches = []

    for criteria in searches:
        listing_types = (
            ["rent", "sale"] if criteria.listing_type == "both" else [criteria.listing_type]
        )

        for ltype in listing_types:
            # Adjust criteria listing_type for this iteration
            from dataclasses import replace
            c = replace(criteria, listing_type=ltype)

            for source_name in c.sources:
                if source_name == "openrent" and ltype == "sale":
                    continue  # OpenRent is rent-only

                scraper_cls = SCRAPERS.get(source_name)
                if not scraper_cls:
                    log.warning(f"Unknown scraper: {source_name}")
                    continue

                scraper = scraper_cls()
                raw = scraper.scrape(c)

                matched = filter_properties(raw, c)

                for prop in matched:
                    is_new = save_property(prop)

                    if not is_notified(prop.id, criteria.id):
                        # Enrich only properties we're about to notify about
                        enrich(prop, c, cfg)
                        save_property(prop)  # update with enrichment data
                        new_matches.append((prop, criteria))
                        mark_notified(prop.id, criteria.id)

    _pending_digest.extend(new_matches)
    log.info(f"Cycle complete — {len(new_matches)} new matches found")
    return len(new_matches)


def run_digest(cfg: dict | None = None) -> None:
    """Send the accumulated digest email then clear the queue."""
    global _pending_digest
    if cfg is None:
        cfg = load_config()

    if not _pending_digest:
        log.info("Digest: nothing to send")
        return

    email_cfg = get_email_config(cfg)
    send_digest(_pending_digest, email_cfg)
    _pending_digest = []


def start(cfg: dict | None = None) -> None:
    """Start the blocking APScheduler daemon."""
    if cfg is None:
        cfg = load_config()

    init_db()

    scheduler = BlockingScheduler(timezone="Europe/London")
    interval_hours = get_schedule_hours(cfg)
    digest_times = get_digest_times(cfg)

    # Scrape every N hours
    scheduler.add_job(
        run_search_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id="scrape",
        kwargs={"cfg": cfg},
        next_run_time=datetime.now(timezone.utc),  # run immediately on start
        misfire_grace_time=300,
    )

    # Send digest at configured times each day
    for time_str in digest_times:
        hour, minute = map(int, time_str.split(":"))
        scheduler.add_job(
            run_digest,
            trigger=CronTrigger(hour=hour, minute=minute, timezone="Europe/London"),
            id=f"digest_{time_str}",
            kwargs={"cfg": cfg},
            misfire_grace_time=300,
        )

    log.info(
        f"Scheduler started — scraping every {interval_hours}h, "
        f"digests at {', '.join(digest_times)}"
    )
    scheduler.start()
