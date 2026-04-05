#!/usr/bin/env python3
"""Poll SLC airport TSA wait times and append to CSV."""

import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

API_URL = "https://www.slcairport.com/ajaxtsa/waittimes"
CSV_FILE = os.path.join(os.path.dirname(__file__), "data", "waittimes.csv")
BRRR_SECRET = os.environ.get("BRRR_SECRET", "")
MT = timezone(timedelta(hours=-6))  # Mountain Daylight Time (UTC-6 in April)

CSV_HEADERS = [
    "timestamp_utc",
    "timestamp_mt",
    "rightnow",
    "rightnow_description",
    "estimated_4am",
    "estimated_5am",
    "estimated_6am",
    "precheck_cp1",
    "precheck_cp2",
]


def fetch_waittimes():
    req = urllib.request.Request(API_URL, headers={"User-Agent": "slc-tsa-poller/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def extract_row(data):
    now_utc = datetime.now(timezone.utc)
    now_mt = now_utc.astimezone(MT)

    hourly = {h["hour"]: h["waittime"] for h in data.get("estimated_hourly_times", [])}

    precheck = data.get("precheck_checkpoints", {}).get("Terminal 1", {})

    return {
        "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_mt": now_mt.strftime("%Y-%m-%d %H:%M:%S"),
        "rightnow": data.get("rightnow", ""),
        "rightnow_description": data.get("rightnow_description", ""),
        "estimated_4am": hourly.get(4, ""),
        "estimated_5am": hourly.get(5, ""),
        "estimated_6am": hourly.get(6, ""),
        "precheck_cp1": precheck.get("Checkpoint 1", ""),
        "precheck_cp2": precheck.get("Checkpoint 2", ""),
    }


def send_push(row):
    if not BRRR_SECRET:
        print("BRRR_SECRET not set, skipping push notification")
        return

    msg = (
        f"SLC TSA @ {row['timestamp_mt']} MT\n"
        f"Live wait: {row['rightnow']} min ({row['rightnow_description']})\n"
        f"Avg 4-5am: {row['estimated_4am']}m | 5-6am: {row['estimated_5am']}m\n"
        f"PreCheck CP1: {row['precheck_cp1']} | CP2: {row['precheck_cp2']}"
    )

    payload = json.dumps({
        "title": f"TSA: {row['rightnow']} min",
        "message": msg,
        "sound": "default",
    }).encode()

    url = f"https://api.brrr.now/v1/{BRRR_SECRET}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Push sent: {resp.status}")
    except Exception as e:
        print(f"Push failed: {e}", file=sys.stderr)


def append_csv(row):
    os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
    file_exists = os.path.isfile(CSV_FILE)

    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def main():
    print(f"Fetching TSA wait times from {API_URL}...")
    data = fetch_waittimes()
    row = extract_row(data)
    append_csv(row)
    print(f"Logged: {row['timestamp_mt']} MT | Live: {row['rightnow']} min")

    send_push(row)

    # Also dump full JSON for debugging on first run
    debug_file = os.path.join(os.path.dirname(__file__), "data", "latest.json")
    with open(debug_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Full response saved to {debug_file}")


if __name__ == "__main__":
    main()
