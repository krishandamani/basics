"""Gmail SMTP email digest notifier."""
import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from ..models import Property, SearchCriteria

log = logging.getLogger(__name__)

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


def send_digest(
    matches: list[tuple[Property, SearchCriteria]],
    email_cfg: dict,
) -> bool:
    """Send a digest email for all matched properties grouped by search.

    Args:
        matches: list of (Property, SearchCriteria) pairs
        email_cfg: dict with keys: from, to, password
    Returns:
        True on success
    """
    if not matches:
        log.info("[email] No matches to send")
        return True

    from_addr = email_cfg.get("from", "")
    to_addr = email_cfg.get("to", "")
    password = email_cfg.get("password", "")

    if not all([from_addr, to_addr, password]):
        log.error("[email] Missing email credentials in config")
        return False

    # Group by search label
    by_search: dict[str, list[Property]] = {}
    search_labels: dict[str, str] = {}
    for prop, criteria in matches:
        by_search.setdefault(criteria.id, []).append(prop)
        search_labels[criteria.id] = criteria.label

    count = len(matches)
    search_summary = ", ".join(search_labels.values())
    today = date.today().strftime("%-d %b")

    subject = f"[Property Alert] {count} new match{'es' if count != 1 else ''} — {search_summary}, {today}"
    html = _build_html(by_search, search_labels)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(_build_plain(by_search, search_labels), "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        log.info(f"[email] Sent digest: {count} properties to {to_addr}")
        return True
    except smtplib.SMTPAuthenticationError:
        log.error(
            "[email] Authentication failed. Make sure you're using a Gmail App Password, "
            "not your regular password. Generate one at: "
            "Google Account → Security → 2-Step Verification → App Passwords"
        )
        return False
    except Exception as e:
        log.error(f"[email] Send failed: {e}")
        return False


def _price_str(prop: Property) -> str:
    if prop.price is None:
        return "Price not listed"
    formatted = f"£{prop.price:,}"
    if prop.price_frequency:
        formatted += f" {prop.price_frequency}"
    return formatted


def _crime_badge(score: Optional[str]) -> str:
    colours = {"Low": "#22c55e", "Medium": "#f59e0b", "High": "#ef4444"}
    if not score:
        return ""
    colour = colours.get(score, "#6b7280")
    return f'<span style="background:{colour};color:white;padding:2px 8px;border-radius:12px;font-size:12px">{score} crime</span>'


def _epc_badge(rating: Optional[str]) -> str:
    colours = {
        "A": "#22c55e", "B": "#84cc16", "C": "#a3e635",
        "D": "#fbbf24", "E": "#f97316", "F": "#ef4444", "G": "#dc2626",
    }
    if not rating:
        return ""
    colour = colours.get(rating.upper(), "#6b7280")
    return f'<span style="background:{colour};color:white;padding:2px 8px;border-radius:12px;font-size:12px">EPC {rating}</span>'


def _property_card(prop: Property) -> str:
    price = _price_str(prop)
    beds = f"{prop.bedrooms} bed" if prop.bedrooms else ""
    baths = f" · {prop.bathrooms} bath" if prop.bathrooms else ""
    ptype = prop.property_type.title() if prop.property_type else ""
    addr = prop.address or prop.title or "Address not listed"
    image_html = (
        f'<img src="{prop.images[0]}" style="width:100%;max-height:200px;object-fit:cover;border-radius:8px 8px 0 0">'
        if prop.images else ""
    )
    badges = " ".join(filter(None, [_crime_badge(prop.crime_score), _epc_badge(prop.epc_rating)]))
    commute = f'<p style="margin:4px 0;color:#6b7280;font-size:13px">🚇 {prop.commute_minutes} min commute</p>' if prop.commute_minutes else ""
    school = (
        f'<p style="margin:4px 0;color:#6b7280;font-size:13px">🏫 {prop.nearest_school} ({prop.nearest_school_rating})</p>'
        if prop.nearest_school else ""
    )
    crime = f'<p style="margin:4px 0;color:#6b7280;font-size:13px">🚨 {prop.crime_summary}</p>' if prop.crime_summary else ""
    avg_price = (
        f'<p style="margin:4px 0;color:#6b7280;font-size:13px">📊 Area avg: £{prop.avg_sold_price:,}</p>'
        if prop.avg_sold_price else ""
    )

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:8px;margin-bottom:24px;font-family:sans-serif;overflow:hidden">
      {image_html}
      <div style="padding:16px">
        <h3 style="margin:0 0 4px 0;font-size:18px">{addr}</h3>
        <p style="margin:0 0 8px 0;font-size:22px;font-weight:bold;color:#1d4ed8">{price}</p>
        <p style="margin:0 0 8px 0;color:#374151">{beds}{baths} · {ptype}</p>
        <div style="margin-bottom:8px">{badges}</div>
        {commute}{school}{crime}{avg_price}
        <a href="{prop.url}" style="display:inline-block;margin-top:12px;padding:8px 20px;background:#1d4ed8;color:white;border-radius:6px;text-decoration:none;font-size:14px">
          View on {prop.source.title()} →
        </a>
      </div>
    </div>
    """


def _build_html(by_search: dict, search_labels: dict) -> str:
    sections = ""
    for search_id, props in by_search.items():
        label = search_labels[search_id]
        cards = "".join(_property_card(p) for p in props)
        sections += f"""
        <h2 style="font-family:sans-serif;color:#111827;border-bottom:2px solid #e5e7eb;padding-bottom:8px">
          {label} — {len(props)} new listing{'s' if len(props) != 1 else ''}
        </h2>
        {cards}
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="max-width:680px;margin:0 auto;padding:24px;background:#f9fafb">
      <div style="background:white;padding:32px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
        <h1 style="font-family:sans-serif;color:#111827;margin-top:0">🏠 Property Alert</h1>
        {sections}
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0">
        <p style="font-family:sans-serif;font-size:12px;color:#9ca3af">
          You're receiving this because you set up property-alert.
          Run <code>property-alert config show</code> to review your search criteria.
        </p>
      </div>
    </body>
    </html>
    """


def _build_plain(by_search: dict, search_labels: dict) -> str:
    lines = ["Property Alert Digest", "=" * 40, ""]
    for search_id, props in by_search.items():
        label = search_labels[search_id]
        lines.append(f"{label} — {len(props)} new listing(s)")
        lines.append("-" * 40)
        for p in props:
            lines.append(f"  {p.address or p.title}")
            lines.append(f"  {_price_str(p)}")
            if p.bedrooms:
                lines.append(f"  {p.bedrooms} bed")
            if p.commute_minutes:
                lines.append(f"  Commute: {p.commute_minutes} min")
            if p.crime_score:
                lines.append(f"  Crime: {p.crime_score}")
            if p.epc_rating:
                lines.append(f"  EPC: {p.epc_rating}")
            lines.append(f"  {p.url}")
            lines.append("")
    return "\n".join(lines)
