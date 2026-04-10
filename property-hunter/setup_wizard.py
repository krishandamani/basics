#!/usr/bin/env python3
"""Property Hunter — first-time setup wizard.
Guides you through creating your config file without editing any files manually.
Run with:  python setup_wizard.py
"""

import os
import sys
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".config" / "property-hunter"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

_SEP = "─" * 60


def _ask(prompt: str, default: str = "") -> str:
    if default:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val or default
    val = input(f"  {prompt}: ").strip()
    return val


def _ask_int(prompt: str, default: int = None) -> int:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return int(raw)
        except ValueError:
            print("    Please enter a whole number.")


def _ask_yn(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    val = input(f"  {prompt}{suffix}: ").strip().lower()
    if not val:
        return default
    return val.startswith("y")


def _print_step(n: int, title: str):
    print(f"\n{_SEP}")
    print(f"  Step {n}: {title}")
    print(_SEP)


def main():
    print("\n" + "=" * 60)
    print("  🏠  Property Hunter — Setup Wizard")
    print("=" * 60)
    print("\nThis wizard takes about 5 minutes.")
    print("It will walk you through everything — no file editing needed.\n")

    config = {}

    # ── Step 1: Gmail ────────────────────────────────────────────────
    _print_step(1, "Gmail — for sending and receiving alerts")
    print("""
  Your alerts will be sent FROM and TO: t.guiner2@googlemail.com
  (You'll see them in your normal Gmail inbox.)

  You need a Gmail "App Password" — a 16-character code that lets
  this tool send email on your behalf.  It is NOT your Gmail password.

  How to get one (takes ~2 minutes):
    1. Go to: https://myaccount.google.com/apppasswords
    2. Sign in with t.guiner2@googlemail.com
    3. Click "Select app" → choose "Mail"
    4. Click "Select device" → choose "Other" → type "Property Hunter"
    5. Click "Generate" — copy the 16-character code shown
    6. Paste it below (spaces are fine — they'll be removed)
""")

    app_password = _ask("Paste your Gmail App Password here").replace(" ", "")
    while len(app_password) < 16:
        print("  That looks too short — an App Password is 16 characters.")
        app_password = _ask("Try again").replace(" ", "")

    config["email"] = {
        "address": "t.guiner2@googlemail.com",
        "app_password": app_password,
    }
    print("  ✓ Email configured.")

    # ── Step 2: Schedule ─────────────────────────────────────────────
    _print_step(2, "How often to check for new properties")
    print("""
  The tool will check all your search URLs on a repeating schedule.
  Recommended: every 6 hours (4 checks per day).
  You can set it lower (e.g. 2 hours) if you're in a competitive market.
""")
    interval = _ask_int("Check every how many hours?", default=6)
    config["schedule"] = {"interval_hours": interval}
    print(f"  ✓ Will check every {interval} hour(s).")

    # ── Step 3: Searches ─────────────────────────────────────────────
    _print_step(3, "Your property searches")
    print("""
  For each search you want to run, you'll paste the URL directly
  from your browser.  Just open the site, do your usual search,
  and copy the address bar URL.

  You can set up multiple independent searches
  (e.g. one for renting, one for buying, different areas).
""")

    searches = []
    search_count = 1

    while True:
        print(f"\n  ── Search #{search_count} ──")

        name = _ask(f"Name for this search (e.g. 'London 2-bed rent')")
        if not name:
            name = f"Search {search_count}"

        listing_type = ""
        while listing_type not in ("rent", "sale", "both"):
            listing_type = _ask("Renting or buying? (rent / sale / both)", default="rent")

        print("\n  Now paste your search URLs.")
        print("  Leave blank (just press Enter) to skip a site.\n")

        print("  RIGHTMOVE: Go to rightmove.co.uk → search → copy URL")
        rm_url = _ask("Rightmove URL")

        print("\n  ZOOPLA: Go to zoopla.co.uk → search → copy URL")
        zp_url = _ask("Zoopla URL")

        print("\n  ONTHEMARKET: Go to onthemarket.com → search → copy URL")
        otm_url = _ask("OnTheMarket URL")

        if listing_type in ("rent", "both"):
            print("\n  OPENRENT: Go to openrent.co.uk → search → copy URL")
            or_url = _ask("OpenRent URL")
        else:
            or_url = ""

        print("\n  Price filters (press Enter to skip):")
        min_price = _ask("Minimum price (£)")
        max_price = _ask("Maximum price (£)")

        print("\n  Bedroom filters (press Enter to skip):")
        min_beds = _ask("Minimum bedrooms")
        max_beds = _ask("Maximum bedrooms")

        print("\n  Exclude properties containing these words (comma-separated, or Enter to skip):")
        print("  e.g. studio, bedsit, room only")
        excl_raw = _ask("Exclude keywords")
        excluded = [k.strip() for k in excl_raw.split(",") if k.strip()] if excl_raw else []

        search_cfg = {
            "id": f"search-{search_count}",
            "name": name,
            "listing_type": listing_type,
            "rightmove_url": rm_url or "",
            "zoopla_url": zp_url or "",
            "onthemarket_url": otm_url or "",
            "openrent_url": or_url or "",
            "property_types": [],
            "keywords_required": [],
            "keywords_excluded": excluded or ["studio", "bedsit"],
        }

        for field, raw in [("min_price", min_price), ("max_price", max_price),
                           ("min_bedrooms", min_beds), ("max_bedrooms", max_beds)]:
            if raw:
                try:
                    search_cfg[field] = int(raw.replace(",", "").replace("£", "").strip())
                except ValueError:
                    pass

        searches.append(search_cfg)
        search_count += 1

        if not _ask_yn("\n  Add another search?", default=False):
            break

    config["searches"] = searches

    # ── Save config ──────────────────────────────────────────────────
    _print_step(4, "Saving your config")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print(f"  ✓ Config saved to: {CONFIG_FILE}")

    # ── Done ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  ✅  Setup complete!")
    print(f"{'='*60}")
    print("""
  What to do next:

  1. Test it right now (runs one search, then stops):
         python main.py once

  2. Start the full scheduler (runs continuously):
         python main.py

  3. See the most recent matches found:
         python main.py recent

  Alerts will appear in your Gmail inbox at:
  t.guiner2@googlemail.com
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        sys.exit(0)
