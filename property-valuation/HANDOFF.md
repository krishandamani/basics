# Handoff — Cranfield Crescent, Cuffley EN6 4DZ

Written 2026-08-17 from a remote session that could not reach the EPC data.
**Start here in a local session.** Read this, then continue the job.

## Verify you are local first

Run `uname`. You need **Darwin**. If it says Linux you are in another remote
session and the EPC files will not be visible.

## The job

Value the 4-bedroom detached house on Cranfield Crescent, Cuffley **EN6 4DZ**,
asking **£995,000**, listed 7 July 2026 by JR Property Services
(Rightmove listing 90581163 — fetch it). Freehold, council tax G, extended,
three receptions, integral garage, en suite to the principal bedroom, two loft
hatches and no loft conversion. Floorplan 188.9 m² / 2,033 sqft total:
ground 117.0 m², first 71.9 m², garage 3.68 × 5.55 m. House number withheld.

Use the `uk-property-valuation` skill and follow SKILL.md exactly.

## Setup

EPC bulk export is local: `domestic-csv/certificates-2008.csv` …
`certificates-2025.csv` (flat folder, year-split layout).

```bash
mkdir -p ~/prop
curl -L -o ~/prop/pp-complete.csv \
  http://prod.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com/pp-complete.csv
pip install duckdb
python3 build_store.py --sales ~/prop/pp-complete.csv --epc-root ./domestic-csv
./propcomps "EN6 4DZ" "EN6 4DY" "EN6 4EA"
```

Expect ~5.5 GB / 31.4M rows for `sales` (~3 min to load) and both tables
indexed on postcode. On a local machine this store **persists** — build once.

## Already established (HM Land Registry, verified 2026-08-17)

- **Subject is one of 53, 55, 67, 79 EN6 4DZ** — the only odd numbers with no
  sale since 1995. Odd numbers *with* a sale: 41,43,45,47,49,51,57,59,61,63,
  65,69,71,73,75,77,81,83,85,87. No. 79 abuts the detached run 81–87; 53/55/67
  sit between semis. **Adjacency inference only — EPC should settle it.**
- **£757,500 no. 2**, 18 Nov 2022, detached, cat A — highest arm's-length on
  the street. CONFIRMED.
- **£675,000 no. 23**, 26 Jul 2024, detached, cat A — CONFIRMED, and the
  **only arm's-length detached sale on the street in three years**.
- **£680,000 no. 9**, 30 May 2024 — best of the last three years, but it is
  **semi-detached**, so not a detached comparable.
- **£785,000 no. 56**, 2 Dec 2022 — higher than the "street record" but
  **category B**, excluded from comps. A lender's valuer may still see it.
- **£671,000 no. 11**, 18 Jul 2025 — most recent street sale, semi.
- **Wider cohort reframes the question.** Cuffley EN6 4xx, detached, freehold,
  cat A, since 2023: **n=182, median £900,000**, range £360,913–£6,250,000.
  £995,000 is ~10% above the town median for a detached, **not** a 31%
  outlier. The Ridgeway / Carbone Hill / Tolmers Road trade £2.5m–£6.25m.
  Per SKILL.md line 138 the street alone cannot carry the answer.
- **£/sqft at asking:** £489 on the 188.9 m² floorplan total; **£549 net of
  the 20.4 m² integral garage** — the EPC-comparable basis, since EPC measures
  the heated envelope only.
- **Open ambiguity:** ground 117.0 + first 71.9 = 188.9 exactly, and the
  garage is quoted only as dimensions. The integral garage is therefore
  probably *inside* the 117.0, not additional. Confirm on the floorplan; both
  £/sqft figures move if not.

## Data quirks met

- No. 11 flips `property_type` between sales (D in 1997/2007, S in
  2012/2025) — the register reclassified it. Treat street `property_type`
  as soft evidence.
- The remote environment blocks EPC, Rightmove, the Welwyn Hatfield planning
  portal and the LR SPARQL endpoint. Only the Land Registry S3 file is
  reachable there. None of this applies locally.

## Still to do

1. Run `propcomps` for EN6 4DZ / 4DY / 4EA.
2. Identify which of 53/55/67/79 it is; report whether the EPC register holds
   a certificate for it, its area, and how that compares with 188.9 m².
3. Net the garage out before £/sqft; show both bases.
4. Flag any address with two certificates whose areas differ materially, and
   whether `extension_count` rose between them — this street is full of
   extensions, so identify which past sale prices predate the work.
5. Check the Welwyn Hatfield planning register for the street, **refusals
   included**.
6. Report coverage honestly: sales with a verified area, sales with no
   certificate, certificates with no sale, dwellings invisible to both.
7. Build the workbook with `scripts/valuation.py`; give Low/Central/High plus
   an offer ladder.
8. Append the outcome to `CLAUDE.md`.

## The question to answer

Does the size premium genuinely justify a street-record price by that margin,
or will a lender's valuer down-value it? Note the tension already in the
evidence: against Cranfield Crescent, £995,000 looks extreme; against Cuffley
detached stock it is unremarkable. **Floor area decides which frame applies.**
Say so plainly if the evidence does not support an answer.
