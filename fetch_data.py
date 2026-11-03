"""
Fetches Google Scholar data (via ScraperAPI proxy) for faculty members.
Persistently stores results in database (PostgreSQL / SQLite) and scholar_data.json.
Enforces 7-day cache freshness, bounded batching, and non-destructive failure handling.

USAGE:
    python fetch_data.py                  # fetch faculty requiring update (default limit)
    python fetch_data.py --all             # fetch every faculty member needing refresh
    python fetch_data.py --limit 10        # fetch a specific number
    python fetch_data.py --department CMPN # fetch only one department
    python fetch_data.py --refetch         # force re-fetch even if fresh (<7 days)
"""
import argparse
import json
import math
import time
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scholarly import scholarly, ProxyGenerator
from config import SCRAPER_API_KEY
import db as dbmod

DATA_FILE = Path(__file__).parent / "scholar_data.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def is_older_than_days(iso_str, days=7):
    if not iso_str:
        return True
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt > timedelta(days=days)
    except Exception:
        return True


def parse_args():
    p = argparse.ArgumentParser(description="Fetch Google Scholar data for faculty.")
    p.add_argument("--all", action="store_true", help="Fetch every faculty member (ignore --limit).")
    p.add_argument("--limit", type=int, default=None, help="How many faculty to fetch this run.")
    p.add_argument("--department", type=str, default=None, help="Only fetch faculty from this department.")
    p.add_argument("--refetch", action="store_true", help="Re-fetch even faculty fetched within last 7 days.")
    return p.parse_args()


def load_existing_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save(all_data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: could not write local cache: {e}")


def setup_proxy():
    if not SCRAPER_API_KEY or SCRAPER_API_KEY.startswith("PUT_YOUR_REAL_KEY"):
        raise RuntimeError("SCRAPER_API_KEY is not set.")
    try:
        pg = ProxyGenerator()
        success = pg.ScraperAPI(SCRAPER_API_KEY)
        if not success:
            raise RuntimeError("ScraperAPI proxy setup returned False.")
        scholarly.use_proxy(pg)
        print("✅ ScraperAPI proxy active\n")
    except Exception as e:
        print(f"❌ Proxy setup error: {e}")
        raise


def fetch_author(scholar_id, name, previous_entry=None):
    """Fetch one author's profile and publications, preserving earlier date_added stamps."""
    prev_pubs_by_key = {}
    if previous_entry:
        for p in previous_entry.get("publications", []):
            key = (p.get("title", "").strip().lower(), str(p.get("year", "")).strip())
            prev_pubs_by_key[key] = p.get("date_added")

    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])

    pubs = []
    fetch_time = now_iso()

    for pub in author.get("publications", []):
        bib = pub.get("bib", {})
        title = bib.get("title", "Untitled").strip()
        year = str(bib.get("pub_year", "N/A")).strip()
        key = (title.lower(), year)
        date_added = prev_pubs_by_key.get(key) or fetch_time

        citations = pub.get("num_citations", 0)
        journal = bib.get("venue", bib.get("journal", "Unknown"))
        url = pub.get("pub_url", "")
        abstract = bib.get("abstract", "")

        pubs.append({
            "title": title,
            "year": year,
            "journal": journal,
            "citations": citations,
            "abstract": abstract,
            "url": url,
            "date_added": date_added,
            "source": "scholar",
        })

        # Save to database
        dbmod.upsert_publication(
            scholar_id=scholar_id,
            title=title,
            year=year,
            journal=journal,
            citations=citations,
            url=url,
            abstract=abstract,
            source="scholar",
            date_added=date_added
        )

    pubs.sort(key=lambda x: str(x["year"]), reverse=True)

    cited_by = author.get("citedby", 0)
    cited_by_5y = author.get("citedby5y", 0)
    hindex = author.get("hindex", 0)
    hindex5y = author.get("hindex5y", 0)
    i10index = author.get("i10index", 0)
    i10index5y = author.get("i10index5y", 0)

    # Save metrics to database
    dbmod.upsert_faculty_metrics(
        scholar_id=scholar_id,
        cited_by=cited_by,
        cited_by_5y=cited_by_5y,
        hindex=hindex,
        hindex5y=hindex5y,
        i10index=i10index,
        i10index5y=i10index5y,
        status="success"
    )

    return {
        "name": author.get("name", name),
        "affiliation": author.get("affiliation", "VIT"),
        "interests": author.get("interests", []),
        "cited_by": cited_by,
        "cited_by_5y": cited_by_5y,
        "hindex": hindex,
        "hindex5y": hindex5y,
        "i10index": i10index,
        "i10index5y": i10index5y,
        "publications": pubs,
        "scholar_id": scholar_id,
        "fetched_at": fetch_time,
    }


def refresh_batch(limit=5, department=None, max_age_days=7, force_refetch=False):
    """
    Programmatic function to refresh a bounded batch of faculty.
    Safe for serverless execution / cron endpoint.
    """
    if limit <= 0:
        return {"batch_size": 0, "refreshed": 0, "errors": 0, "message": "Batch size 0 requested."}

    dbmod.init_db()
    faculty_list = dbmod.get_all_faculty(department=department)
    all_data = load_existing_data()

    # Filter eligible faculty
    eligible = []
    for f in faculty_list:
        sid = (f.get("scholar_id") or "").strip()
        if not sid or sid in ("Not Found", "nan", "None"):
            continue
        last_fetched = f.get("last_fetched_at")
        if force_refetch or is_older_than_days(last_fetched, days=max_age_days):
            eligible.append(f)

    if not eligible:
        return {"refreshed": 0, "errors": 0, "message": "All eligible faculty are fresh (<7 days)."}

    try:
        setup_proxy()
    except Exception as e:
        return {"refreshed": 0, "errors": 0, "error": f"Proxy setup failed: {str(e)}"}

    success_count = 0
    error_count = 0
    details = []

    batch = eligible[:limit]
    for fac in batch:
        sid = fac["scholar_id"]
        name = fac["name"]
        try:
            prev = all_data.get(sid)
            result = fetch_author(sid, name, prev)
            all_data[sid] = result
            success_count += 1
            details.append(f"SUCCESS: {name} ({sid})")
            save(all_data)
        except Exception as e:
            error_count += 1
            details.append(f"ERROR: {name} ({sid}): {str(e)}")
            # NON-DESTRUCTIVE: Keep previous good data if present!
            if sid not in all_data:
                all_data[sid] = {"name": name, "error": True, "scholar_id": sid, "error_message": str(e)}
                save(all_data)
            dbmod.upsert_faculty_metrics(scholar_id=sid, status="error", error_msg=str(e))

        time.sleep(random.uniform(2, 5))

    dbmod.set_meta("scholar_last_updated", now_iso())
    dbmod.log_refresh_event(len(batch), success_count, error_count, "; ".join(details))
    return {
        "batch_size": len(batch),
        "refreshed": success_count,
        "errors": error_count,
        "remaining_eligible": len(eligible) - len(batch)
    }


def main():
    args = parse_args()
    dbmod.init_db()

    faculty_list = dbmod.get_all_faculty(department=args.department)
    if not faculty_list:
        print(f"❌ No faculty found in database.")
        sys.exit(1)

    print(f"Total faculty in database: {len(faculty_list)}")

    # Filter eligible
    eligible = []
    for f in faculty_list:
        sid = (f.get("scholar_id") or "").strip()
        if not sid or sid in ("Not Found", "nan", "None"):
            continue
        last_fetched = f.get("last_fetched_at")
        if args.refetch or is_older_than_days(last_fetched, days=7):
            eligible.append(f)

    print(f"Faculty requiring update (older than 7 days): {len(eligible)}")

    if not eligible:
        print("✅ All faculty data is currently fresh (< 7 days old). Pass --refetch to force refresh.")
        return

    limit = len(eligible) if args.all else (args.limit or min(10, len(eligible)))
    print(f"Fetching up to: {limit} faculty records this run...\n")

    res = refresh_batch(limit=limit, department=args.department, max_age_days=7, force_refetch=args.refetch)
    print("=" * 40)
    print(f"Batch completed: {res}")
    print("=" * 40)


if __name__ == "__main__":
    main()
