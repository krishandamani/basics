# School Catchment Finder — Project Context

## The Problem

When buying a house, school catchment area is often the single most important factor. But finding out whether a specific property is inside a catchment is genuinely hard:

- **It is not on Rightmove, Zoopla, or any portal.** They show a radius circle, not actual catchment boundaries.
- **Companies like Locrating give approximations.** They use historical admissions data (last distance offered) to estimate likelihood, not actual boundaries. The product is explicitly probabilistic and caveated.
- **The only authoritative source is each council's website.** And councils don't have a shared format, API, or data standard.

The real answer to "is this house in catchment for St Mary's Primary?" requires going to the council's admissions page, finding the right year's policy, and either checking a catchment map or looking at the last-accepted distance for that school.

This project builds tooling to automate as much of that as possible and surface it cleanly per property.

---

## Why This Is Hard

### 1. No central dataset

Catchment boundaries are held by ~150 English local authorities. Each publishes them differently:

| Format | Examples |
|--------|---------|
| Interactive map (ArcGIS/Esri) | Hertfordshire, Surrey |
| Downloadable GIS shapefile | Some London boroughs |
| PDF map per school | Many rural councils |
| Lookup tool (address → school list) | Some councils |
| Excel / CSV admission stats only | Some councils |
| Nothing machine-readable at all | A few councils |

### 2. It changes every year

Catchment boundaries are redrawn. Last-distance-offered changes each September as year groups shift. A static database goes stale fast.

### 3. "Catchment" has multiple meanings

- **Defined geographic catchment**: A formal polygon. If your address is inside it, you have priority. The council publishes a map.
- **Priority distance**: No formal catchment, but the school ranks applicants by straight-line distance from home to school gate. The "last distance offered" is the cut-off in a given year.
- **Both**: Many schools have a named catchment zone *and* a distance tie-breaker within it.
- **Neither**: Faith schools, selective schools, and many academies use entirely different criteria.

### 4. Distance measurement varies

Most councils measure straight-line (crow-flies) distance from the property's address point to the school's main entrance. Some use road distance. The reference point for the home is typically the Royal Mail address point, not the property centroid.

---

## Available Data Sources

### Per-council catchment boundary data

| Source | Type | Coverage | Notes |
|--------|------|----------|-------|
| Council planning portals (ArcGIS) | Live GIS | ~40% of councils | Can be queried via ArcGIS REST API in most cases |
| Council websites (PDF maps) | Static | ~30% of councils | Needs OCR / manual extraction |
| Council address lookup tools | Web form | ~20% of councils | Can be scraped with Playwright |
| GIAS (schools data) | JSON API | All schools | School locations/URNs only, not catchment polygons |

### Admissions statistics (last distance offered)

Each school's last-distance-offered is published annually in:
- **School's own admissions policy** (PDF on school/trust website)
- **Council admissions booklet** (PDF, published Oct–Jan each year)
- **School Performance data** (DfE, bulk download)

These give a radius for rough eligibility checks even when no polygon exists.

### School locations

- **GIAS API**: Official, has lat/lng for every school, free, no key required (blocked from Railway/cloud but accessible via Apify proxy).
- **OpenStreetMap**: Covers most schools, no auth needed.

### Council ArcGIS endpoints (highest-value data source)

Many councils host their catchment boundaries on ArcGIS Online or ArcGIS Server. These can be queried programmatically:

```
https://<council-arcgis-host>/arcgis/rest/services/<layer>/FeatureServer/0/query
  ?geometry=<lng,lat>
  &geometryType=esriGeometryPoint
  &spatialRel=esriSpatialRelContains
  &outFields=*
  &f=json
```

This returns which catchment polygon contains a given point — exactly the answer needed.

Known ArcGIS endpoints:
- **Hertfordshire**: `https://mapping.hertfordshire.gov.uk/arcgis/rest/services`
- **Surrey**: `https://www.surreycc.gov.uk` (ArcGIS-based)
- **Kent**: ESRI-hosted
- **Hampshire**: ESRI-hosted
- **Buckinghamshire**: Grammar school selection — different system

---

## Proposed Architecture

### Phase 1 — Council registry

Build a YAML/JSON registry mapping each local authority to its data source type and endpoint:

```yaml
hertfordshire:
  name: Hertfordshire County Council
  arcgis_url: https://mapping.hertfordshire.gov.uk/arcgis/rest/services/
  arcgis_layer: SchoolCatchments/FeatureServer/0
  admissions_url: https://www.hertfordshire.gov.uk/services/schools-and-education/school-admissions/
  last_distance_url: https://www.hertfordshire.gov.uk/...
  method: arcgis  # arcgis | scrape | manual
```

### Phase 2 — Point-in-polygon lookup

Given a property lat/lng:

1. Determine which local authority it falls in (use ONS boundary data or postcodes.io — already returns `admin_district`)
2. Look up the council in the registry
3. If `method: arcgis` → query the ArcGIS endpoint with the point
4. If `method: scrape` → Playwright scrape the council's address lookup tool
5. Return list of schools the property is in catchment for

### Phase 3 — Last distance enrichment

For each school returned, fetch:
- Last distance offered (from council booklet or school admissions PDF)
- Actual straight-line distance from property to school
- Ratio: `distance / last_distance` → gives a probability-like "how safe is this address"

### Phase 4 — Integration with property-hunter

Surface the data as a new enrichment in the existing property-hunter web UI:
- Per property card: "In catchment for: St Mary's CE Primary (0.3mi, last year cut-off 0.6mi ✓)"
- Filter: "In catchment for Outstanding primary"
- Link to council's official admissions page

---

## Key Challenges and Mitigations

| Challenge | Mitigation |
|-----------|-----------|
| Councils block scrapers | Playwright + Apify proxy (already used for Rightmove) |
| PDFs for catchment maps | PDF → image → GPT-4o vision OCR to extract school name / boundary description |
| ArcGIS layers named differently per council | Manual inspection + registry; one-time setup per council |
| Data goes stale annually | Scheduled refresh (GitHub Actions, monthly) |
| Faith/selective schools ignore catchment | Flag these in the registry; show admissions criteria instead |
| Buckinghamshire / grammar counties | 11-plus registration deadlines and grammar zones, not geographic catchment; needs separate handling |
| No ArcGIS, no scrape tool → PDF only | Accept this for now; show link to council page + last distance offered |

---

## Councils to Prioritise (property-hunter target areas)

Based on the commute table in property-hunter (Herts, Bucks, EN postcodes, WD postcodes):

| Council | Priority | Method |
|---------|----------|--------|
| Hertfordshire | High | ArcGIS (mapping.hertfordshire.gov.uk) |
| Buckinghamshire | High | Grammar county — needs separate handling |
| London Borough of Barnet | High | ArcGIS or lookup tool |
| London Borough of Enfield | High | TBD |
| London Borough of Harrow | Medium | TBD |
| London Borough of Hillingdon | Medium | TBD |
| Three Rivers / Watford (Herts) | High | Same as Hertfordshire |

---

## First Steps

1. **Investigate Hertfordshire ArcGIS** — confirm the catchment layer endpoint, query format, and field names. This is the highest-value council for property-hunter users.

2. **Build the council registry** (`data/councils.yml`) — start with the 7 councils above.

3. **Write the core lookup function**:
   ```python
   def catchment_schools(lat: float, lng: float) -> list[dict]:
       """Return schools this point is in catchment for, with last-distance data."""
   ```

4. **Test on known addresses** — use postcodes known to be inside/outside specific catchments to validate the ArcGIS queries.

5. **Integrate into property-hunter enricher** as `_enrich_catchment(prop)` alongside the existing school enrichment.

---

## What Success Looks Like

A user pastes a Rightmove URL. The app shows:

> **Schools**
> - ✅ In catchment: Brookmans Park Primary (Outstanding, 0.4mi away, last year cut-off 1.2mi — very safe)
> - ❌ Not in catchment: Welham Green Primary (Good, 0.6mi — you're outside the 0.5mi cut-off)
> - [Check Hertfordshire admissions →](https://www.hertfordshire.gov.uk/...)

This is information that today requires 20 minutes of manual research per property. It is the most common reason buyers lose time on properties that look good on paper.
