#!/usr/bin/env python3
"""Property Hunter — entry point.

Usage:
    python main.py          # Start the scheduled monitor
    python main.py once     # Run one search cycle and exit (good for testing)
    python main.py recent   # Print the last 20 matched properties
    python setup_wizard.py  # First-time setup
"""

import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

CONFIG_FILE = Path.home() / ".config" / "property-hunter" / "config.yaml"


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print("No config found. Run the setup wizard first:\n")
        print("    python setup_wizard.py\n")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def build_searches(config: dict):
    from src.models import Search

    searches = []
    for s in config.get("searches", []):
        searches.append(
            Search(
                id=s["id"],
                name=s.get("name", s["id"]),
                listing_type=s.get("listing_type", "both"),
                rightmove_url=s.get("rightmove_url") or None,
                zoopla_url=s.get("zoopla_url") or None,
                onthemarket_url=s.get("onthemarket_url") or None,
                openrent_url=s.get("openrent_url") or None,
                min_price=s.get("min_price"),
                max_price=s.get("max_price"),
                min_bedrooms=s.get("min_bedrooms"),
                max_bedrooms=s.get("max_bedrooms"),
                property_types=s.get("property_types") or [],
                keywords_required=s.get("keywords_required") or [],
                keywords_excluded=s.get("keywords_excluded") or [],
            )
        )
    return searches


def cmd_recent():
    from src.database import init_db, recent_properties

    init_db()
    rows = recent_properties(20)
    if not rows:
        print("No properties in the database yet. Run a search first.")
        return
    print(f"\n{'─'*70}")
    print(f"  {'Price':>8}  {'Beds':>4}  {'Source':<12}  Address")
    print(f"{'─'*70}")
    for r in rows:
        price_str = f"£{r['price']:,}" if r["price"] else "—"
        print(
            f"  {price_str:>8}  {r['bedrooms'] or '?':>4}  "
            f"{r['source']:<12}  {r['address']}"
        )
        print(f"  {'':>8}  {'':>4}  {'':12}  {r['url']}")
        print()


def main():
    load_dotenv()
    config = load_config()
    searches = build_searches(config)

    if not searches:
        print("No searches configured. Run: python setup_wizard.py")
        sys.exit(1)

    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    if command == "once":
        from src.scheduler import run_cycle
        from src.database import init_db

        init_db()
        run_cycle(config, searches)

    elif command == "recent":
        cmd_recent()

    else:  # default: run the scheduler loop
        from src.scheduler import start

        start(config, searches)


if __name__ == "__main__":
    main()
