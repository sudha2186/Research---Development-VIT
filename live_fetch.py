"""
Live, on-demand Google Scholar fetch for a SINGLE faculty member.

Used by app.py when someone views/searches a faculty profile that hasn't
been pre-fetched yet (i.e. not yet in scholar_data.json). Instead of showing
"no data", we fetch that one person live via ScraperAPI right at request
time, cache the result into scholar_data.json, and serve it.

"""
import time
from datetime import datetime, timezone

from scholarly import scholarly, ProxyGenerator

from config import SCRAPER_API_KEY

_proxy_configured = False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_proxy():
    """Set up the ScraperAPI proxy once per running process (not per request)."""
    global _proxy_configured
    if _proxy_configured:
        return
    if not SCRAPER_API_KEY or SCRAPER_API_KEY.startswith("PUT_YOUR_REAL_KEY"):
        raise RuntimeError("SCRAPER_API_KEY is not set. Add a real key in your .env / Render environment variables.")
    pg = ProxyGenerator()
    success = pg.ScraperAPI(SCRAPER_API_KEY)
    if not success:
        raise RuntimeError("ScraperAPI proxy setup failed — check your API key/quota.")
    scholarly.use_proxy(pg)
    _proxy_configured = True


def fetch_author_live(scholar_id, name, previous_entry=None):
    """Fetch one author's full profile + publications from Google Scholar right now."""
    ensure_proxy()

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
        "fetched_live": True,
        "fetched_at": fetch_time,
    }
