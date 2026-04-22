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


def _score_property(p: dict) -> tuple[int, list]:
    """Score a property 0-100 based on enrichment signals. Returns (score, reasons)."""
    score = 0
    reasons = []

    school = p.get("school_rating") or ""
    if school == "Outstanding":
        score += 30; reasons.append("🎓 Outstanding school")
    elif school == "Good":
        score += 20; reasons.append("🎓 Good school")

    crime = p.get("crime_rate") or ""
    if crime == "Low":
        score += 20; reasons.append("🔒 Low crime")
    elif crime == "Medium":
        score += 5
    elif crime == "High":
        score -= 10

    epc = p.get("epc_rating") or ""
    if epc in ("A", "B"):
        score += 15; reasons.append(f"⚡ EPC {epc}")
    elif epc == "C":
        score += 10; reasons.append("⚡ EPC C")
    elif epc == "D":
        score += 5

    commute = p.get("commute_minutes") or 0
    if 0 < commute <= 30:
        score += 20; reasons.append(f"🚂 {commute}min to London")
    elif commute <= 40:
        score += 15; reasons.append(f"🚂 {commute}min to London")
    elif commute <= 50:
        score += 10; reasons.append(f"🚂 {commute}min to London")

    dist = p.get("station_distance_miles") or 0
    if 0 < dist <= 0.5:
        score += 15; reasons.append(f"🚉 {dist}mi to station")
    elif dist <= 1.0:
        score += 10; reasons.append(f"🚉 {dist}mi to station")
    elif dist <= 2.0:
        score += 5

    if p.get("previous_price") and p.get("price") and p["price"] < p["previous_price"]:
        score += 10; reasons.append("💰 Price reduced")

    return max(score, 0), reasons


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
    """Add is_new and days_ago fields; convert Rows to dicts."""
    now = datetime.now()
    cutoff = now - timedelta(hours=24)
    out = []
    for p in props:
        d = dict(p)
        try:
            fs = datetime.fromisoformat(d["first_seen"])
            d["is_new"] = fs > cutoff
            days = (now - fs).days
            d["days_ago"] = None if days == 0 else f"{days}d ago"
        except Exception:
            d["is_new"] = False
            d["days_ago"] = None
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
    q                = request.args.get("q", "").strip()
    listing_type     = request.args.get("listing_type", "")
    min_price_raw    = request.args.get("min_price", "")
    max_price_raw    = request.args.get("max_price", "")
    min_beds_raw     = request.args.get("min_beds", "")
    source           = request.args.get("source", "")
    property_type    = request.args.get("property_type", "")
    keyword          = request.args.get("keyword", "").strip()
    sort             = request.args.get("sort", "newest")
    new_only         = bool(request.args.get("new_only"))

    nl = _parse_nl(q) if q else {}

    all_props = _enrich_is_new(get_web_properties(
        listing_type  = nl.get("listing_type") or listing_type,
        min_price     = nl.get("min_price")    or _parse_int(min_price_raw),
        max_price     = nl.get("max_price")    or _parse_int(max_price_raw),
        min_bedrooms  = nl.get("min_bedrooms") or _parse_int(min_beds_raw),
        source        = source,
        property_type = property_type,
        keyword       = keyword,
        sort          = sort,
    ))

    new_count = sum(1 for p in all_props if p["is_new"])
    displayed  = [p for p in all_props if p["is_new"]] if new_only else all_props

    active_filter_count = sum(bool(x) for x in [
        listing_type, min_price_raw, max_price_raw, min_beds_raw, source, q,
        property_type, keyword,
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
            "property_type": property_type, "keyword": keyword,
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
    listing_type  = request.args.get("listing_type", "")
    min_price_raw = request.args.get("min_price", "")
    max_price_raw = request.args.get("max_price", "")
    min_beds_raw  = request.args.get("min_beds", "")
    property_type = request.args.get("property_type", "")
    source        = request.args.get("source", "")

    props = get_web_properties(
        listing_type  = listing_type,
        min_price     = _parse_int(min_price_raw),
        max_price     = _parse_int(max_price_raw),
        min_bedrooms  = _parse_int(min_beds_raw),
        property_type = property_type,
        source        = source,
        limit         = 500,
    )
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
                "property_type": d.get("property_type") or "",
                "source": d["source"],
                "listing_type": d["listing_type"],
                "url": d["url"],
                "image_url": d.get("image_url") or "",
                "nearest_station": d.get("nearest_station") or "",
                "station_distance_miles": d.get("station_distance_miles"),
                "commute_minutes": d.get("commute_minutes"),
                "epc_rating": d.get("epc_rating") or "",
            })

    map_filters = {
        "listing_type": listing_type,
        "min_price": min_price_raw,
        "max_price": max_price_raw,
        "min_beds": min_beds_raw,
        "property_type": property_type,
        "source": source,
    }
    active_map_filter_count = sum(bool(v) for v in map_filters.values())

    return render_template(
        "map.html",
        points                 = points,
        map_filters            = map_filters,
        active_map_filter_count = active_map_filter_count,
        search_state           = _search_state_for_template(),
    )


@app.route("/recommended")
def recommended():
    listing_type = request.args.get("listing_type", "")
    props = get_web_properties(
        listing_type=listing_type,
        sort="newest",
        limit=500,
    )
    enriched = _enrich_is_new(props)

    scored = []
    for p in enriched:
        score, reasons = _score_property(p)
        if score > 0:
            p = dict(p)
            p["score"] = score
            p["reasons"] = reasons
            scored.append(p)

    scored.sort(key=lambda p: p["score"], reverse=True)
    top = scored[:20]

    return render_template(
        "recommended.html",
        properties   = top,
        listing_type = listing_type,
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


@app.route("/api/db-status")
def api_db_status():
    """Show which DB backend is active and row counts — useful for diagnosing duplicate emails."""
    from src.database import _USE_PG, _db, _x
    try:
        with _db() as conn:
            props = _x(conn, "SELECT COUNT(*) FROM properties").fetchone()[0]
            alerts = _x(conn, "SELECT COUNT(*) FROM alerts_sent").fetchone()[0]
        return jsonify({
            "backend": "postgresql" if _USE_PG else "sqlite",
            "properties": props,
            "alerts_sent": alerts,
            "note": (
                "If alerts_sent is 0 but properties > 0, the DB was restarted and "
                "URL-dedup will prevent duplicate emails."
                if alerts == 0 and props > 0 else ""
            ),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
    """Test OTM scraper against a Hitchin sale search. 10–20 s."""
    if not os.environ.get("APIFY_API_KEY"):
        return jsonify({"error": "APIFY_API_KEY not set"}), 400
    try:
        from src.scrapers.onthemarket import scrape
        from src.models import Search
        search = Search(
            id="test", name="Test", listing_type="sale",
            location="hitchin", min_bedrooms=3, min_price=900000, max_price=1300000,
        )
        props = scrape(search)
        return jsonify({
            "count": len(props),
            "properties": [
                {
                    "title": p.title, "price": p.price, "bedrooms": p.bedrooms,
                    "address": p.address, "url": p.url,
                    "image_url": p.image_url, "agent_name": p.agent_name,
                }
                for p in props[:5]
            ],
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "type": type(exc).__name__}), 500


@app.route("/api/test-fineandcountry")
def api_test_fineandcountry():
    """Probe F&C JSON/API endpoints — site is CraftCMS with client-side rendering. 20–40 s."""
    if not os.environ.get("APIFY_API_KEY"):
        return jsonify({"error": "APIFY_API_KEY not set"}), 400
    try:
        import re as _re
        import json as _json
        from src.scrapers.fineandcountry import _get, _HEADERS
        import requests as _req

        def _get_json(url, extra_headers=None, timeout=12):
            """Try to get a JSON response, return (status, body_text, parsed_or_None)."""
            api_key = os.environ.get("APIFY_API_KEY", "")
            hdrs = dict(_HEADERS)
            hdrs["Accept"] = "application/json, text/javascript, */*; q=0.01"
            hdrs["X-Requested-With"] = "XMLHttpRequest"
            if extra_headers:
                hdrs.update(extra_headers)
            for group in ("groups-RESIDENTIAL", "auto"):
                proxy_url = f"http://{group}:{api_key}@proxy.apify.com:8000"
                try:
                    r = _req.get(url, headers=hdrs,
                                 proxies={"http": proxy_url, "https": proxy_url},
                                 timeout=timeout, verify=False)
                    parsed = None
                    try:
                        parsed = r.json()
                    except Exception:
                        pass
                    return r.status_code, r.text[:600], parsed
                except Exception:
                    pass
            return None, None, None

        results = {}

        # 1. CraftCMS actions API — common patterns
        craft_urls = [
            "https://www.fineandcountry.com/actions/properties/search?location=hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000",
            "https://www.fineandcountry.com/actions/property-search/search?location=hitchin",
            "https://www.fineandcountry.com/actions/search/results?location=hitchin&type=sale",
            "https://www.fineandcountry.com/api/properties?location=hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000",
            "https://www.fineandcountry.com/api/v1/properties?location=hitchin&sale=true",
            "https://www.fineandcountry.com/find-a-property/property-for-sale.json?location=hitchin&minBedrooms=3",
            "https://www.fineandcountry.com/find-a-property/property-for-sale?location=hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000&format=json",
        ]
        for url in craft_urls:
            status, body, parsed = _get_json(url)
            results[url] = {
                "status": status,
                "is_json": parsed is not None,
                "snippet": body[:300] if body else None,
                "top_keys": list(parsed.keys())[:10] if isinstance(parsed, dict) else None,
                "list_len": len(parsed) if isinstance(parsed, list) else None,
            }

        # 2. Scrape the search page HTML and look for XHR URLs in JS
        base_resp = _get("https://www.fineandcountry.com/find-a-property/property-for-sale?location=hitchin&minBedrooms=3&minPrice=900000&maxPrice=1300000", timeout=15)
        xhr_hints = _re.findall(r'["\']((?:https?://[^"\']+|/[^"\']+)(?:api|search|properties|ajax)[^"\']{0,80})["\']', base_resp.text)
        js_api_urls = list(dict.fromkeys(xhr_hints))[:20]

        # 3. Look for initialData / window.__data or similar embedded JSON
        window_data = {}
        for pat in (r'window\.__(?:data|state|props|initialData)\s*=\s*(\{.{0,2000}?\});', r'var\s+initialData\s*=\s*(\{.{0,2000}?\});'):
            wm = _re.search(pat, base_resp.text, _re.DOTALL)
            if wm:
                try:
                    window_data[pat[:40]] = _json.loads(wm.group(1))
                except Exception:
                    window_data[pat[:40]] = wm.group(1)[:300]

        return jsonify({
            "api_probe_results": results,
            "xhr_hints_in_html": js_api_urls,
            "window_data_found": window_data,
            "base_page_status": base_resp.status_code,
        })
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
