#!/usr/bin/env python3
"""Property Hunter — entry point.

Usage:
    python main.py              # Start the scheduled monitor (keeps running)
    python main.py once         # Run one search cycle and exit (good for testing)
    python main.py test-email   # Send a test email to confirm Gmail is working
    python main.py add-search   # Add a new search without redoing full setup
    python main.py recent       # Print the last 20 matched properties
    python setup_wizard.py      # First-time setup wizard
"""

import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


# ── Config loading ────────────────────────────────────────────────────────────

def _find_config() -> Path | None:
    """Find the config file — checks several locations in priority order."""
    candidates = [
        Path("property-hunter/config/config.yaml"),          # GitHub Actions (repo root)
        Path("config/config.yaml"),                           # local (inside property-hunter/)
        Path.home() / ".config" / "property-hunter" / "config.yaml",  # local wizard
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_config() -> dict:
    path = _find_config()
    if not path:
        print("No config found. Run the setup wizard first:\n")
        print("    python setup_wizard.py\n")
        sys.exit(1)
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    # Inject Gmail App Password from environment variable if not set in config
    env_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    if env_pw and not cfg.get("email", {}).get("app_password"):
        cfg.setdefault("email", {})["app_password"] = env_pw

    return cfg


def _config_path_writable() -> Path:
    """Return the config path to write to (prefer repo-local, fall back to home dir)."""
    for p in [Path("property-hunter/config/config.yaml"), Path("config/config.yaml")]:
        if p.exists():
            return p
    # Fall back to home dir config
    p = Path.home() / ".config" / "property-hunter" / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


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


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_test_email(config: dict) -> None:
    """Send a dummy property email to confirm Gmail SMTP is configured correctly."""
    from datetime import datetime
    from src.models import Property, Search
    from src.notifier import send_digest

    print("\nSending test email to t.guiner2@googlemail.com …")

    fake_prop = Property(
        id="test_000",
        source="rightmove",
        listing_type="rent",
        url="https://www.rightmove.co.uk/properties/000000000",
        price=1850,
        bedrooms=2,
        property_type="Flat",
        address="123 Test Street, London, E1 1AA",
        title="2 bed flat — 123 Test Street, London",
        postcode="E1 1AA",
        epc_rating="C",
        crime_rate="Low",
        first_seen=datetime.now(),
    )
    fake_search = Search(
        id="test",
        name="Test Search",
        listing_type="rent",
    )

    send_digest([(fake_prop, fake_search)], config, subject_prefix="[TEST] ")
    print("\nIf you received the email, Gmail is set up correctly. ✓")
    print("If not, check your App Password and try again.")


def cmd_recent() -> None:
    """Print the 20 most recently matched properties from the local database."""
    from src.database import init_db, recent_properties

    init_db()
    rows = recent_properties(20)
    if not rows:
        print("No properties in the database yet. Run a search first.")
        return
    print(f"\n{'─'*72}")
    print(f"  {'Price':>8}  {'Beds':>4}  {'Source':<14}  Address")
    print(f"{'─'*72}")
    for r in rows:
        price_str = f"£{r['price']:,}" if r["price"] else "—"
        print(f"  {price_str:>8}  {r['bedrooms'] or '?':>4}  {r['source']:<14}  {r['address']}")
        print(f"  {'':>8}  {'':>4}  {'':14}  {r['url']}")
        print()


def cmd_add_search() -> None:
    """Mini wizard — add a single new search to the config file."""
    config_path = _config_path_writable()

    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {"email": {"address": "t.guiner2@googlemail.com", "app_password": ""},
                  "schedule": {"interval_hours": 6}, "searches": []}

    searches = config.get("searches") or []
    search_num = len(searches) + 1

    print("\n── Add a new search ──────────────────────────────────")
    print("Paste your search URL from each site (or press Enter to skip).\n")

    def ask(prompt, default=""):
        val = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        return val or default

    name = ask("Search name (e.g. 'Manchester 3-bed rent')", f"Search {search_num}")

    listing_type = ""
    while listing_type not in ("rent", "sale", "both"):
        listing_type = ask("Rent, sale, or both?", "rent")

    rm_url  = ask("Rightmove URL")
    zp_url  = ask("Zoopla URL")
    otm_url = ask("OnTheMarket URL")
    or_url  = ask("OpenRent URL") if listing_type in ("rent", "both") else ""

    min_price = ask("Min price £")
    max_price = ask("Max price £")
    min_beds  = ask("Min bedrooms")
    max_beds  = ask("Max bedrooms")

    excl_raw = ask("Exclude keywords (comma-separated)", "studio, bedsit")
    excluded = [k.strip() for k in excl_raw.split(",") if k.strip()]

    entry = {
        "id": f"search-{search_num}",
        "name": name,
        "listing_type": listing_type,
        "rightmove_url": rm_url or "",
        "zoopla_url": zp_url or "",
        "onthemarket_url": otm_url or "",
        "openrent_url": or_url or "",
        "property_types": [],
        "keywords_required": [],
        "keywords_excluded": excluded,
    }
    for field, raw in [("min_price", min_price), ("max_price", max_price),
                       ("min_bedrooms", min_beds), ("max_bedrooms", max_beds)]:
        if raw:
            try:
                entry[field] = int(raw.replace(",", "").replace("£", "").strip())
            except ValueError:
                pass

    searches.append(entry)
    config["searches"] = searches

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print(f"\n  ✓ Search '{name}' added to {config_path}")

    if "property-hunter/config" in str(config_path) or "config/config.yaml" in str(config_path):
        print("\n  If you're using GitHub Actions, commit and push this file:")
        print(f"    git add {config_path}")
        print("    git commit -m 'Add new property search'")
        print("    git push")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    if command == "add-search":
        cmd_add_search()
        return

    config = load_config()

    if command == "test-email":
        cmd_test_email(config)
        return

    if command == "recent":
        cmd_recent()
        return

    searches = build_searches(config)
    if not searches:
        print("No searches configured yet.")
        print("Run:  python main.py add-search")
        sys.exit(1)

    if command == "once":
        from src.scheduler import run_cycle
        from src.database import init_db
        init_db()
        run_cycle(config, searches)
    else:
        from src.scheduler import start
        start(config, searches)


if __name__ == "__main__":
    main()
