"""Send a formatted HTML digest email via Gmail SMTP."""

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


def send_digest(matches: List[Tuple[Property, Search]], config: dict) -> None:
    """Send one HTML digest email listing all new matched properties.

    Args:
        matches:  List of (Property, Search) pairs — each property + the search that found it.
        config:   Parsed config.yaml dict (needs config['email']['address'] and ['app_password']).
    """
    if not matches:
        return

    email_cfg = config.get("email", {})
    address = email_cfg.get("address", "")
    app_password = email_cfg.get("app_password", "")

    if not address or not app_password:
        print("[Email] Gmail credentials not configured — skipping notification.")
        print("[Email] Run: python setup_wizard.py  to set them up.")
        return

    # Render HTML using the Jinja2 template
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("email.html")
    html_body = template.render(
        matches=matches,
        count=len(matches),
        date=datetime.now().strftime("%-d %b %Y"),
    )

    # Build the email
    n = len(matches)
    subject = (
        f"[Property Alert] {n} new match{'es' if n != 1 else ''} "
        f"— {datetime.now().strftime('%-d %b')}"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address  # sending to yourself
    msg.attach(MIMEText(html_body, "html"))

    # Send via Gmail SMTP (TLS)
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(address, app_password)
            server.sendmail(address, address, msg.as_string())
        print(f"[Email] ✓ Digest sent — {n} propert{'ies' if n != 1 else 'y'} to {address}")
    except smtplib.SMTPAuthenticationError:
        print(
            "[Email] ✗ Authentication failed.\n"
            "  Make sure you're using a Gmail App Password, not your regular password.\n"
            "  Create one at: https://myaccount.google.com/apppasswords"
        )
    except Exception as exc:
        print(f"[Email] ✗ Failed to send: {exc}")
