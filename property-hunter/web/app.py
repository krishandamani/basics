"""Property Hunter — web UI.

Run from the property-hunter/ directory:
    python web/app.py

Then open http://localhost:5000 in your browser.
"""

import json
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import (
    DB_PATH, get_web_properties, hide_property,
    init_db, init_web_tables, toggle_favourite,
)

app = Flask(__name__)
app.secret_key = "property-hunter-local"

# ── Background search state ───────────────────────────────────────────────────

_search_state: dict = {"running": False, "last_ran": None, "error": None, "started_at": None}


def _search_state_for_template() -> dict:
    """Return a copy of search state with elapsed_seconds computed, for templates."""
    state = dict(_search_state)
    elapsed = 0
    if state.get("running") and state.get("started_at"):
        try:
            elapsed = int(
                (datetime.now() - datetime.fromisoformat(state["started_at"])).total_seconds()
            )
        except Exception:
            pass
    state["elapsed_seconds"] = elapsed
    state["apify_configured"] = bool(os.environ.get("APIFY_API_KEY"))
    return state


def _run_search_background() -> None:
    _search_state["running"] = True
    _search_state["error"] = None
    _search_state["started_at"] = datetime.now().isoformat()
    try:
        import yaml
        from main import build_searches, _find_config
        from src.scheduler import run_cycle

        cfg_path = _find_config()
        if not cfg_path:
            _search_state["error"] = "No config file found."
            return

        with open(cfg_path) as f:
            config = yaml.safe_load(f) or {}

        env_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
        if env_pw and not config.get("email", {}).get("app_password"):
            config.setdefault("email", {})["app_password"] = env_pw

        searches = build_searches(config)
        if not searches:
            _search_state["error"] = "No searches in config.yaml yet."
            return

        run_cycle(config, searches)
        # Surface any per-source Apify errors into the UI banner
        from src.scheduler import _last_run_stats
        errs = _last_run_stats.get("errors") or []
        if errs:
            _search_state["error"] = errs[0]
        _search_state["last_ran"] = datetime.now().strftime("%-d %b at %H:%M")
    except Exception as exc:
        _search_state["error"] = str(exc)
    finally:
        _search_state["running"] = False
        _search_state["started_at"] = None


# ── Natural language parsing ──────────────────────────────────────────────────

def _parse_nl(query: str) -> dict:
    """Parse a natural-language query into filter criteria via Claude API."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=(
                "Extract property search filters from the user's query. "
                "Return ONLY a JSON object with keys: "
                "listing_type (rent|sale|null), min_price (number|null), "
                "max_price (number|null), min_bedrooms (number|null). "
                "Use null for anything not mentioned."
            ),
            messages=[{"role": "user", "content": query}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except Exception:
        return {}


# ── Geocoding ─────────────────────────────────────────────────────────────────

def _geocode(postcodes: list) -> dict:
    """Bulk-geocode UK postcodes via postcodes.io (free, no key)."""
    if not postcodes:
        return {}
    try:
        import requests as req
        clean = [p.strip() for p in postcodes if p][:100]
        resp = req.post(
            "https://api.postcodes.io/postcodes",
            json={"postcodes": clean},
            timeout=10,
        )
        result: dict = {}
        for item in resp.json().get("result", []):
            if item and item.get("result"):
                result[item["query"]] = (
                    item["result"]["latitude"],
                    item["result"]["longitude"],
                )
        return result
    except Exception:
        return {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _enrich_is_new(props) -> list:
    """Add is_new flag (True if first_seen < 24 h ago) and convert Rows to dicts."""
    cutoff = datetime.now() - timedelta(hours=24)
    out = []
    for p in props:
        d = dict(p)
        try:
            d["is_new"] = datetime.fromisoformat(d["first_seen"]) > cutoff
        except Exception:
            d["is_new"] = False
        out.append(d)
    return out


def _parse_int(val: str):
    try:
        return int(str(val).replace(",", "").replace("£", "").strip())
    except (ValueError, TypeError):
        return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    q             = request.args.get("q", "").strip()
    listing_type  = request.args.get("listing_type", "")
    min_price_raw = request.args.get("min_price", "")
    max_price_raw = request.args.get("max_price", "")
    min_beds_raw  = request.args.get("min_beds", "")
    source        = request.args.get("source", "")
    sort          = request.args.get("sort", "newest")
    new_only      = bool(request.args.get("new_only"))

    nl = _parse_nl(q) if q else {}

    all_props = _enrich_is_new(get_web_properties(
        listing_type  = nl.get("listing_type") or listing_type,
        min_price     = nl.get("min_price")    or _parse_int(min_price_raw),
        max_price     = nl.get("max_price")    or _parse_int(max_price_raw),
        min_bedrooms  = nl.get("min_bedrooms") or _parse_int(min_beds_raw),
        source        = source,
        sort          = sort,
    ))

    new_count = sum(1 for p in all_props if p["is_new"])
    displayed  = [p for p in all_props if p["is_new"]] if new_only else all_props

    active_filter_count = sum(bool(x) for x in [
        listing_type, min_price_raw, max_price_raw, min_beds_raw, source, q
    ])

    # Used in templates to build tab URLs that preserve current filters
    base_params = {k: v for k, v in request.args.items() if k != "new_only"}

    return render_template(
        "index.html",
        properties          = displayed,
        filters             = {
            "q": q, "listing_type": listing_type,
            "min_price": min_price_raw, "max_price": max_price_raw,
            "min_beds": min_beds_raw, "source": source,
            "sort": sort, "new_only": new_only,
        },
        search_state        = _search_state_for_template(),
        nl_active           = bool(q),
        new_count           = new_count,
        total_count         = len(all_props),
        active_filter_count = active_filter_count,
        base_params         = base_params,
    )


@app.route("/favourites")
def favourites():
    props = get_web_properties(favourites_only=True)
    return render_template(
        "favourites.html",
        properties   = _enrich_is_new(props),
        search_state = _search_state_for_template(),
    )


@app.route("/map")
def map_view():
    props = get_web_properties(limit=500)
    postcodes = list({dict(p)["postcode"] for p in props if dict(p).get("postcode")})
    geo = _geocode(postcodes)

    points = []
    for p in props:
        d = dict(p)
        pc = d.get("postcode", "")
        if pc and pc in geo:
            lat, lng = geo[pc]
            points.append({
                "lat": lat, "lng": lng,
                "price": d["price"],
                "address": d["address"] or d["title"],
                "bedrooms": d["bedrooms"],
                "source": d["source"],
                "listing_type": d["listing_type"],
                "url": d["url"],
                "image_url": d.get("image_url") or "",
            })

    return render_template(
        "map.html",
        points       = points,
        search_state = _search_state_for_template(),
    )


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/toggle-favourite/<prop_id>", methods=["POST"])
def api_toggle_favourite(prop_id):
    new_state = toggle_favourite(prop_id)
    return jsonify({"favourited": new_state})


@app.route("/api/hide/<prop_id>", methods=["POST"])
def api_hide(prop_id):
    hide_property(prop_id)
    return jsonify({"hidden": True})


@app.route("/api/run-search", methods=["POST"])
def api_run_search():
    if _search_state["running"]:
        return jsonify({"status": "already_running"})
    threading.Thread(target=_run_search_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/search-status")
def api_search_status():
    state = dict(_search_state)
    if state.get("running") and state.get("started_at"):
        try:
            elapsed_sec = int((datetime.now() - datetime.fromisoformat(state["started_at"])).total_seconds())
            state["elapsed_seconds"] = elapsed_sec
        except Exception:
            state["elapsed_seconds"] = 0
    return jsonify(state)


@app.route("/api/diagnostics")
def api_diagnostics():
    from src.scheduler import _apify_configured, _last_run_stats
    return jsonify({
        "apify_configured": _apify_configured(),
        "last_run": _last_run_stats,
    })


@app.route("/api/test-zoopla")
def api_test_zoopla():
    """Zoopla and PrimeLocation both return 403 (Cloudflare network). Status report only."""
    return jsonify({
        "status": "blocked",
        "note": "Zoopla and PrimeLocation share Cloudflare protection that blocks all datacenter "
                "and residential proxy IPs. Not currently scraped. Rightmove + OTM cover the same "
                "listings — all major UK agents list on both portals.",
    })


@app.route("/api/test-onthemarket")
def api_test_onthemarket():
    """Test OTM async API directly (OTM is a Redux app, __NEXT_DATA__ is empty shell). 5–15 s."""
    if not os.environ.get("APIFY_API_KEY"):
        return jsonify({"error": "APIFY_API_KEY not set"}), 400
    api_url = (
        "https://www.onthemarket.com/async-search/"
        "?channel=property&search-type=for-sale&location-id=hitchin"
        "&min-price=900000&max-price=1300000&min-bedrooms=3"
        "&sort-field=recent&direction=desc&frame-size=10&page=1"
    )
    try:
        from src.scrapers.onthemarket import _get
        resp = _get(api_url, timeout=20)
        try:
            data = resp.json()
        except Exception:
            data = {}
        props = data.get("properties") or data.get("results") or data.get("listings") or []
        return jsonify({
            "api_url": api_url,
            "http_status": resp.status_code,
            "response_keys": list(data.keys()) if isinstance(data, dict) else [],
            "properties_found": len(props),
            "first_3": props[:3],
            # Show raw response snippet when 0 results to debug structure
            "raw_snippet": str(resp.text)[:2000] if not props else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "type": type(exc).__name__}), 500


@app.route("/api/test-fineandcountry")
def api_test_fineandcountry():
    """Test Fine & Country — probes robots.txt + 8 URL patterns to find working search URL."""
    if not os.environ.get("APIFY_API_KEY"):
        return jsonify({"error": "APIFY_API_KEY not set"}), 400
    try:
        import re as _re
        from src.scrapers.fineandcountry import _get, _parse_html_cards

        # First: fetch robots.txt to find allowed paths
        robots_resp = _get("https://www.fineandcountry.com/robots.txt", timeout=10)
        robots_text = robots_resp.text[:2000] if robots_resp.status_code == 200 else "unavailable"

        candidates = [
            "https://www.fineandcountry.com/property-search/",
            "https://www.fineandcountry.com/property-search/residential-for-sale/?q=Hitchin&min_bedrooms=3&min_price=900000&max_price=1300000",
            "https://www.fineandcountry.com/property-for-sale/?location=Hitchin&min_bedrooms=3&min_price=900000&max_price=1300000",
            "https://www.fineandcountry.com/en/property-search/residential-sales/?q=Hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000",
            "https://www.fineandcountry.com/residential-for-sale/en/?q=Hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000",
            "https://www.fineandcountry.com/search/residential-sales/?location=Hitchin&min_beds=3&min_price=900000&max_price=1300000",
            "https://www.fineandcountry.com/property/for-sale/?q=Hitchin",
            "https://www.fineandcountry.com/listings/?q=Hitchin&type=for-sale",
        ]
        results = []
        for url in candidates:
            try:
                resp = _get(url, timeout=12)
                cards = _parse_html_cards(resp.text, "sale") if resp.status_code == 200 else []
                results.append({
                    "url": url,
                    "status": resp.status_code,
                    "cards_found": len(cards),
                    "snippet": resp.text[200:600] if resp.status_code == 200 else None,
                })
            except Exception as e:
                results.append({"url": url, "error": str(e)})
        return jsonify({"robots_txt": robots_text, "url_attempts": results})
    except Exception as exc:
        return jsonify({"error": str(exc), "type": type(exc).__name__}), 500


@app.route("/api/test-rightmove")
def api_test_rightmove():
    """Test the Rightmove proxy scraper against one URL and return raw output.

    No Apify actor used — fetches HTML via proxy and parses __NEXT_DATA__ JSON.
    Call from browser or: curl <host>/api/test-rightmove  (takes 5-15 seconds)
    """
    import os
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        return jsonify({"error": "APIFY_API_KEY not set"}), 400

    test_url = (
        "https://www.rightmove.co.uk/property-for-sale/find.html"
        "?locationIdentifier=REGION%5E643&sortType=6&minBedrooms=3"
        "&minPrice=900000&maxPrice=1300000"
    )

    try:
        from src.scrapers.apify_scraper import _proxy_get, _looks_valid, _parse_rightmove_html

        resp = _proxy_get(test_url, timeout=20)
        valid = _looks_valid(resp.text)
        props = _parse_rightmove_html(resp.text) if valid else []

        return jsonify({
            "test_url": test_url,
            "http_status": resp.status_code,
            "page_looks_valid": valid,
            "properties_parsed": len(props),
            "first_3_raw": props[:3],
            "html_snippet": resp.text[2000:3000] if not valid else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "type": type(exc).__name__}), 500


# ── Startup ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    init_web_tables()
    print()
    print("  Property Hunter")
    print(f"  http://localhost:5000")
    print(f"  DB: {DB_PATH}")
    print()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
