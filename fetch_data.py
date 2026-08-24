"""
Fetches Google Scholar data (via ScraperAPI proxy) for faculty listed in
faculty_metrics.xlsx, and saves it to scholar_data.json.

USAGE:
    python fetch_data.py                  # fetch HALF of all faculty (default)
    python fetch_data.py --all             # fetch every faculty member
    python fetch_data.py --limit 20        # fetch a specific number
    python fetch_data.py --department CMPN # fetch only one department
    python fetch_data.py --department CMPN --limit 10

Re-running is safe: faculty already successfully fetched are skipped
(resume-friendly), unless you pass --refetch to force re-fetching everyone
matched by your filters.

NOTE ON "DATE ADDED": Google Scholar only reports a publication's YEAR, not
month. So each publication gets a `date_added` timestamp = the moment WE
first pulled it into this system. That's what powers the monthly report
feature in the Flask app ("X new publications added this month"), since a
true "published in this exact month" report isn't possible from Scholar data.
"""
import argparse
import json
import math
import time
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from scholarly import scholarly, ProxyGenerator

from config import SCRAPER_API_KEY
import db as dbmod

DATA_FILE = Path(__file__).parent / "scholar_data.json"
EXCEL_FILE = Path(__file__).parent / "faculty_metrics.xlsx"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_args():
    p = argparse.ArgumentParser(description="Fetch Google Scholar data for faculty.")
    p.add_argument("--all", action="store_true", help="Fetch every faculty member (ignore --limit).")
    p.add_argument("--limit", type=int, default=None,
                    help="How many faculty to fetch this run. Defaults to HALF of the total.")
    p.add_argument("--department", type=str, default=None, help="Only fetch faculty from this department.")
    p.add_argument("--refetch", action="store_true", help="Re-fetch even faculty already saved.")
    return p.parse_args()


# ==========================================
# LOAD EXISTING DATA (resume support)
# ==========================================
def load_existing_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = {k: v for k, v in data.items() if not v.get("error")}
        print(f"Resuming — {len(data)} faculty already successfully fetched.")
        return data
    return {}


def save(all_data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)


# ==========================================
# PROXY SETUP
# ==========================================
def setup_proxy():
    if not SCRAPER_API_KEY or SCRAPER_API_KEY.startswith("PUT_YOUR_REAL_KEY"):
        print("❌ SCRAPER_API_KEY is not set. Add a real key to your .env file (see .env.example).")
        sys.exit(1)
    try:
        pg = ProxyGenerator()
        success = pg.ScraperAPI(SCRAPER_API_KEY)
        print(f"Proxy Connected: {success}")
        scholarly.use_proxy(pg)
        print("✅ ScraperAPI proxy active\n")
    except Exception as e:
        print(f"❌ Proxy setup error: {e}")
        sys.exit(1)


# ==========================================
# FETCH ONE AUTHOR
# ==========================================
def fetch_author(scholar_id, name, previous_entry=None):
    """previous_entry lets us preserve original date_added for publications
    we've already seen in an earlier run, instead of stamping them as 'new'
    every time the script re-runs."""
    prev_pubs_by_key = {}
    if previous_entry:
        for p in previous_entry.get("publications", []):
            key = (p.get("title", ""), str(p.get("year", "")))
            prev_pubs_by_key[key] = p.get("date_added")

    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])

    pubs = []
    fetch_time = now_iso()

    for pub in author.get("publications", []):
        bib = pub.get("bib", {})
        title = bib.get("title", "Untitled")
        year = bib.get("pub_year", "N/A")
        key = (title, str(year))
        date_added = prev_pubs_by_key.get(key) or fetch_time

        pubs.append({
            "title": title,
            "year": year,
            "journal": bib.get("venue", bib.get("journal", "Unknown")),
            "citations": pub.get("num_citations", 0),
            "abstract": bib.get("abstract", ""),
            "url": pub.get("pub_url", ""),
            "date_added": date_added,
            "source": "scholar",
        })

    pubs.sort(key=lambda x: str(x["year"]), reverse=True)

    return {
        "name": author.get("name", name),
        "affiliation": author.get("affiliation", "VIT"),
        "interests": author.get("interests", []),
        "cited_by": author.get("citedby", 0),
        "cited_by_5y": author.get("citedby5y", 0),
        "hindex": author.get("hindex", 0),
        "hindex5y": author.get("hindex5y", 0),
        "i10index": author.get("i10index", 0),
        "i10index5y": author.get("i10index5y", 0),
        "publications": pubs,
        "scholar_id": scholar_id,
    }


def main():
    args = parse_args()

    df = pd.read_excel(EXCEL_FILE)

    if args.department:
        df = df[df["Department"].str.lower() == args.department.lower()]
        if df.empty:
            print(f"❌ No faculty found in department '{args.department}'. "
                  f"Available: {sorted(pd.read_excel(EXCEL_FILE)['Department'].unique())}")
            sys.exit(1)

    total_in_scope = len(df)

    if args.all:
        limit = total_in_scope
    elif args.limit is not None:
        limit = args.limit
    else:
        # Default: fetch HALF of the faculty in scope (rounded up), as requested.
        limit = math.ceil(total_in_scope / 2)

    print(f"Faculty in scope: {total_in_scope} | Fetching up to: {limit}"
          f"{' (department: ' + args.department + ')' if args.department else ''}\n")

    all_data = {} if args.refetch else load_existing_data()

    setup_proxy()

    fetched_this_run = 0

    for _, row in df.iterrows():
        if fetched_this_run >= limit:
            print(f"\nReached limit of {limit} for this run. Run again (or use --limit/--all) to fetch more.")
            break

        name = row["Name"]
        scholar_id = str(row["Scholar ID"]).strip()

        if scholar_id in ("Not Found", "nan", ""):
            print(f"⚠️ Skipping {name} — no Scholar ID")
            continue

        if scholar_id in all_data and not args.refetch:
            print(f"✅ Already done: {name}")
            continue

        print(f"🔍 Fetching: {name}")

        try:
            previous_entry = all_data.get(scholar_id)
            result = fetch_author(scholar_id, name, previous_entry)
            all_data[scholar_id] = result
            fetched_this_run += 1
            print(f"✅ {len(result['publications'])} papers, {result['cited_by']} citations")
            save(all_data)
        except Exception as e:
            print(f"❌ Failed: {repr(e)}")
            all_data[scholar_id] = {"name": name, "error": True, "scholar_id": scholar_id}
            save(all_data)

        time.sleep(random.uniform(5, 10))

    # Stamp "last updated" so the Flask app can show it to users.
    dbmod.init_db()
    dbmod.set_meta("scholar_last_updated", now_iso())
    dbmod.set_meta("scholar_last_fetch_count", str(fetched_this_run))

    ok = sum(1 for v in all_data.values() if not v.get("error"))
    err = sum(1 for v in all_data.values() if v.get("error"))

    print("\n================================")
    print(f"Fetched this run : {fetched_this_run}")
    print(f"Successful       : {ok}")
    print(f"Errors           : {err}")
    print("================================")
    print("\nNow run:\npython app.py")


if __name__ == "__main__":
    main()
