"""Property quality scoring — shared between web UI and email alerts."""


def score_property(prop) -> tuple[int, list]:
    """Score a property 0-100 based on enrichment signals.

    Accepts either a Property dataclass or a plain dict (e.g. from DB query).
    Returns (score, reasons) where reasons are short human-readable strings.
    """
    def _get(k):
        if isinstance(prop, dict):
            return prop.get(k)
        return getattr(prop, k, None)

    score = 0
    reasons = []

    school = _get("school_rating") or ""
    if school == "Outstanding":
        score += 30; reasons.append("🎓 Outstanding school")
    elif school == "Good":
        score += 20; reasons.append("🎓 Good school")

    crime = _get("crime_rate") or ""
    if crime == "Low":
        score += 20; reasons.append("🔒 Low crime")
    elif crime == "Medium":
        score += 5
    elif crime == "High":
        score -= 10

    epc = _get("epc_rating") or ""
    if epc in ("A", "B"):
        score += 15; reasons.append(f"⚡ EPC {epc}")
    elif epc == "C":
        score += 10; reasons.append("⚡ EPC C")
    elif epc == "D":
        score += 5

    commute = _get("commute_minutes") or 0
    if 0 < commute <= 30:
        score += 20; reasons.append(f"🚂 {commute}min to London")
    elif commute <= 40:
        score += 15; reasons.append(f"🚂 {commute}min to London")
    elif commute <= 50:
        score += 10; reasons.append(f"🚂 {commute}min to London")

    dist = _get("station_distance_miles") or 0
    if 0 < dist <= 0.5:
        score += 15; reasons.append(f"🚉 {dist}mi to station")
    elif dist <= 1.0:
        score += 10; reasons.append(f"🚉 {dist}mi to station")
    elif dist <= 2.0:
        score += 5

    prev = _get("previous_price")
    price = _get("price")
    if prev and price and price < prev:
        score += 10; reasons.append("💰 Price reduced")

    return max(score, 0), reasons
