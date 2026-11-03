"""
Live, on-demand Google Scholar fetch for a SINGLE faculty member.

Persists fetched results in the database and preserves existing data on failure.
Used by app.py when a faculty member's Google Scholar profile needs fetching.
"""
import time
from datetime import datetime, timezone
from scholarly import scholarly, ProxyGenerator
from config import SCRAPER_API_KEY
import db as dbmod

_proxy_configured = False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_proxy():
    """Set up the ScraperAPI proxy once per running process."""
    global _proxy_configured
    if _proxy_configured:
        return
    if not SCRAPER_API_KEY or SCRAPER_API_KEY.startswith("PUT_YOUR_REAL_KEY"):
        raise RuntimeError("SCRAPER_API_KEY is not configured.")
    pg = ProxyGenerator()
    success = pg.ScraperAPI(SCRAPER_API_KEY)
    if not success:
        raise RuntimeError("ScraperAPI proxy setup failed — check API key/quota.")
    scholarly.use_proxy(pg)
    _proxy_configured = True


def fetch_author_live(scholar_id, name, previous_entry=None):
    """Fetch one author's full profile + publications from Google Scholar and save to DB."""
    if not scholar_id or scholar_id in ("Not Found", "nan", "None", ""):
        return {"name": name, "no_id": True, "scholar_id": scholar_id, "publications": []}

    ensure_proxy()

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
        "fetched_live": True,
        "fetched_at": fetch_time,
    }
