# Property valuation data store

Scripts to build a local DuckDB store of UK property evidence and pull
comparables for a given set of postcodes.

## Why this lives in git

The analysis needs ~14 GB of source data (HM Land Registry Price Paid plus the
EPC bulk export). That data cannot live in the repo. These scripts can, so the
store is reproducible on any machine in about ten minutes.

## Where to run it

**Prefer your own machine.** Claude Code on the web runs in a throwaway cloud
container that (a) cannot see your local `domestic-csv` folder and (b) is
deleted after a period of inactivity, so the store must be rebuilt every
session. Run locally and it persists, the EPC files are already there, and the
blocked domains (EPC, Rightmove, council planning portals) are reachable.

```bash
npm install -g @anthropic-ai/claude-code
cd ~/Documents/Property && claude
```

## Setup

```bash
pip install duckdb
```

### 1. Land Registry Price Paid (~5.5 GB, no registration)

```bash
curl -L -o ~/prop/pp-complete.csv \
  http://prod.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com/pp-complete.csv
```

No header row; 16 fixed columns, supplied explicitly by `build_store.py`.

### 2. EPC bulk export (~8 GB, free registration)

Register at `epc.opendatacommunities.org` and download all domestic
certificates. Two layouts exist in the wild and both are handled:

- per-council: `<authority>/certificates.csv`
- year-split:  `certificates-2008.csv … certificates-2025.csv` in one folder

### 3. Build

```bash
python3 build_store.py --sales ~/prop/pp-complete.csv --epc-root ~/prop/domestic-csv
```

Creates `~/prop/prop.duckdb` with `sales` and `epc`, indexed on postcode.
Either flag may be given alone to rebuild just that table.

## Usage

```bash
./propcomps "EN6 4DZ" "EN6 4DY" "EN6 4EA"
```

Writes two CSVs to the current directory:

- `epc_*.csv` — **every** certificate for those postcodes, deliberately *not*
  deduplicated to the latest. Duplicate certificates are the point: they reveal
  extensions (via `extension_count` rising) and assessor disagreement. Adds
  `certs_for_address` and `cert_seq` so pairs are easy to spot.
- `sales_*.csv` — **category-A sales only**. Category B is repossessions and
  non-arm's-length transfers and must never be used as a comparable.

## Method

See the `uk-property-valuation` skill. The rules that matter most: EPC is the
source of truth for floor area; use the certificate current *at the date of
sale*, not the latest; never mix EPC (heated envelope) with GIA (includes
garage and unheated space); test the size gradient and the EPC-condition proxy
rather than assuming them; report a range, not a point estimate.

`CLAUDE.md` is the running log of properties assessed and data quirks met.
