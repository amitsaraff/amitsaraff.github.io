#!/usr/bin/env python3
"""
Fetch the last 4 weeks of steps from the open Garmin Connect tab via CDP,
then update steps.csv and 10000/index.html.

Usage:
    python3 update_steps.py [--dry-run]

Requirements:
    pip install cdp-cli-python  # or use the `cdp` CLI via subprocess
    The cdp daemon must be running: cdp daemon start --auto-connect
    A Garmin Connect tab must be open at the current steps page, for example:
        https://connect.garmin.com/app/steps/2026-09-14/2
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent
HTML_FILE = REPO_ROOT / "10000" / "index.html"
STEPS_CSV = REPO_ROOT / "steps.csv"
LEGACY_GARMIN_URL = "https://connect.garmin.com/app/report/29/wellness/last_four_weeks"
GARMIN_URL = f"https://connect.garmin.com/app/steps/{datetime.now().strftime('%Y-%m-%d')}/2"
EXPORT_BTN_SEL = "button, a, [role='button']"


def page_matches_garmin(url: str) -> bool:
    """Return True for the legacy report page and the current steps page."""
    if not url:
        return False
    return (
        "https://connect.garmin.com/app/steps/" in url
        or "https://connect.garmin.com/app/report/" in url
        or LEGACY_GARMIN_URL in url
    )


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
    """Return the CDP target ID of the Garmin Connect steps tab, opening it if needed."""
    data = cdp("pages")
    for page in data.get("pages", []):
        if page_matches_garmin(page.get("url", "")):
            return page["id"]

    print(f"No Garmin tab open — opening {GARMIN_URL} ...")
    cdp("open", GARMIN_URL)

    target_id = None
    for _ in range(15):
        time.sleep(1)
        data = cdp("pages")
        for page in data.get("pages", []):
            url = page.get("url", "")
            if page_matches_garmin(url) or "garmin.com" in url:
                target_id = page["id"]
                break
        if target_id:
            break
    if not target_id:
        sys.exit(f"Timed out opening {GARMIN_URL}.")

    data = cdp("pages")
    page = next(p for p in data["pages"] if p["id"] == target_id)
    if "sso.garmin.com" in page.get("url", "") or "signin" in page.get("url", "").lower():
        input("Log in to Garmin Connect in the opened browser tab, then press Enter to continue...")
        time.sleep(1)

    data = cdp("pages")
    for page in data.get("pages", []):
        if page_matches_garmin(page.get("url", "")):
            return page["id"]

    # Login redirected away from the steps page — navigate back to it.
    cdp("open", GARMIN_URL, "--new-tab=false", "--target", target_id)
    time.sleep(2)
    data = cdp("pages")
    for page in data.get("pages", []):
        if page_matches_garmin(page.get("url", "")):
            return page["id"]

    sys.exit(f"Still couldn't reach {GARMIN_URL} after login.")


def eval_js(target_id: str, js: str) -> str:
    """Evaluate JS in the target tab and return the string result."""
    data = cdp("eval", "--target", target_id, js)
    return data["result"]["value"]


def click_export(target_id: str) -> bool:
    """Click the actual Garmin Export CSV control, including the menu-triggered version."""
    js = r'''
    (() => {
      const labelText = (el) => {
        const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
        const label = (el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
        return (text + ' ' + label).replace(/\s+/g, ' ').trim();
      };

      const menu = Array.from(document.querySelectorAll('button, [role="button"], div')).find((el) => {
        const text = labelText(el);
        const cls = (el.getAttribute('class') || '').replace(/\s+/g, ' ').trim();
        return /Menu/.test(text) || /widget_menu_title/.test(cls);
      });
      if (menu) {
        menu.click();
      }

      const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, div'));
      const match = candidates.find((el) => /Export CSV/i.test(labelText(el)));
      if (!match) return false;

      const clickable = match.matches && match.matches('button, [role="button"]')
        ? match
        : match.querySelector && match.querySelector('button, [role="button"]');

      if (clickable) {
        clickable.click();
        return true;
      }

      match.click();
      return true;
    })()
    '''

    for _ in range(20):
        if eval_js(target_id, js):
            return True
        time.sleep(1)

    return False


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


def parse_percent_goal_row(raw: str, year: int | None = None) -> tuple[str, int, int]:
    """Parse a value like 'Sep 13 82% of 10,980' into (YYYY-MM-DD, steps, goal)."""
    match = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d+(?:\.\d+)?)%\s+of\s+([\d,]+)", raw.strip())
    if not match:
        raise ValueError(f"Unrecognized percentage row: {raw!r}")

    month = datetime.strptime(match.group(1), "%b").month
    day = int(match.group(2))
    pct = float(match.group(3))
    goal = int(match.group(4).replace(",", ""))
    year = year or datetime.now().year
    iso = datetime(year, month, day).strftime("%Y-%m-%d")
    steps = round(pct / 100 * goal)
    return iso, steps, goal


def parse_day_rows(rows: list[str] | str, year: int | None = None) -> dict[str, tuple[int, int]]:
    """Parse a Garmin 'Date / % of Goal' table into {YYYY-MM-DD: (steps, goal)}."""
    if isinstance(rows, str):
        rows = rows.splitlines()

    parsed: dict[str, tuple[int, int]] = {}
    for raw in rows:
        text = (raw or "").strip()
        if not text or text.lower().startswith("date"):
            continue
        try:
            iso, steps, goal = parse_percent_goal_row(text, year=year)
        except ValueError:
            continue
        parsed[iso] = (steps, goal)
    return parsed


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
    """Click Export when available, otherwise fall back to reading the visible step table."""
    before = datetime.now()
    if click_export(target_id):
        for _ in range(15):
            time.sleep(1)
            path = latest_download(before)
            if path:
                print(f"Downloaded: {path}")
                return parse_garmin_csv(path.read_text(encoding="utf-8-sig"))
        sys.exit("Timed out waiting for the CSV download.")

    print("No legacy export button detected on the current Garmin page; falling back to DOM parsing.")
    steps, _ = fetch_via_dom(target_id)
    return steps


# ── Alternative: read data directly from the DOM via JS ───────────────────────

def fetch_via_dom(target_id: str) -> tuple[dict[str, int], dict[str, int]]:
    """Extract steps+goal data from the current Garmin 4-week daily table, expanding 'Show More' if needed."""
    year = datetime.now().year
    merged_steps: dict[str, int] = {}
    merged_goals: dict[str, int] = {}

    for _ in range(6):
        js = r'''
        (() => {
          const table = Array.from(document.querySelectorAll('table')).find((t) => {
            const text = (t.textContent || '').replace(/\s+/g, ' ').trim();
            return /Date/i.test(text) && /Percent of Goal/i.test(text);
          });
          if (!table) return JSON.stringify([]);

          const rows = [];
          for (const tr of table.querySelectorAll('tr')) {
            const cells = Array.from(tr.querySelectorAll('td')).map((td) => (td.textContent || '').replace(/\s+/g, ' ').trim());
            if (cells.length < 2) continue;
            const dateText = cells[0];
            const pctText = cells[1];
            if (!dateText || !/%/.test(pctText)) continue;
            rows.push(dateText + ' ' + pctText);
          }
          return JSON.stringify(rows);
        })()
        '''
        raw_rows = json.loads(eval_js(target_id, js))
        for key, (steps, goal) in parse_day_rows(raw_rows, year=year).items():
            merged_steps[key] = steps
            merged_goals[key] = goal

        clicked = eval_js(target_id, r'''
        (() => {
          const btn = Array.from(document.querySelectorAll('button')).find((el) => {
            const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
            return /show more/i.test(text);
          });
          if (!btn) return false;
          btn.click();
          return true;
        })()
        ''')
        if clicked is False or clicked == "false":
            break
        time.sleep(0.5)

    return merged_steps, merged_goals


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
        new_data, garmin_goals = fetch_via_dom(target_id)
    else:
        # Prefer the live DOM on the current Garmin page; the export flow is still
        # retained as a fallback when the page exposes a downloadable CSV.
        new_data, garmin_goals = fetch_via_dom(target_id)

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
