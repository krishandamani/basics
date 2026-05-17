"""Property Hunter — web UI.

Run from the property-hunter/ directory:
    python web/app.py

Then open http://localhost:5000 in your browser.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import (
    DB_PATH, get_unenriched_school_props, get_web_properties,
    has_any_properties, hide_property, init_db, init_web_tables,
    toggle_favourite, update_school_data,
)
from src.scoring import score_property

app = Flask(__name__)
app.secret_key = "property-hunter-local"

# ── School backfill ───────────────────────────────────────────────────────────

_school_backfill_state: dict = {
    "running": False, "done": 0, "total": 0,
    "saved": 0, "last_error": "", "last_api_status": None,
}


def _school_backfill_bg() -> None:
    """Enrich existing properties that have no school data (runs once per startup)."""
    from src.enricher import _enrich_school
    from src.models import Property as _Property
    import requests as _req

    _school_backfill_state["running"] = True
    _school_backfill_state["saved"] = 0
    _school_backfill_state["last_error"] = ""
    try:
        rows = get_unenriched_school_props(limit=60)
        _school_backfill_state["total"] = len(rows)
        print(f"[school-backfill] {len(rows)} properties to enrich")
        if not rows:
            print("[school-backfill] Nothing to do — all properties already have school data")
            return
        for row in rows:
            d = dict(row)
            try:
                prop = _Property(
                    id=d["id"], source=d["source"], listing_type=d["listing_type"],
                    url=d["url"], price=d["price"] or 0, bedrooms=d["bedrooms"] or 0,
                    property_type=d["property_type"] or "", address=d["address"] or "",
                    postcode=d.get("postcode"), lat=d.get("lat"), lng=d.get("lng"),
                )
                enriched = _enrich_school(prop)
                if enriched.nearby_schools:
                    update_school_data(
                        d["id"], enriched.nearby_schools,
                        enriched.nearest_school or "", enriched.school_rating or "",
                    )
                    _school_backfill_state["saved"] += 1
                _school_backfill_state["done"] += 1
            except Exception as exc:
                err = f"{d['id']}: {exc}"
                print(f"  [school-backfill] {err}")
                _school_backfill_state["last_error"] = err
                _school_backfill_state["done"] += 1
        print(f"[school-backfill] Done — saved {_school_backfill_state['saved']}/{_school_backfill_state['done']}")
    except Exception as exc:
        print(f"[school-backfill] Error: {exc}")
        _school_backfill_state["last_error"] = str(exc)
    finally:
        _school_backfill_state["running"] = False


threading.Thread(target=_school_backfill_bg, daemon=True).start()


@app.template_global()
def url_with(**kwargs):
    """Return current page URL with the given query params overridden/removed."""
    from urllib.parse import urlencode
    params = {k: v for k, v in request.args.items()}
    for k, v in kwargs.items():
        if v is None or v == "":
            params.pop(k, None)
        else:
            params[k] = str(v)
    qs = urlencode(params)
    return f"{request.path}?{qs}" if qs else request.path


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

def _extract_town(address: str) -> str:
    """Extract the likely town name from a comma-separated address string."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if len(parts) >= 2:
        return parts[-2]  # e.g. "4 bed house, Hitchin, Hertfordshire" → "Hitchin"
    return parts[0] if parts else ""


def _geocode_towns(towns: list) -> dict:
    """Geocode UK town/place names via postcodes.io places API. Returns {town: (lat, lng)}."""
    if not towns:
        return {}
    try:
        import requests as req
        result: dict = {}
        for town in set(t for t in towns if t):
            try:
                r = req.get(
                    "https://api.postcodes.io/places",
                    params={"q": town, "limit": 1},
                    timeout=6,
                )
                items = (r.json().get("result") or []) if r.status_code == 200 else []
                if items:
                    result[town] = (items[0]["latitude"], items[0]["longitude"])
            except Exception:
                pass
        return result
    except Exception:
        return {}


def _geocode(postcodes: list) -> dict:
    """Bulk-geocode UK postcodes via postcodes.io (free, no key). Batches 100 at a time."""
    if not postcodes:
        return {}
    try:
        import requests as req
        clean = list({p.strip() for p in postcodes if p})  # deduplicate
        result: dict = {}
        for i in range(0, len(clean), 100):
            batch = clean[i:i + 100]
            resp = req.post(
                "https://api.postcodes.io/postcodes",
                json={"postcodes": batch},
                timeout=10,
            )
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

def _council_schools_url(postcode: str) -> str:
    """Return a council catchment-area tool URL for the given UK postcode, or ''."""
    if not postcode:
        return ""
    district = postcode.strip().split()[0].upper()
    num_m = re.search(r'\d+', district)
    n = int(num_m.group()) if num_m else 0
    if district.startswith("HP"):
        if n <= 5:
            return "https://apps.hertfordshire.gov.uk/apps/catchmentareamap/"
        return "https://www.buckinghamshire.gov.uk/schools-and-learning/schools-and-admissions/find-a-school/"
    if district.startswith(("SG", "AL", "WD")):
        return "https://apps.hertfordshire.gov.uk/apps/catchmentareamap/"
    if district.startswith("EN") and n >= 6:
        return "https://apps.hertfordshire.gov.uk/apps/catchmentareamap/"
    if district.startswith("HA"):
        return ("https://www.harrow.gov.uk/schools-learning/school-admissions/2"
                if n <= 3 else "https://www.hillingdon.gov.uk/article/2534/Find-a-school")
    return ""


def _enrich_is_new(props) -> list:
    """Add is_new, days_ago, parsed school lists, and council_schools_url; convert Rows to dicts."""
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

        raw_schools = d.get("nearby_schools")
        if raw_schools:
            try:
                schools_list = json.loads(raw_schools)
                d["nearby_schools_primary"]   = [s for s in schools_list if s.get("phase") == "Primary"]
                d["nearby_schools_secondary"] = [s for s in schools_list if s.get("phase") == "Secondary"]
            except Exception:
                d["nearby_schools_primary"] = []
                d["nearby_schools_secondary"] = []
        else:
            d["nearby_schools_primary"] = []
            d["nearby_schools_secondary"] = []

        d["council_schools_url"] = _council_schools_url(d.get("postcode") or "")

        raw_catchment = d.get("catchment_schools")
        if raw_catchment:
            try:
                d["catchment_schools_list"] = json.loads(raw_catchment)
            except Exception:
                d["catchment_schools_list"] = []
        else:
            d["catchment_schools_list"] = []

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
    outstanding_school = bool(request.args.get("outstanding_school"))
    has_catchment    = bool(request.args.get("has_catchment"))

    nl = _parse_nl(q) if q else {}

    all_props = _enrich_is_new(get_web_properties(
        listing_type      = nl.get("listing_type") or listing_type,
        min_price         = nl.get("min_price")    or _parse_int(min_price_raw),
        max_price         = nl.get("max_price")    or _parse_int(max_price_raw),
        min_bedrooms      = nl.get("min_bedrooms") or _parse_int(min_beds_raw),
        source            = source,
        property_type     = property_type,
        keyword           = keyword,
        outstanding_school = outstanding_school,
        has_catchment     = has_catchment,
        sort              = sort,
    ))

    new_count = sum(1 for p in all_props if p["is_new"])
    displayed  = [p for p in all_props if p["is_new"]] if new_only else all_props

    active_filter_count = sum(bool(x) for x in [
        listing_type, min_price_raw, max_price_raw, min_beds_raw, source, q,
        property_type, keyword, outstanding_school, has_catchment,
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
            "outstanding_school": outstanding_school,
            "has_catchment": has_catchment,
        },
        search_state        = _search_state_for_template(),
        nl_active           = bool(q),
        new_count           = new_count,
        total_count         = len(all_props),
        active_filter_count = active_filter_count,
        base_params         = base_params,
        has_results         = has_any_properties(),
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

    # Bucket properties: stored coords → postcode geocode → town geocode
    with_coords, need_postcode, need_town = [], [], []
    for p in props:
        d = dict(p)
        if d.get("lat") and d.get("lng"):
            with_coords.append(d)
        elif d.get("postcode"):
            need_postcode.append(d)
        else:
            need_town.append(d)

    geo      = _geocode([d["postcode"] for d in need_postcode])
    town_geo = _geocode_towns([_extract_town(d.get("address") or d.get("title", "")) for d in need_town])

    def _make_point(d, lat, lng, jitter=False):
        import random
        if jitter:
            lat += random.uniform(-0.004, 0.004)
            lng += random.uniform(-0.004, 0.004)
        return {
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
        }

    points = [_make_point(d, d["lat"], d["lng"]) for d in with_coords]
    for d in need_postcode:
        if d["postcode"] in geo:
            lat, lng = geo[d["postcode"]]
            points.append(_make_point(d, lat, lng))
    for d in need_town:
        town = _extract_town(d.get("address") or d.get("title", ""))
        if town in town_geo:
            lat, lng = town_geo[town]
            points.append(_make_point(d, lat, lng, jitter=True))

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
        score, reasons = score_property(p)
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

@app.route("/api/re-enrich-schools", methods=["POST"])
def api_re_enrich_schools():
    if _school_backfill_state["running"]:
        return jsonify({"status": "already_running", **_school_backfill_state})
    _school_backfill_state["done"] = 0
    _school_backfill_state["total"] = 0
    threading.Thread(target=_school_backfill_bg, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/school-backfill-status")
def api_school_backfill_status():
    return jsonify(_school_backfill_state)


@app.route("/api/test-gias")
def api_test_gias():
    """Test GIAS school API — returns full raw response for debugging."""
    import requests as _req
    postcode = request.args.get("postcode", "SG50DT")
    url = "https://api.get-information-about-schools.service.gov.uk/api/establishments"
    try:
        r = _req.get(url, params={"nearestToPostCode": postcode, "radiusInMiles": 2},
                     headers={"Accept": "application/json"}, timeout=12)
        ct = r.headers.get("content-type", "")
        try:
            raw = r.json()
        except Exception:
            raw = None
        return jsonify({
            "status_code": r.status_code,
            "content_type": ct,
            "response_type": type(raw).__name__,
            "count": len(raw) if isinstance(raw, list) else (len(raw) if isinstance(raw, dict) else None),
            "keys": list(raw.keys()) if isinstance(raw, dict) else None,
            "first": raw[0] if isinstance(raw, list) and raw else raw,
            "raw_text_preview": r.text[:300] if raw is None else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "url": url, "postcode": postcode}), 500


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
    import subprocess
    from src.scheduler import _apify_configured, _last_run_stats
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                          cwd="/home/user/basics").decode().strip()
    except Exception:
        git_sha = "unknown"
    return jsonify({
        "apify_configured": _apify_configured(),
        "last_run": _last_run_stats,
        "git_sha": git_sha,
        "school_backfill": _school_backfill_state,
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


@app.route("/api/test-catchment")
def api_test_catchment():
    """Test school catchment lookup for a postcode (or lat/lng).

    Usage: /api/test-catchment?postcode=AL10+9AB
           /api/test-catchment?lat=51.75&lng=-0.23
    """
    from src.catchment import lookup_catchment, council_admissions_url, council_name, _postcode_to_council_key, _discover_arcgis_catchment_layer, _COUNCILS
    import requests as _req

    postcode = request.args.get("postcode", "").strip()
    try:
        lat = float(request.args.get("lat", 0))
        lng = float(request.args.get("lng", 0))
    except (ValueError, TypeError):
        lat = lng = 0.0

    if postcode and not (lat and lng):
        try:
            r = _req.get(f"https://api.postcodes.io/postcodes/{postcode.replace(' ', '')}", timeout=6)
            if r.status_code == 200:
                res = r.json().get("result", {})
                lat = res.get("latitude", 0)
                lng = res.get("longitude", 0)
        except Exception:
            pass

    council_key = _postcode_to_council_key(postcode) if postcode else None
    council = _COUNCILS.get(council_key, {}) if council_key else {}

    # Attempt layer discovery if this is an arcgis council
    discovered_layer = None
    if council.get("method") == "arcgis" and council.get("arcgis_base"):
        discovered_layer = _discover_arcgis_catchment_layer(council["arcgis_base"])

    catchment = []
    if lat and lng:
        catchment = lookup_catchment(lat, lng, postcode)

    return jsonify({
        "postcode": postcode,
        "lat": lat,
        "lng": lng,
        "council_key": council_key,
        "council_name": council_name(postcode) if postcode else None,
        "council_method": council.get("method"),
        "arcgis_base": council.get("arcgis_base"),
        "discovered_layer": discovered_layer,
        "admissions_url": council_admissions_url(postcode) if postcode else None,
        "catchment_schools": catchment,
        "count": len(catchment),
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
