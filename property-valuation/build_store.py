#!/usr/bin/env python3
"""Build ~/prop/prop.duckdb from HM Land Registry Price Paid and EPC bulk CSVs.

Usage:
    python3 build_store.py --sales ~/prop/pp-complete.csv
    python3 build_store.py --epc-root ~/prop/domestic-csv
    python3 build_store.py --sales ... --epc-root ...   (both)

Safe to re-run: each table is rebuilt only if its source is supplied.
"""
import argparse
import glob
import os
import sys
import time

import duckdb

DB = os.path.expanduser("~/prop/prop.duckdb")

# HM Land Registry Price Paid "complete" file has NO header row.
# Column order is fixed and documented by HMLR.
SALES_COLS = [
    ("transaction_id", "VARCHAR"),
    ("price", "BIGINT"),
    ("date_of_transfer", "DATE"),
    ("postcode", "VARCHAR"),
    ("property_type", "VARCHAR"),      # D detached, S semi, T terraced, F flat, O other
    ("old_new", "VARCHAR"),            # Y new build, N established
    ("duration", "VARCHAR"),           # F freehold, L leasehold, U unknown
    ("paon", "VARCHAR"),               # house number / name
    ("saon", "VARCHAR"),               # flat / sub-address
    ("street", "VARCHAR"),
    ("locality", "VARCHAR"),
    ("town_city", "VARCHAR"),
    ("district", "VARCHAR"),
    ("county", "VARCHAR"),
    ("ppd_category_type", "VARCHAR"),  # A arm's-length, B repossession/non-arm's-length
    ("record_status", "VARCHAR"),      # A add, C change, D delete
]

# EPC fields we keep. Bulk CSV headers are UPPER_SNAKE; we normalise to lower.
EPC_FIELDS = [
    "lmk_key", "address1", "postcode", "total_floor_area", "inspection_date",
    "property_type", "built_form", "extension_count", "number_habitable_rooms",
    "current_energy_rating", "potential_energy_rating", "construction_age_band",
    "walls_description", "windows_description", "mainheat_description",
]


def build_sales(con, path):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        sys.exit(f"ERROR: sales file not found: {path}")
    size_gb = os.path.getsize(path) / 1e9
    print(f"[sales] reading {path} ({size_gb:.2f} GB, no header row)")
    t0 = time.time()

    names = ", ".join(f"'{n}': '{t}'" for n, t in SALES_COLS)
    con.execute("DROP TABLE IF EXISTS sales")
    con.execute(f"""
        CREATE TABLE sales AS
        SELECT * FROM read_csv(
            '{path}',
            header      = false,
            columns     = {{{names}}},
            dateformat  = '%Y-%m-%d %H:%M',
            ignore_errors = true
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_sales_postcode ON sales(postcode)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sales_street ON sales(street)")
    n = con.execute("SELECT count(*) FROM sales").fetchone()[0]
    print(f"[sales] {n:,} rows in {time.time()-t0:.0f}s; indexed on postcode, street")


def build_epc(con, root):
    """Load every certificates.csv beneath root.

    The EPC bulk download nests as <root>/<year or authority>/<...>/certificates.csv.
    We glob recursively so either layout works.
    """
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        sys.exit(f"ERROR: EPC root not found: {root}")
    # Two layouts exist in the wild: the per-council download nests
    # <authority>/certificates.csv, while the year-split export puts
    # certificates-YYYY.csv side by side in one folder. Match both.
    files = sorted(
        glob.glob(os.path.join(root, "**", "certificates.csv"), recursive=True)
        + glob.glob(os.path.join(root, "**", "certificates-*.csv"), recursive=True)
    )
    if not files:
        sys.exit(f"ERROR: no certificates.csv or certificates-YYYY.csv found beneath {root}")
    total_gb = sum(os.path.getsize(f) for f in files) / 1e9
    print(f"[epc] found {len(files):,} certificates.csv files ({total_gb:.2f} GB)")
    t0 = time.time()

    # union_by_name tolerates schema drift between annual vintages; DuckDB reads
    # the whole glob in one pass, which is far faster than file-by-file inserts.
    sel = ", ".join(f'"{f}"' for f in EPC_FIELDS)
    con.execute("DROP TABLE IF EXISTS epc")
    con.execute(f"""
        CREATE TABLE epc AS
        SELECT {sel}
        FROM read_csv(
            {files!r},
            union_by_name  = true,
            normalize_names = true,
            header         = true,
            all_varchar    = true,
            ignore_errors  = true
        )
    """)
    # Cast the numerics after load so a bad cell kills a value, not the whole file.
    con.execute("""
        ALTER TABLE epc ALTER total_floor_area TYPE DOUBLE
            USING TRY_CAST(total_floor_area AS DOUBLE)
    """)
    con.execute("""
        ALTER TABLE epc ALTER inspection_date TYPE DATE
            USING TRY_CAST(inspection_date AS DATE)
    """)
    for c in ("extension_count", "number_habitable_rooms"):
        con.execute(f"ALTER TABLE epc ALTER {c} TYPE INTEGER USING TRY_CAST({c} AS INTEGER)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_epc_postcode ON epc(postcode)")
    n = con.execute("SELECT count(*) FROM epc").fetchone()[0]
    print(f"[epc] {n:,} certificates in {time.time()-t0:.0f}s; indexed on postcode")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sales", help="path to pp-complete.csv")
    ap.add_argument("--epc-root", help="directory containing certificates.csv files")
    args = ap.parse_args()
    if not args.sales and not args.epc_root:
        ap.error("supply --sales and/or --epc-root")

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = duckdb.connect(DB)
    if args.sales:
        build_sales(con, args.sales)
    if args.epc_root:
        build_epc(con, args.epc_root)
    print(f"\nStore: {DB}")
    for t, in con.execute("SHOW TABLES").fetchall():
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t:8s} {n:>12,} rows")
    con.close()


if __name__ == "__main__":
    main()
