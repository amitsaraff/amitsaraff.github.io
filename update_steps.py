#!/usr/bin/env python3
"""Sync Garmin daily steps directly into steps.csv and the dashboard.

Setup (Python 3.12+):
    uv venv
    uv pip install -r requirements-steps.txt

Usage:
    .venv/bin/python update_steps.py --dry-run
    .venv/bin/python update_steps.py
    .venv/bin/python update_steps.py --start 2026-03-01 --end 2026-09-25

The default range is the last 28 days, including today. First login prompts
for email, password and MFA if required. Later runs reuse tokens stored outside
this repository in ~/.garminconnect (override with --token-dir or GARMINTOKENS).
--dry-run still logs in and may refresh tokens, but leaves site files untouched.
"""

import argparse
import csv
import io
import os
import re
import sys
from datetime import date, datetime, timedelta
from getpass import getpass
from pathlib import Path

REPO_ROOT = Path(__file__).parent
HTML_FILE = REPO_ROOT / "10000" / "index.html"
STEPS_CSV = REPO_ROOT / "steps.csv"


def login(token_dir: Path):
    """Restore tokens before asking for credentials; never save the password."""
    from garminconnect import Garmin, GarminConnectAuthenticationError

    client = Garmin()
    try:
        client.login(str(token_dir))
        print("Logged in using saved Garmin tokens.")
        return client
    except GarminConnectAuthenticationError:
        pass

    if not sys.stdin.isatty():
        raise RuntimeError("Garmin login required. Run this script in a terminal first.")

    print("Garmin login required; credentials are sent directly to Garmin.")
    client = Garmin(
        email=os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip(),
        password=getpass("Garmin password: "),
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    client.login(str(token_dir))
    print(f"Logged in. Garmin tokens saved in {token_dir}.")
    return client


def parse_daily_steps(rows: list[dict], start: date, end: date) -> dict[str, tuple[int, int | None]]:
    """Use exact totals; missing totals are not zero-step days."""
    data = {}
    for row in rows:
        day = date.fromisoformat(row["calendarDate"])
        if not start <= day <= end or row.get("totalSteps") is None:
            continue
        steps = row["totalSteps"]
        goal = row.get("stepGoal")
        if type(steps) is not int or steps < 0:
            raise ValueError(f"Invalid step count for {day}.")
        if goal is not None and (type(goal) is not int or goal < 0):
            raise ValueError(f"Invalid step goal for {day}.")
        data[day.isoformat()] = (steps, goal)
    return data


def read_steps_csv() -> dict[str, tuple[int, int]]:
    """Read the Garmin-format CSV, including quoted numbers."""
    with STEPS_CSV.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        next(reader)
        return {
            datetime.strptime(row[0], "%m/%d/%Y").date().isoformat():
            (int(row[1].replace(",", "")), int(row[2].replace(",", "")))
            for row in reader if row
        }


def update_files(new_data: dict[str, tuple[int, int | None]], dry_run: bool) -> None:
    """Merge exact counts and render both outputs before writing either file."""
    html = HTML_FILE.read_text(encoding="utf-8")
    match = re.search(r"(const CSV = `date,steps\n)(.*?)(`;)", html, re.DOTALL)
    if not match:
        raise ValueError("Could not locate the dashboard's const CSV block.")
    existing = read_steps_csv()
    # Older iPhone counts may exist only in the dashboard. Import those dates
    # before merging Garmin data; use 0 for their unavailable daily goals.
    recovered = 0
    for day, steps in csv.reader(io.StringIO(match.group(2))):
        day = date.fromisoformat(day).isoformat()
        if day not in existing:
            existing[day] = (int(steps), 0)
            recovered += 1
    if recovered:
        print(f"Preserving {recovered} dashboard-only day(s) in steps.csv.")
    merged = existing.copy()
    for day, (steps, goal) in new_data.items():
        merged[day] = (steps, goal if goal is not None else existing.get(day, (0, 0))[1])

    added = new_data.keys() - existing.keys()
    updated = {day for day in new_data if day in existing and merged[day] != existing[day]}
    print(f"Adding {len(added)} day(s), updating {len(updated)} day(s).")
    for day in sorted(added | updated):
        print(f"  {day}: {merged[day][0]:,} steps, goal {merged[day][1]:,}")

    csv_output = io.StringIO(newline="")
    writer = csv.writer(csv_output, lineterminator="\n")
    writer.writerow(["", "Actual", "Goal"])
    for day, (steps, goal) in sorted(merged.items()):
        writer.writerow([date.fromisoformat(day).strftime("%m/%d/%Y"), steps, goal])

    body = "".join(f"{day},{steps}\n" for day, (steps, _) in sorted(merged.items()))
    new_html = html[:match.start(2)] + body + html[match.end(2):]

    for path, content in [(STEPS_CSV, csv_output.getvalue()), (HTML_FILE, new_html)]:
        if path.read_text(encoding="utf-8") == content:
            print(f"{path.relative_to(REPO_ROOT)}: no changes.")
        elif dry_run:
            print(f"Would update {path.relative_to(REPO_ROOT)}.")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"Updated {path.relative_to(REPO_ROOT)}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Garmin steps to the dashboard without a browser.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing site files.")
    parser.add_argument("--start", type=date.fromisoformat, help="First day, YYYY-MM-DD (default: end minus 27 days).")
    parser.add_argument("--end", type=date.fromisoformat, default=date.today(), help="Last day, YYYY-MM-DD (default: today).")
    parser.add_argument("--token-dir", type=Path, default=Path(os.getenv("GARMINTOKENS", "~/.garminconnect")), help="Directory for saved Garmin tokens.")
    args = parser.parse_args()
    start = args.start or args.end - timedelta(days=27)
    if start > args.end:
        parser.error("--start must be on or before --end")

    try:
        client = login(args.token_dir.expanduser())
        rows = client.get_daily_steps(start.isoformat(), args.end.isoformat())
        new_data = parse_daily_steps(rows, start, args.end)
        if not new_data:
            raise ValueError("No step totals retrieved; site files were not changed.")
        print(f"Retrieved {len(new_data)} day(s) from Garmin.")
        update_files(new_data, args.dry_run)
    except ImportError:
        sys.exit("Install dependencies first: uv venv && uv pip install -r requirements-steps.txt")
    except (OSError, ValueError, RuntimeError, EOFError) as exc:
        sys.exit(f"Sync failed: {exc}")
    except Exception as exc:
        # Garmin errors may contain server responses: don't print tokens or
        # credential details from those responses.
        sys.exit(f"Garmin request failed ({type(exc).__name__}). Check your connection or retry login.")


if __name__ == "__main__":
    main()
