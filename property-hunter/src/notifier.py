"""Send formatted HTML emails via Gmail SMTP."""

import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Tuple

from jinja2 import Environment, FileSystemLoader

from .models import Property, Search

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def _get_credentials(config: dict) -> tuple[str, str]:
    """Return (gmail_address, app_password) from config or environment."""
    email_cfg = config.get("email", {})
    address = email_cfg.get("address", "")
    # App password: config file first, then GMAIL_APP_PASSWORD env var
    app_password = email_cfg.get("app_password") or os.environ.get("GMAIL_APP_PASSWORD", "")
    return address, app_password


def _send(address: str, app_password: str, subject: str, html_body: str) -> bool:
    """Send a single email. Returns True on success."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(address, app_password)
            server.sendmail(address, address, msg.as_string())
        return True
    except smtplib.SMTPAuthenticationError:
        print(
            "[Email] ✗ Authentication failed.\n"
            "  Use a Gmail App Password (not your regular password).\n"
            "  Create one at: https://myaccount.google.com/apppasswords"
        )
        return False
    except Exception as exc:
        print(f"[Email] ✗ Failed: {exc}")
        return False


def send_digest(
    matches: List[Tuple[Property, Search]],
    config: dict,
    subject_prefix: str = "",
) -> None:
    """Send one HTML digest email listing all new matched properties."""
    if not matches:
        return

    address, app_password = _get_credentials(config)
    if not address or not app_password:
        print("[Email] Gmail credentials not configured — skipping notification.")
        print("[Email] Run: python setup_wizard.py  or set GMAIL_APP_PASSWORD env var.")
        return

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("email.html")
    html_body = template.render(
        matches=matches,
        count=len(matches),
        date=datetime.now().strftime("%-d %b %Y"),
    )

    n = len(matches)
    subject = (
        f"{subject_prefix}[Property Alert] {n} new match{'es' if n != 1 else ''}"
        f" — {datetime.now().strftime('%-d %b')}"
    )

    if _send(address, app_password, subject, html_body):
        print(f"[Email] ✓ Digest sent — {n} propert{'ies' if n != 1 else 'y'} to {address}")


def send_health_alert(search_name: str, zero_sources: List[str], config: dict) -> None:
    """Email a warning when all scrapers for a search returned 0 results.

    This usually means a site is temporarily blocking the tool, or the
    search URL is broken. Better to know than to silently get no alerts.
    """
    address, app_password = _get_credentials(config)
    if not address or not app_password:
        return

    now = datetime.now().strftime("%-d %b at %H:%M")
    sources_str = ", ".join(s.title() for s in zero_sources)

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:24px auto;
                border:1px solid #fbbf24;border-radius:8px;overflow:hidden;">
      <div style="background:#fef3c7;padding:20px 24px;border-bottom:1px solid #fbbf24;">
        <h2 style="margin:0;color:#92400e;">⚠️ Scraper warning</h2>
      </div>
      <div style="padding:20px 24px;color:#374151;font-size:15px;line-height:1.6;">
        <p><strong>{sources_str}</strong> returned <strong>0 results</strong>
           for your search "<strong>{search_name}</strong>" on {now}.</p>
        <p>This usually means one of:</p>
        <ul>
          <li>The property sites are temporarily blocking automated access from cloud servers — this is normal and should recover within a few hours</li>
          <li>Your search criteria in <code>config.yaml</code> need updating — check the <code>location</code> field is a valid UK town, city, or postcode</li>
        </ul>
        <p style="color:#6b7280;font-size:13px;">
          Property Hunter will keep running and alert you normally when results return.
        </p>
      </div>
    </div>
    """

    subject = f"[Property Hunter] ⚠️ Scraper warning — {search_name}"
    if _send(address, app_password, subject, html_body):
        print(f"[Email] ⚠ Health alert sent for '{search_name}' ({sources_str} returned 0)")
