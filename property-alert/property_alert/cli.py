"""CLI entry point using Click."""
import logging
import os
import sys
from pathlib import Path

import click
import yaml
from tabulate import tabulate

from .config import config_path, load_config, get_searches, get_email_config
from .database import init_db, get_recent, get_stats
from .scheduler import run_search_cycle, run_digest, start as start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


@click.group()
def cli():
    """🏠 property-alert — automated UK property search and email notifications."""


# ── init ──────────────────────────────────────────────────────────────────────

@cli.command()
def init():
    """Interactive setup wizard — creates your config file."""
    cfg_path = config_path()

    if cfg_path.exists():
        if not click.confirm(f"Config already exists at {cfg_path}. Overwrite?"):
            return

    click.echo("\n=== Property Alert Setup ===\n")

    click.echo("Step 1 of 3 — Email")
    click.echo("─" * 40)
    click.echo("Alerts will be SENT from a Gmail address and RECEIVED at your inbox.")
    click.echo("If you don't have a Gmail to use as a sender, create a free one at gmail.com\n")
    email_from = click.prompt("Gmail address to send alerts FROM")
    email_to = click.prompt("Email address to RECEIVE alerts", default="houselistings1234@proton.me")

    click.echo(
        "\nStep 2 of 3 — Gmail App Password"
        "\n─" + "─" * 39 +
        "\nGoogle requires a special App Password for automated tools."
        "\nDo NOT use your regular Gmail password here."
        "\n\nHow to create one (takes ~2 minutes):"
        "\n  1. Go to: myaccount.google.com/apppasswords"
        "\n  2. Sign in to the Gmail you just entered"
        "\n  3. Under 'App name', type: Property Alert"
        "\n  4. Click 'Create' — Google will show a 16-character code"
        "\n  5. Copy and paste it below (spaces don't matter)\n"
    )
    email_password = click.prompt("Gmail App Password (16 characters)", hide_input=True)

    click.echo(
        "\nStep 3 of 3 — Search schedule"
        "\n─" + "─" * 39
    )
    schedule_hours = click.prompt("How often to scrape (hours)", default=6, type=int)
    digest_times_raw = click.prompt("Send digest emails at (comma-separated HH:MM)", default="09:00,18:00")
    digest_times = [t.strip() for t in digest_times_raw.split(",")]

    click.echo("\n── First Search ──")
    search_label = click.prompt("Search name", default="My search")
    listing_type = click.prompt("Listing type", type=click.Choice(["rent", "sale", "both"]), default="rent")
    location = click.prompt("Location (e.g. 'London', 'Guildford, Surrey')")
    radius = click.prompt("Search radius (miles)", default=2, type=int)
    min_price = click.prompt("Min price (PCM for rent, total for sale)", default=0, type=int) or None
    max_price = click.prompt("Max price", default=0, type=int) or None
    min_beds = click.prompt("Min bedrooms", default=0, type=int) or None
    max_beds = click.prompt("Max bedrooms (0 = any)", default=0, type=int) or None
    commute_to = click.prompt("Commute destination (leave blank to skip)", default="")

    google_maps_key = ""
    if commute_to:
        click.echo("\nCommute time requires a free Google Maps API key.")
        click.echo("Get one at: console.cloud.google.com → Enable 'Distance Matrix API'")
        google_maps_key = click.prompt("Google Maps API key (leave blank to skip)", default="")

    epc_key = click.prompt(
        "\nEPC API key (free at epc.opendatacommunities.org, leave blank to skip)",
        default="",
    )

    search_id = search_label.lower().replace(" ", "-")
    cfg = {
        "notification": {
            "email": {"from": email_from, "to": email_to, "password": email_password},
            "digest_times": digest_times,
        },
        "schedule_hours": schedule_hours,
        "google_maps_api_key": google_maps_key,
        "epc_api_key": epc_key,
        "searches": [
            {
                "id": search_id,
                "label": search_label,
                "type": listing_type,
                "sources": ["rightmove", "zoopla", "onthemarket", "openrent"],
                "location": location,
                "radius_miles": radius,
                "min_price": min_price,
                "max_price": max_price,
                "min_bedrooms": min_beds,
                "max_bedrooms": max_beds,
                "property_types": [],
                "keywords_require": [],
                "keywords_exclude": [],
                "enrichment": {
                    "commute_to": commute_to or None,
                    "include_crime": True,
                    "include_epc": bool(epc_key),
                    "include_schools": True,
                },
            }
        ],
    }

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

    init_db()
    click.echo(f"\n✓ Config saved to {cfg_path}")
    click.echo("✓ Database initialised")
    click.echo("\nNext steps:")
    click.echo("  property-alert search    # run a test search right now")
    click.echo("  property-alert run       # start the scheduler daemon")


# ── run ───────────────────────────────────────────────────────────────────────

@cli.command()
def run():
    """Start the scheduler daemon (runs continuously)."""
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    init_db()
    click.echo("Starting scheduler… press Ctrl+C to stop.")
    start_scheduler(cfg)


# ── search ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--source", default=None, help="Only scrape this source (rightmove|zoopla|onthemarket|openrent)")
@click.option("--notify/--no-notify", default=True, help="Send email digest after searching")
@click.option("--dry-run", is_flag=True, help="Print matches without saving or notifying")
def search(source, notify, dry_run):
    """Run one search cycle immediately."""
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    init_db()

    if source:
        for s in cfg.get("searches", []):
            s["sources"] = [source]

    n = run_search_cycle(cfg)
    click.echo(f"Found {n} new matching properties.")

    if notify and not dry_run and n > 0:
        run_digest(cfg)
        click.echo("Digest email sent.")


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show database statistics and config summary."""
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    stats = get_stats()
    searches = get_searches(cfg)

    click.echo("\n=== Property Alert Status ===\n")
    click.echo(f"Config:          {config_path()}")
    click.echo(f"Total properties in DB: {stats['total_properties']}")
    click.echo(f"Total notified:         {stats['total_notified']}")
    if stats["by_source"]:
        click.echo("\nBy source:")
        for src, n in stats["by_source"].items():
            click.echo(f"  {src:<16} {n}")
    click.echo(f"\nActive searches ({len(searches)}):")
    for s in searches:
        click.echo(f"  [{s.listing_type}] {s.label} — {s.location} ({', '.join(s.sources)})")


# ── recent ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("n", default=20, type=int)
def recent(n):
    """Show the N most recently found properties (default 20)."""
    props = get_recent(n)
    if not props:
        click.echo("No properties in database yet. Run `property-alert search` first.")
        return

    rows = []
    for p in props:
        price = f"£{p.price:,}" if p.price else "—"
        if p.price_frequency:
            price += f" {p.price_frequency}"
        rows.append([
            p.source,
            p.listing_type,
            price,
            f"{p.bedrooms}bd" if p.bedrooms else "—",
            (p.address or p.title or "")[:45],
            p.crime_score or "—",
            p.epc_rating or "—",
            f"{p.commute_minutes}m" if p.commute_minutes else "—",
            p.first_seen[:10] if p.first_seen else "—",
        ])

    headers = ["Source", "Type", "Price", "Beds", "Address", "Crime", "EPC", "Commute", "Found"]
    click.echo(tabulate(rows, headers=headers, tablefmt="simple"))


# ── config ────────────────────────────────────────────────────────────────────

@cli.group()
def config():
    """Manage configuration."""


@config.command("show")
def config_show():
    """Print current config (passwords redacted)."""
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    # Redact password
    safe = yaml.dump(cfg, default_flow_style=False)
    safe = safe.replace(cfg.get("notification", {}).get("email", {}).get("password", "REDACTED_NOT_FOUND"), "***")
    click.echo(safe)


@config.command("edit")
def config_edit():
    """Open the config file in your default editor."""
    editor = os.environ.get("EDITOR", "nano")
    os.system(f"{editor} {config_path()}")
