"""School catchment area lookup.

Determines which school catchments a property falls inside by querying
the relevant council's data source (currently ArcGIS point-in-polygon).

Entry point: lookup_catchment(lat, lng, postcode) -> list[dict]
Each result: {name, phase, urn, in_catchment, council, source}
"""

import os
import re
from pathlib import Path
from typing import Optional
import requests
import yaml

_TIMEOUT = 10
_DATA_DIR = Path(__file__).parent / "data"
_COUNCILS_FILE = _DATA_DIR / "councils.yml"

# Cache: arcgis base_url -> discovered layer URL (None = not found)
_arcgis_layer_cache: dict = {}


def _load_councils() -> dict:
    try:
        data = yaml.safe_load(_COUNCILS_FILE.read_text())
        return data.get("councils", {}) if data else {}
    except Exception:
        return {}


_COUNCILS: dict = _load_councils()


def _postcode_to_council_key(postcode: str) -> Optional[str]:
    """Map a UK postcode to a council key from the registry."""
    if not postcode:
        return None
    clean = re.sub(r"\s+", "", postcode).upper()
    # UK inward code is always last 3 chars (digit + 2 letters); strip it to get outward
    outward = clean[:-3] if len(clean) >= 5 else clean
    m = re.match(r'^([A-Z]{1,2})(\d{1,2})', outward)
    if not m:
        return None
    letters, num_s = m.group(1), m.group(2)
    n = int(num_s)

    if letters == "HP":
        return "hertfordshire" if n <= 5 else "buckinghamshire"
    if letters in ("SG", "AL", "WD"):
        return "hertfordshire"
    if letters == "EN":
        if n >= 6:
            return "hertfordshire"
        if n in (4, 5):
            return "barnet"
        return "enfield"
    if letters == "HA":
        return "harrow" if n <= 3 else "hillingdon"
    if letters == "N" and n in (11, 12, 14, 20):
        return "barnet"
    if letters == "NW" and n in (4, 7, 9):
        return "barnet"
    return None


def _apify_proxies() -> Optional[dict]:
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        return None
    proxy_url = f"http://groups-RESIDENTIAL:{api_key}@proxy.apify.com:8000"
    return {"http": proxy_url, "https": proxy_url}


def _get_json(url: str, params: dict = None, use_proxy: bool = False) -> Optional[dict]:
    """GET JSON from url, optionally via Apify proxy. Returns None on any failure."""
    proxies = _apify_proxies() if use_proxy else None
    try:
        r = requests.get(url, params=params or {}, timeout=_TIMEOUT, proxies=proxies)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _discover_arcgis_catchment_layer(base_url: str) -> Optional[str]:
    """Walk an ArcGIS services directory to find a school catchment FeatureServer layer.

    Scores candidate services by how catchment-like their names are, then
    probes the top candidate to confirm it has valid fields. Caches the result.
    """
    cache_key = base_url.rstrip("/")
    if cache_key in _arcgis_layer_cache:
        return _arcgis_layer_cache[cache_key]

    result = None
    try:
        root = _get_json(base_url, {"f": "json"})
        if not root:
            _arcgis_layer_cache[cache_key] = None
            return None

        candidate_names: list = []

        def _collect(data: dict) -> None:
            for svc in data.get("services", []):
                if svc.get("type") == "FeatureServer":
                    candidate_names.append(svc["name"])
            for folder in data.get("folders", []):
                fd = _get_json(f"{base_url.rstrip('/')}/{folder}", {"f": "json"})
                if fd:
                    _collect(fd)

        _collect(root)

        SCORE = [("catchment", 10), ("admission", 8), ("school", 5), ("education", 3)]
        scored = sorted(
            ((sum(pts for kw, pts in SCORE if kw in n.lower()), n) for n in candidate_names),
            reverse=True,
        )

        for score, name in scored:
            if score == 0:
                break
            probe_url = f"{base_url.rstrip('/')}/{name}/FeatureServer/0"
            probe = _get_json(probe_url, {"f": "json"})
            if probe and "fields" in probe:
                result = probe_url
                break

    except Exception:
        pass

    _arcgis_layer_cache[cache_key] = result
    return result


def _pick_attr(attrs: dict, *keys: str) -> str:
    """Return the first non-empty value from attrs matching any of keys."""
    for k in keys:
        v = attrs.get(k)
        if v is not None:
            s = str(v).strip()
            if s and s.lower() not in ("null", "none", "0", ""):
                return s
    return ""


def _parse_phase(raw: str) -> str:
    r = raw.lower()
    if any(w in r for w in ("primary", "infant", "junior", "prep", "first", "lower")):
        return "Primary"
    if any(w in r for w in ("secondary", "high", "upper", "middle", "through", "grammar")):
        return "Secondary"
    return "Primary"


def _query_arcgis(layer_url: str, lat: float, lng: float) -> list:
    """Point-in-polygon query: returns school dicts for catchments containing lat/lng."""
    data = _get_json(layer_url + "/query", {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    })
    if not data or data.get("error"):
        return []

    schools = []
    for feat in data.get("features", []):
        attrs = feat.get("attributes", {})
        name = _pick_attr(attrs,
            "SchoolName", "SCHOOLNAME", "school_name",
            "EstablishmentName", "ESTABLISHMENTNAME",
            "Name", "NAME", "SCHOOL", "School",
        )
        if not name:
            continue
        phase_raw = _pick_attr(attrs,
            "PhaseOfEducation", "PHASE", "Phase", "SchoolType", "SCHOOLTYPE",
            "phase", "Type", "TYPE", "school_type",
        )
        urn = _pick_attr(attrs,
            "URN", "urn", "Urn", "DfE", "DFE", "RefNumber", "SchoolDfENumber",
        )
        schools.append({"name": name, "phase": _parse_phase(phase_raw), "urn": urn})

    return schools


def lookup_catchment(lat: float, lng: float, postcode: str = "") -> list:
    """Return schools this property is confirmed in-catchment for.

    Each entry: {name, phase, urn, in_catchment: True, council, source}.
    Returns [] if no council match, method != arcgis, or the query fails.
    """
    council_key = _postcode_to_council_key(postcode)
    if not council_key:
        return []

    council = _COUNCILS.get(council_key)
    if not council or council.get("method") != "arcgis":
        return []

    base_url = council.get("arcgis_base", "")
    if not base_url:
        return []

    known = council.get("arcgis_primary_layer", "")
    if known:
        layer_url = f"{base_url.rstrip('/')}/{known}"
    else:
        layer_url = _discover_arcgis_catchment_layer(base_url)

    if not layer_url:
        return []

    schools = _query_arcgis(layer_url, lat, lng)
    for s in schools:
        s["in_catchment"] = True
        s["council"] = council_key
        s["source"] = "arcgis"

    return schools


def council_admissions_url(postcode: str) -> str:
    """Return the most useful council admissions/catchment URL for a postcode, or ''."""
    key = _postcode_to_council_key(postcode)
    if not key:
        return ""
    c = _COUNCILS.get(key, {})
    return c.get("catchment_map_url") or c.get("admissions_url") or ""


def council_name(postcode: str) -> str:
    """Return the council name for a postcode, or ''."""
    key = _postcode_to_council_key(postcode)
    if not key:
        return ""
    return _COUNCILS.get(key, {}).get("name", "")
