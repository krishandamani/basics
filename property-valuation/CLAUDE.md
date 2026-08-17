# Property assessment log

Running history of properties assessed, offers made, outcomes, and data quirks.
**Append a new entry after every valuation** so future sessions inherit the context.

Read alongside the `uk-property-valuation` skill, which holds the method. This
file holds only what actually happened.

---

## How the data store is built

| Table | Source | Status |
|---|---|---|
| `sales` | HM Land Registry Price Paid `pp-complete.csv` (~5.5 GB, no header row, 16 fixed columns) | Downloadable in-session |
| `epc` | EPC bulk `all-domestic-certificates` / `domestic-csv`, per-council `certificates.csv` | **Must be supplied by the user** — see quirks |

Rebuild with:

```bash
python3 ~/prop/build_store.py --sales ~/prop/pp-complete.csv --epc-root ~/prop/domestic-csv
~/prop/propcomps "EN6 4DZ" "EN6 4DY" "EN6 4EA"
```

`propcomps` writes two CSVs to the current folder: every EPC certificate for
those postcodes (deliberately **not** deduplicated — duplicate certificates
reveal extensions and assessor disagreement) and **category-A sales only**
(category B is repossessions and non-arm's-length transfers).

---

## Environment quirks — read this before planning a session

These cost real time on 2026-08-17 and will recur:

1. **Sessions run on a throwaway cloud container, not the user's own machine.**
   `/documents/Property` and the local `domestic-csv` EPC folders are on the
   user's laptop and are *not* visible here. Anything built at `~/prop/` is
   destroyed when the container is reclaimed. Only what is committed and pushed
   to the `krishandamani/basics` repo survives — and the ~12 GB of data cannot
   go in git, so **the store must be rebuilt every session**.

2. **The network policy blocks four of the five data sources.** Verified by
   direct test:

   | Source | Result |
   |---|---|
   | HM Land Registry `pp-complete.csv` (S3) | reachable |
   | EPC bulk download / EPC API | blocked, 403 at proxy |
   | Rightmove | blocked, 403 at proxy |
   | Welwyn Hatfield planning register | blocked, 403 at proxy |
   | Land Registry SPARQL qonsole | blocked, 403 at proxy |

   So EPC rows, listing text and planning history must all be **pasted in by the
   user**. The skill's own SKILL.md (lines 30, 36-38) already assumes this.

3. `property-alert/property_alert/enrichers/epc.py` in the repo calls the EPC
   API, but that host is blocked and no `EPC_API_KEY` is configured. It is not a
   workaround.

---

## Properties assessed

### 1. Cranfield Crescent, Cuffley, EN6 4DZ — asking £995,000

- **Status:** in progress, 2026-08-17.
- **Listing:** Rightmove 90581163, listed 7 July 2026 by JR Property Services.
  House number withheld. Could not be fetched (blocked) — details below are as
  supplied by the user, not independently verified.
- **Stated facts:** 4-bed detached, freehold, council tax G, extended, three
  receptions, integral garage, en suite to principal bedroom, two loft hatches
  and no loft conversion.
- **Floorplan:** 188.9 m² / 2,033 sqft total — ground 117.0 m², first 71.9 m²,
  garage 3.68 × 5.55 m (20.4 m²). Garage must be netted out before £/sqft,
  because every EPC comparable measures the heated envelope only.
- **User's price context (unverified pending data):** street record £757,500
  (no. 2, Nov 2022); best of last three years £680,000 (no. 9); 4-bed detached
  no. 23 sold £675,000 July 2024. Asking is ~31% above the street record.
- **Central question:** does the size premium justify a street-record price by
  that margin, or will a lender's valuer down-value?
**Findings from Land Registry alone (EPC still missing):**

- **Subject narrowed to 4 candidates.** Odd numbers in EN6 4DZ with a recorded
  sale: 41,43,45,47,49,51,57,59,61,63,65,69,71,73,75,77,81,83,85,87.
  **No sale since 1995: 53, 55, 67, 79.** No. 79 abuts the detached run 81-87;
  53/55/67 sit between semis. That is adjacency inference, not evidence — EPC
  will settle it.
- **£757,500 no. 2 (18 Nov 2022, detached, cat A)** — confirmed, highest
  arm's-length on the street.
- **£675,000 no. 23 (26 Jul 2024, detached, cat A)** — confirmed.
- **£680,000 no. 9 (30 May 2024)** — confirmed as best of last 3 years but it is
  **semi-detached**, not a detached comparable.
- **£785,000 no. 56 (2 Dec 2022)** — higher than the "street record", but
  **category B**, so excluded from comps. A lender's valuer may still see it.
- **£671,000 no. 11 (18 Jul 2025)** — most recent street sale, semi.
- **Only ONE arm's-length detached sale on the street in 3 years** (no. 23).
  The street cannot on its own support or refute a street-record price.
- **Wider cohort reframes the question:** Cuffley EN6 4xx detached, freehold,
  cat A, since 2023: n=182, **median £900,000**, range £360,913-£6,250,000.
  £995,000 is ~10% above the town median for a detached, not a 31% outlier.
  The Ridgeway / Carbone Hill / Tolmers Road trade £2.5m-£6.25m.
- **£/sqft at asking:** £489/sqft on the 188.9 m² floorplan total; **£549/sqft**
  net of the 20.4 m² integral garage (the EPC-comparable basis). Ambiguity to
  resolve: ground 117.0 + first 71.9 = 188.9 exactly, so the integral garage is
  probably *inside* the 117.0 rather than additional to it — confirm on the plan.

- **Outcome:** _pending EPC_
- **Quirks found:** no. 11 changes property_type between sales (D in 1997/2007,
  S in 2012/2025) — the register reclassified it; treat street property_type
  as soft. User's EPC export is `certificates-YYYY.csv` in one flat folder, not
  the per-council `certificates.csv` layout.

<!-- Template for future entries:

### N. <Street>, <Town>, <Postcode> — asking £X

- **Status:**
- **Listing:**
- **Area:** EPC __ m² (cert dated __) vs floorplan __ m². Basis: EPC heated
  envelope, garage excluded.
- **Comparable cohort:** n = __, median £__/sqft, bootstrap 95% CI __
- **Coverage:** sale+area __, sale-no-EPC __, EPC-no-sale __, invisible __
- **Planning:** grants/refusals
- **Valuation:** Low / Central / High
- **Offer made:** ; **Outcome:**
- **Quirks found:**
-->
