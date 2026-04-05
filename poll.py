#!/usr/bin/env python3
"""Poll SLC airport TSA wait times and append to CSV."""

import csv
import json
import os
import sys
import urllib.error
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


def append_csv(row):
    os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
    file_exists = os.path.isfile(CSV_FILE)

    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def summarize_today():
    """Read today's CSV rows and send a single summary push."""
    if not os.path.isfile(CSV_FILE):
        print("No CSV file found, nothing to summarize")
        return

    today_mt = datetime.now(timezone.utc).astimezone(MT).strftime("%Y-%m-%d")
    rows = []
    with open(CSV_FILE, newline="") as f:
        for row in csv.DictReader(f):
            if row["timestamp_mt"].startswith(today_mt):
                rows.append(row)

    if not rows:
        print("No data for today yet")
        return

    waits = [int(r["rightnow"]) for r in rows if r["rightnow"]]
    peak = max(waits)
    low = min(waits)
    avg = sum(waits) / len(waits)

    # Find the row closest to 4:45am
    target_445 = None
    for r in rows:
        t = r["timestamp_mt"]
        if "04:4" in t or "04:50" in t:
            target_445 = r

    latest = rows[-1]

    lines = [
        f"SLC TSA Morning Summary ({today_mt})",
        f"Samples: {len(rows)} polls, 4:00-6:55am MT",
        f"",
        f"Live wait range: {low}-{peak} min (avg {avg:.0f})",
        f"Latest ({latest['timestamp_mt'].split(' ')[1]}): {latest['rightnow']} min",
    ]
    if target_445:
        lines.append(f"At ~4:45am: {target_445['rightnow']} min")
    lines += [
        f"",
        f"Hourly estimates: 4am={latest['estimated_4am']}m  5am={latest['estimated_5am']}m  6am={latest['estimated_6am']}m",
        f"PreCheck: CP1={latest['precheck_cp1']}  CP2={latest['precheck_cp2']}",
    ]

    msg = "\n".join(lines)
    print(msg)
    send_push_payload(f"SLC TSA: {low}-{peak} min range", msg)


def send_push_payload(title, msg):
    """Send a push notification via brrr.now, trying both auth methods."""
    if not BRRR_SECRET:
        print("BRRR_SECRET not set, skipping push")
        return

    payload = json.dumps({"title": title, "message": msg, "sound": "default"}).encode()

    # Browser-like headers to avoid Cloudflare 1010 blocks on CI
    base_headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    attempts = [
        ("https://api.brrr.now/v1/send", {**base_headers, "Authorization": f"Bearer {BRRR_SECRET}"}),
        (f"https://api.brrr.now/v1/{BRRR_SECRET}", base_headers),
    ]
    for url, headers in attempts:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode()
                print(f"Push sent: {resp.status} {body}")
                return
        except urllib.error.HTTPError as e:
            body = e.read().decode() if hasattr(e, 'read') else ''
            print(f"Push attempt failed: {e.code} {body}", file=sys.stderr)
        except Exception as e:
            print(f"Push attempt failed: {e}", file=sys.stderr)
    print("All push attempts failed", file=sys.stderr)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true", help="Send daily summary instead of polling")
    args = parser.parse_args()

    if args.summary:
        summarize_today()
        return

    print(f"Fetching TSA wait times from {API_URL}...")
    data = fetch_waittimes()
    row = extract_row(data)
    append_csv(row)
    print(f"Logged: {row['timestamp_mt']} MT | Live: {row['rightnow']} min")

    # Dump full JSON for debugging
    debug_file = os.path.join(os.path.dirname(__file__), "data", "latest.json")
    with open(debug_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Full response saved to {debug_file}")


if __name__ == "__main__":
    main()
