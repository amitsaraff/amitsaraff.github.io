#!/usr/bin/env python3
"""
Fetch the last 4 weeks of steps from the open Garmin Connect tab via CDP,
then update steps.csv and 10000/index.html.

Usage:
    python3 update_steps.py [--dry-run]

Requirements:
    pip install cdp-cli-python  # or use the `cdp` CLI via subprocess
    The cdp daemon must be running: cdp daemon start --auto-connect
    A Garmin Connect tab must be open at:
        https://connect.garmin.com/app/report/29/wellness/last_four_weeks
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent
HTML_FILE = REPO_ROOT / "10000" / "index.html"
STEPS_CSV = REPO_ROOT / "steps.csv"
GARMIN_URL = "https://connect.garmin.com/app/report/29/wellness/last_four_weeks"
EXPORT_BTN_SEL = ".Report_exportBtn__6MES-"


# ── CDP helpers ────────────────────────────────────────────────────────────────

def cdp(*args, timeout="20s") -> dict:
    """Run a cdp CLI command and return parsed JSON output."""
    cmd = ["cdp", *args, "--json"]
    if timeout:
        cmd += ["--timeout", timeout]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"cdp error: {result.stderr or result.stdout}", file=sys.stderr)
        sys.exit(1)


def find_garmin_target() -> str:
    """Return the CDP target ID of the Garmin Connect steps tab."""
    data = cdp("pages")
    for page in data.get("pages", []):
        if GARMIN_URL in page.get("url", ""):
            return page["id"]
    sys.exit(
        f"No Garmin Connect tab found at {GARMIN_URL}.\n"
        "Open that URL in Chrome, then re-run this script."
    )


def eval_js(target_id: str, js: str) -> str:
    """Evaluate JS in the target tab and return the string result."""
    data = cdp("eval", "--target", target_id, js)
    return data["result"]["value"]


def click_export(target_id: str) -> None:
    """Click the Export button on the Garmin report page."""
    cdp("click", EXPORT_BTN_SEL, "--target", target_id)


# ── CSV parsing ────────────────────────────────────────────────────────────────

def parse_garmin_csv(text: str) -> dict[str, int]:
    """
    Parse a Garmin-exported CSV (MM/DD/YYYY, Actual, Goal) into
    {YYYY-MM-DD: steps} with the BOM stripped.
    """
    rows = {}
    reader = csv.reader(text.lstrip("﻿").splitlines())
    header = next(reader, None)  # skip header
    if header is None:
        return rows
    for row in reader:
        if len(row) < 2:
            continue
        date_str, steps_str = row[0].strip(), row[1].strip()
        try:
            dt = datetime.strptime(date_str, "%m/%d/%Y")
            rows[dt.strftime("%Y-%m-%d")] = int(steps_str.replace(",", ""))
        except (ValueError, IndexError):
            continue
    return rows


# ── Download via Export button → find newest CSV in ~/Downloads ────────────────

def latest_download(before: datetime) -> Path | None:
    """Return the most recently modified Steps*.csv in ~/Downloads after `before`."""
    downloads = Path.home() / "Downloads"
    candidates = sorted(
        [f for f in downloads.glob("Steps*.csv") if datetime.fromtimestamp(f.stat().st_mtime) > before],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def fetch_via_export(target_id: str) -> dict[str, int]:
    """Click Export, wait for the download to land, then parse it."""
    import time
    before = datetime.now()
    click_export(target_id)
    # Poll for up to 15 seconds
    for _ in range(15):
        time.sleep(1)
        path = latest_download(before)
        if path:
            print(f"Downloaded: {path}")
            return parse_garmin_csv(path.read_text(encoding="utf-8-sig"))
    sys.exit("Timed out waiting for the CSV download.")


# ── Alternative: read data directly from the DOM via JS ───────────────────────

def fetch_via_dom(target_id: str) -> dict[str, int]:
    """
    Extract step data rendered in the Garmin report DOM (table rows or SVG
    tooltips).  Falls back gracefully if the DOM structure has changed.
    """
    js = """
    (function() {
      var rows = Array.from(document.querySelectorAll('table tr'));
      var out = {};
      var YEAR = new Date().getFullYear();
      var MONTHS = {Jan:1,Feb:2,Mar:3,Apr:4,May:5,Jun:6,Jul:7,Aug:8,Sep:9,Oct:10,Nov:11,Dec:12};
      rows.forEach(function(tr) {
        var cells = Array.from(tr.querySelectorAll('td')).map(function(td){ return td.textContent.trim(); });
        if (cells.length < 2) return;
        // cells[0]: "May 4"  cells[1]: "113% of 11,520"
        var dateMatch = cells[0].match(/(\\w+)\\s+(\\d+)/);
        var pctMatch  = cells[1].match(/(\\d+(?:\\.\\d+)?)%\\s+of\\s+([\\d,]+)/);
        if (!dateMatch || !pctMatch) return;
        var m = MONTHS[dateMatch[1]]; if (!m) return;
        var d = parseInt(dateMatch[2]);
        var pct  = parseFloat(pctMatch[1]) / 100;
        var goal = parseInt(pctMatch[2].replace(/,/g,''));
        var steps = Math.round(pct * goal);
        var iso = YEAR + '-' + String(m).padStart(2,'0') + '-' + String(d).padStart(2,'0');
        out[iso] = steps;
      });
      return JSON.stringify(out);
    })()
    """
    raw = eval_js(target_id, js)
    return json.loads(raw)


# ── Patch HTML ────────────────────────────────────────────────────────────────

def patch_html(new_data: dict[str, int], dry_run: bool) -> None:
    """
    Update the embedded `const CSV` block in 10000/index.html with new/changed
    rows from new_data.
    """
    text = HTML_FILE.read_text()

    # Extract existing CSV block between backtick lines
    pattern = r'(const CSV = `date,steps\n)(.*?)(`;\n)'
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        sys.exit("Could not locate `const CSV` block in index.html")

    prefix, csv_body, suffix = match.group(1), match.group(2), match.group(3)

    # Parse existing rows
    existing: dict[str, int] = {}
    for line in csv_body.strip().splitlines():
        parts = line.split(",")
        if len(parts) == 2:
            existing[parts[0].strip()] = int(parts[1].strip())

    # Merge: new_data wins (Garmin may have refined a day's count)
    merged = {**existing, **new_data}
    merged_sorted = dict(sorted(merged.items()))

    new_csv_body = "\n".join(f"{d},{s}" for d, s in merged_sorted.items()) + "\n"

    if new_csv_body == csv_body:
        print("HTML: no changes needed.")
        return

    new_text = text[: match.start(2)] + new_csv_body + text[match.end(2) :]

    added   = set(new_data) - set(existing)
    updated = {d for d in new_data if d in existing and existing[d] != new_data[d]}
    print(f"HTML: adding {len(added)} day(s), updating {len(updated)} day(s).")
    for d in sorted(added):
        print(f"  + {d}: {new_data[d]:,}")
    for d in sorted(updated):
        print(f"  ~ {d}: {existing[d]:,} → {new_data[d]:,}")

    if not dry_run:
        HTML_FILE.write_text(new_text)
        print(f"Written: {HTML_FILE}")


# ── Patch steps.csv ───────────────────────────────────────────────────────────

def patch_steps_csv(new_data: dict[str, int], garmin_goals: dict[str, int], dry_run: bool) -> None:
    """
    Append or update rows in steps.csv (MM/DD/YYYY,Actual,Goal format).
    garmin_goals maps YYYY-MM-DD → goal steps (may be 0 if unavailable).
    """
    raw = STEPS_CSV.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()

    existing: dict[str, list[str]] = {}
    for line in lines[1:]:  # skip header
        parts = line.split(",")
        if len(parts) >= 1 and parts[0].strip():
            try:
                dt = datetime.strptime(parts[0].strip(), "%m/%d/%Y")
                existing[dt.strftime("%Y-%m-%d")] = parts
            except ValueError:
                pass

    added = updated = 0
    for iso, steps in sorted(new_data.items()):
        mmddyyyy = datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y")
        goal = garmin_goals.get(iso, 0)
        row = [mmddyyyy, str(steps), str(goal)]
        if iso not in existing:
            existing[iso] = row
            added += 1
        elif existing[iso][1] != str(steps):
            existing[iso] = row
            updated += 1

    print(f"steps.csv: adding {added} row(s), updating {updated} row(s).")
    if dry_run:
        return

    sorted_rows = sorted(existing.items())
    out_lines = [",Actual,Goal"]
    for _, row in sorted_rows:
        out_lines.append(",".join(row))
    STEPS_CSV.write_text("\n".join(out_lines) + "\n")
    print(f"Written: {STEPS_CSV}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Sync Garmin steps → dashboard.")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without writing files.")
    ap.add_argument("--dom-only", action="store_true", help="Read from DOM instead of clicking Export.")
    args = ap.parse_args()

    # Ensure cdp daemon is running
    subprocess.run(["cdp", "daemon", "start", "--auto-connect", "--json"],
                   capture_output=True, text=True)

    target_id = find_garmin_target()
    print(f"Found Garmin tab: {target_id}")

    if args.dom_only:
        # DOM extraction gives less data (only current week visible in table)
        new_data = fetch_via_dom(target_id)
        garmin_goals: dict[str, int] = {}
    else:
        # Export button downloads a full 4-week CSV
        raw_download = fetch_via_export(target_id)
        new_data = raw_download

        # Re-parse the downloaded file for goal values
        downloads = Path.home() / "Downloads"
        latest = max(downloads.glob("Steps*.csv"), key=lambda f: f.stat().st_mtime, default=None)
        garmin_goals = {}
        if latest:
            text = latest.read_text(encoding="utf-8-sig")
            reader = csv.reader(text.splitlines())
            next(reader, None)
            for row in reader:
                if len(row) >= 3:
                    try:
                        dt = datetime.strptime(row[0].strip(), "%m/%d/%Y")
                        garmin_goals[dt.strftime("%Y-%m-%d")] = int(row[2].strip().replace(",", ""))
                    except (ValueError, IndexError):
                        pass

    if not new_data:
        print("No data retrieved.")
        sys.exit(1)

    print(f"Retrieved {len(new_data)} day(s) from Garmin.")
    patch_html(new_data, dry_run=args.dry_run)
    patch_steps_csv(new_data, garmin_goals, dry_run=args.dry_run)

    if not args.dry_run:
        print("\nDone. Open 10000/index.html in a browser to verify.")


if __name__ == "__main__":
    main()
