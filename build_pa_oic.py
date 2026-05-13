"""
PA OIC Dashboard — Monthly Update Script
==========================================
Reads the latest OIC CSV from OneDrive, filters to 30+ day PA claims,
consolidates locations, builds JSON, embeds into dashboard.html,
and updates months.json.

Usage:
    python3 build_pa_oic.py                    # auto-detect latest CSV
    python3 build_pa_oic.py path/to/file.csv   # specify CSV explicitly

Output:
    - data/YYYY-MM.json          (monthly claims JSON)
    - data/months.json           (updated month index)
    - dashboard.html             (re-embedded with all months' data)
"""

import pandas as pd
import json
import re
import os
import sys
import glob
from datetime import datetime

# ── Paths ────────────────────────────────────────────────────────────────────
ONEDRIVE_OIC = os.path.expanduser(
    "~/Library/CloudStorage/OneDrive-ChildSmilesGroup,LLC(2)/ABRA RCM - PA/AR/OIC/"
)
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(DASHBOARD_DIR, "data")

# ── PA Location Consolidation ────────────────────────────────────────────────
LOCATION_MAP = {
    "Allentown":        "Allentown",
    "Allentown OR":     "Allentown",
    "Allentown OS":     "Allentown",
    "Scranton":         "Scranton",
    "Scranton West":    "Scranton",
    "Scranton OR":      "Scranton",
    "Wilkes-Barre":     "Wilkes-Barre",
    "Wilkes-Barre East":"Wilkes-Barre",
    "Wilkes-Barre OR":  "Wilkes-Barre",
    "Hazleton":         "Hazleton",
    "Hazleton OR":      "Hazleton",
    "Bartonsville":     "Bartonsville",
    "Bartonsville OR":  "Bartonsville",
    "Reading":          "Reading",
    "Reading OR":       "Reading",
}
VALID_CLINICS = set(LOCATION_MAP.keys())

# ── Helpers ──────────────────────────────────────────────────────────────────

def find_latest_csv():
    """Find the most recent OIC CSV in the OneDrive folder."""
    patterns = [
        os.path.join(ONEDRIVE_OIC, "CL - OIC Claim Level With Provider PA*.csv"),
        os.path.join(ONEDRIVE_OIC, "OIC PA*.csv"),
    ]
    all_files = []
    for p in patterns:
        all_files.extend(glob.glob(p))
    if not all_files:
        print(f"ERROR: No OIC CSV files found in {ONEDRIVE_OIC}")
        sys.exit(1)
    latest = max(all_files, key=os.path.getmtime)
    return latest


def parse_dollar(val):
    """Parse dollar strings: strip $, commas, treat () as negative."""
    if pd.isna(val):
        return 0.0
    s = str(val).strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def format_dos(val):
    """Format date-of-service, return empty string for bad/epoch dates."""
    if pd.isna(val) or str(val).strip() == "":
        return ""
    s = str(val).strip()
    # Strip time component if present (e.g., "01/01/0001 12:00:00 AM")
    s = s.split(" ")[0] if " " in s else s
    if s in ("01/01/0001", "1/1/0001", ""):
        return ""
    return s


def map_claim_level(val):
    """Map ClaimLevel to display string."""
    if pd.isna(val):
        return ""
    s = str(val).strip().lower()
    if s in ("p", "primary"):
        return "Primary"
    elif s in ("s", "secondary"):
        return "Secondary"
    elif s in ("o", "other"):
        return "Other"
    return ""


def extract_date_from_filename(filepath):
    """Extract date from filename like 'OIC PA 4.24.2026.csv' or 'Provider PA as of 04.29.2026.csv'."""
    basename = os.path.basename(filepath)
    # Look for M.DD.YYYY or MM.DD.YYYY pattern
    match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', basename)
    if match:
        m, d, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return datetime(y, m, d)
    return datetime.now()


def process_csv(csv_path):
    """Read and process the OIC CSV into dashboard-ready records."""
    print(f"Reading: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"  Raw rows: {len(df):,}")

    # Filter to valid PA clinics only (ClaimClinic or PatientAssignedClinic)
    clinic_col = "ClaimClinic" if "ClaimClinic" in df.columns else "PatientAssignedClinic"
    df = df[df[clinic_col].isin(VALID_CLINICS)].copy()
    print(f"  After clinic filter: {len(df):,}")

    # Filter to 30+ days outstanding
    df["DaysOutstanding"] = pd.to_numeric(df["DaysOutstanding"], errors="coerce").fillna(0).astype(int)
    df = df[df["DaysOutstanding"] >= 30].copy()
    print(f"  After 30+ day filter: {len(df):,}")

    # Consolidate locations
    df["clinic"] = df[clinic_col].map(LOCATION_MAP)

    # Build output records; append counter only for 2nd+ duplicate ClaimNums
    records = []
    id_counter = {}
    for _, row in df.iterrows():
        base_id = str(row.get("ClaimNum", ""))
        id_counter[base_id] = id_counter.get(base_id, 0) + 1
        claim_id = base_id if id_counter[base_id] == 1 else f"{base_id}_{id_counter[base_id]}"
        records.append({
            "id": claim_id,
            "patNum": str(row.get("PatNum", "")),
            "clinic": row["clinic"],
            "carrier": str(row.get("CarrierName", "")).strip(),
            "provider": str(row.get("RenderingProvider", "")).strip(),
            "claimLevel": map_claim_level(row.get("ClaimLevel")),
            "dos": format_dos(row.get("LastDOS")),
            "billedFee": parse_dollar(row.get("TotalBilledFee")),
            "insPayEst": parse_dollar(row.get("TotalInsPayEst")),
            "daysOut": int(row["DaysOutstanding"]),
            "agingBucket": str(row.get("AgingBucket", "")).strip(),
            "trackDate": format_dos(row.get("LastTrackingDate")),
            "trackNote": str(row.get("LastTrackingNote", "")).strip().replace("\\", "") if pd.notna(row.get("LastTrackingNote")) else "",
        })

    print(f"  Final claims: {len(records):,}")
    return records


def save_month_json(records, month_key):
    """Save records to data/YYYY-MM.json."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{month_key}.json")
    with open(path, "w") as f:
        json.dump(records, f, separators=(",", ":"))
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  Saved: {path} ({size_mb:.1f} MB)")
    return path


def update_months_json(month_key, label, date_str):
    """Add or update month entry in months.json (newest first)."""
    months_path = os.path.join(DATA_DIR, "months.json")
    if os.path.exists(months_path):
        with open(months_path) as f:
            months = json.load(f)
    else:
        months = []

    # Remove existing entry for this month if present
    months = [m for m in months if m["key"] != month_key]

    # Add new entry
    months.insert(0, {
        "key": month_key,
        "label": label,
        "date": date_str,
        "file": f"data/{month_key}.json"
    })

    # Sort newest first
    months.sort(key=lambda m: m["key"], reverse=True)

    with open(months_path, "w") as f:
        json.dump(months, f, indent=2)
    print(f"  Updated: {months_path} ({len(months)} months)")
    return months


def embed_into_html(target_filename, months):
    """Re-embed all month data into the given HTML file (dashboard.html or performance.html)."""
    target_path = os.path.join(DASHBOARD_DIR, target_filename)
    if not os.path.exists(target_path):
        print(f"  [WARN] {target_path} not found — skipping")
        return
    with open(target_path, "r") as f:
        html = f.read()

    # Load all month JSONs into embedded data dict
    embedded = {}
    for m in months:
        json_path = os.path.join(DASHBOARD_DIR, m["file"])
        if os.path.exists(json_path):
            with open(json_path) as f:
                embedded[m["key"]] = json.load(f)

    # Build MONTHS_DATA line
    months_meta = [{"key": m["key"], "label": m["label"], "date": m["date"]} for m in months]
    months_line = f"const MONTHS_DATA = {json.dumps(months_meta)};"

    # Build EMBEDDED_DATA line
    embedded_line = f"const EMBEDDED_DATA = {json.dumps(embedded, separators=(',', ':'))};"

    # Replace existing lines in HTML (use lambda to avoid backslash interpretation in replacement)
    html = re.sub(r'const MONTHS_DATA = .*?;', lambda m: months_line, html)
    html = re.sub(r'const EMBEDDED_DATA = .*?;', lambda m: embedded_line, html)

    with open(target_path, "w") as f:
        f.write(html)
    size_mb = os.path.getsize(target_path) / (1024 * 1024)
    print(f"  Embedded into: {target_path} ({size_mb:.1f} MB)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Determine CSV path
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        csv_path = find_latest_csv()

    # Extract date for month key
    file_date = extract_date_from_filename(csv_path)
    month_key = file_date.strftime("%Y-%m")
    month_label = file_date.strftime("%B %Y")
    date_str = file_date.strftime("%m/%d/%Y")

    print(f"\n{'='*60}")
    print(f"PA OIC Dashboard Update — {month_label}")
    print(f"{'='*60}")
    print(f"  CSV: {os.path.basename(csv_path)}")
    print(f"  Month Key: {month_key}")
    print()

    # Process
    records = process_csv(csv_path)

    # Save JSON
    print("\nSaving month JSON...")
    save_month_json(records, month_key)

    # Update months index
    print("\nUpdating months.json...")
    months = update_months_json(month_key, month_label, date_str)

    # Embed into both dashboard.html and performance.html so they stay in sync
    print("\nEmbedding data into dashboard.html and performance.html...")
    embed_into_html("dashboard.html", months)
    embed_into_html("performance.html", months)

    print(f"\n{'='*60}")
    print(f"DONE! {len(records):,} claims for {month_label}")
    print(f"{'='*60}")
    print(f"\nNext steps:")
    print(f"  cd '{DASHBOARD_DIR}'")
    print(f"  git add -A && git commit -m 'Update {month_label} OIC data' && git push")


if __name__ == "__main__":
    main()
