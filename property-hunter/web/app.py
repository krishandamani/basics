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
    """Test PrimeLocation (Zoopla sister site — same data, less Cloudflare). 5–15 s."""
    if not os.environ.get("APIFY_API_KEY"):
        return jsonify({"error": "APIFY_API_KEY not set"}), 400
    test_url = (
        "https://www.primelocation.com/for-sale/property/hitchin/"
        "?price_min=900000&price_max=1300000&beds_min=3&results_sort=newest_listings"
    )
    try:
        import re as _re, json as _json
        from src.scrapers.zoopla import _get, _extract_listings
        resp = _get(test_url, timeout=20)
        m = _re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>', resp.text, _re.DOTALL)
        props = _extract_listings(_json.loads(m.group(1))) if m else []
        return jsonify({
            "note": "Testing PrimeLocation (Zoopla sister) — Zoopla itself returns 403",
            "test_url": test_url,
            "http_status": resp.status_code,
            "next_data_found": bool(m),
            "properties_parsed": len(props),
            "first_3_raw": props[:3],
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "type": type(exc).__name__}), 500


@app.route("/api/test-onthemarket")
def api_test_onthemarket():
    """Test OnTheMarket — returns __NEXT_DATA__ structure on 0 results for debugging. 5–15 s."""
    if not os.environ.get("APIFY_API_KEY"):
        return jsonify({"error": "APIFY_API_KEY not set"}), 400
    test_url = (
        "https://www.onthemarket.com/for-sale/property/hitchin/"
        "?min-price=900000&max-price=1300000&min-bedrooms=3&sort=newest"
    )
    try:
        import re as _re, json as _json
        from src.scrapers.onthemarket import _get, _find_listings, _parse_html_cards, scrape
        from src.models import Search
        resp = _get(test_url, timeout=20)
        m = _re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>', resp.text, _re.DOTALL)

        debug = {}
        props = []
        raw_nd = None
        if m:
            try:
                raw_nd = _json.loads(m.group(1))
                pp = raw_nd.get("props", {}).get("pageProps", {})
                # Expose structure for debugging
                debug["pageProps_keys"] = list(pp.keys())
                debug["pageProps_types"] = {
                    k: f"{type(v).__name__}[{len(v)}]" if hasattr(v, '__len__') else type(v).__name__
                    for k, v in pp.items()
                }
                results_obj = pp.get("results") or {}
                props = (
                    results_obj.get("results", []) if isinstance(results_obj, dict) else []
                ) or pp.get("properties", []) or pp.get("listings", []) or _find_listings(pp)
            except Exception as e:
                debug["parse_error"] = str(e)

        if not props:
            props = _parse_html_cards(resp.text)
            debug["used_html_fallback"] = True

        return jsonify({
            "test_url": test_url,
            "http_status": resp.status_code,
            "next_data_found": bool(m),
            "properties_parsed": len(props),
            "first_3_raw": props[:3],
            "debug": debug,
            # Show raw __NEXT_DATA__ snippet when 0 results so we can fix the path
            "next_data_snippet": _json.dumps(raw_nd)[:3000] if raw_nd and not props else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "type": type(exc).__name__}), 500


@app.route("/api/test-fineandcountry")
def api_test_fineandcountry():
    """Test Fine & Country — tries multiple URL patterns to find the working one. 15–30 s."""
    if not os.environ.get("APIFY_API_KEY"):
        return jsonify({"error": "APIFY_API_KEY not set"}), 400

    candidates = [
        "https://www.fineandcountry.com/property-search/residential-sales/?q=Hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000",
        "https://www.fineandcountry.com/property-search/?q=Hitchin&section=residential-sales&minBedrooms=3&minPrice=900000&maxPrice=1300000",
        "https://www.fineandcountry.com/search?q=Hitchin&type=residential-sales&minBedrooms=3&minPrice=900000&maxPrice=1300000",
        "https://www.fineandcountry.com/uk/property-search/residential-sales/?q=Hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000",
        "https://www.fineandcountry.com/property-search/?search_type=for-sale&location=Hitchin&min_bedrooms=3&min_price=900000&max_price=1300000",
    ]
    try:
        import re as _re
        from src.scrapers.fineandcountry import _get, _parse_html_cards
        results = []
        for url in candidates:
            try:
                resp = _get(url, timeout=15)
                m = _re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>', resp.text, _re.DOTALL)
                cards = _parse_html_cards(resp.text, "sale") if resp.status_code == 200 else []
                results.append({
                    "url": url,
                    "http_status": resp.status_code,
                    "next_data": bool(m),
                    "html_cards": len(cards),
                    "html_snippet_200": resp.text[500:1200] if resp.status_code == 200 else None,
                })
            except Exception as e:
                results.append({"url": url, "error": str(e)})
        return jsonify({"url_attempts": results})
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
