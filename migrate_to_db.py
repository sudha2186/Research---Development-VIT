"""
Data Migration and Integration Script for VIT Research Portal.
Integrates faculty from the 4 new documents + existing faculty_metrics.xlsx,
imports scholar_data.json cache into database tables,
seeds/resets demonstration accounts according to specifications.
Can be safely rerun without creating duplicates or overwriting newer data.
"""
import os
import re
import sys
import json
import sqlite3
from pathlib import Path
import pandas as pd
import docx

sys.stdout.reconfigure(encoding='utf-8')

import config
import db

BASE = Path(__file__).parent
FACULTY_LIST_DIR = BASE / "faculty_list"


def clean_scholar_id(val):
    if not val:
        return ""
    val = str(val).strip()
    if val.lower() in ("nan", "not found", "none", "-", ""):
        return ""
    val = re.sub(r"&.*$", "", val)
    m = re.search(r"user=([a-zA-Z0-9_-]+)", val)
    if m:
        return m.group(1).strip()
    return val.strip()


def clean_name(name):
    if not name:
        return ""
    name = str(name).strip()
    # Strip (Initials) like (GGI), (AO), etc.
    name = re.sub(r"\s*\([A-Za-z]+\)\s*", " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name


def normalize_for_matching(name):
    n = clean_name(name).lower()
    n = re.sub(r"^(dr\.|prof\.|mr\.|ms\.|mrs\.)\s*", "", n)
    n = re.sub(r"[^a-z0-9]", "", n)
    return n


def extract_new_faculty():
    records = []

    # 1. CMPN
    cmpn_path = FACULTY_LIST_DIR / "Updated D2 Faculty List_Odd 2026-27.xlsx"
    if cmpn_path.exists():
        df = pd.read_excel(cmpn_path, sheet_name="CMPN Dept D2 list")
        for _, r in df.iterrows():
            raw_name = r.get("Unnamed: 1")
            desig = r.get("Unnamed: 2")
            sid = r.get("Unnamed: 3")
            if pd.isna(raw_name) or str(raw_name).strip() in ("Name of the Staff Member", "Sr. No.", ""):
                continue
            records.append({
                "name": clean_name(raw_name),
                "department": "CMPN",
                "scholar_id": clean_scholar_id(sid),
                "designation": str(desig).strip() if pd.notna(desig) else "",
                "source": "Updated D2 Faculty List_Odd 2026-27.xlsx"
            })

    # 2. FE (First Year Engineering)
    fe_path = FACULTY_LIST_DIR / "D2 List Faculty (1).docx"
    if fe_path.exists():
        doc_fe = docx.Document(fe_path)
        for t in doc_fe.tables:
            for row in t.rows[1:]:
                cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                if len(cells) >= 4 and cells[1]:
                    records.append({
                        "name": clean_name(cells[1]),
                        "department": "FE",
                        "scholar_id": clean_scholar_id(cells[3]),
                        "designation": cells[2] if len(cells) > 2 else "",
                        "source": "D2 List Faculty (1).docx"
                    })

    # 3. IT
    it_path = FACULTY_LIST_DIR / "D2 Faculty List [Odd Sem] A.Y. 2026-27.docx"
    if it_path.exists():
        doc_it = docx.Document(it_path)
        if len(doc_it.tables) > 1:
            t = doc_it.tables[1]
            for row in t.rows[1:]:
                cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                if len(cells) >= 4 and cells[1]:
                    records.append({
                        "name": clean_name(cells[1]),
                        "department": "IT",
                        "scholar_id": clean_scholar_id(cells[3]),
                        "designation": cells[2],
                        "source": "D2 Faculty List [Odd Sem] A.Y. 2026-27.docx"
                    })

    # 4. EXTC
    extc_path = FACULTY_LIST_DIR / "EXTC_D2 List ODD SEM_2026-27.docx"
    if extc_path.exists():
        doc_extc = docx.Document(extc_path)
        if doc_extc.tables:
            t = doc_extc.tables[0]
            for row in t.rows[1:]:
                cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                if len(cells) >= 4 and cells[1]:
                    records.append({
                        "name": clean_name(cells[1]),
                        "department": "EXTC",
                        "scholar_id": clean_scholar_id(cells[3]),
                        "designation": cells[2],
                        "source": "EXTC_D2 List ODD SEM_2026-27.docx"
                    })

    return records


def run_migration():
    print("=" * 60)
    print("STARTING DATA MIGRATION & INTEGRATION")
    print("=" * 60)

    # 1. Initialize database schema
    db.init_db()
    print("✅ Database schema initialized successfully.")

    # 2. Extract new records
    new_records = extract_new_faculty()
    print(f"📄 Extracted {len(new_records)} faculty records from faculty_list directory.")

    # 3. Load existing faculty from faculty_metrics.xlsx
    existing_excel_path = BASE / "faculty_metrics.xlsx"
    existing_df = pd.read_excel(existing_excel_path)
    existing_records = []
    for _, r in existing_df.iterrows():
        dept = str(r["Department"]).strip()
        if dept == "ENTC":
            dept = "EXTC"
        existing_records.append({
            "name": clean_name(r["Name"]),
            "department": dept,
            "scholar_id": clean_scholar_id(r["Scholar ID"]),
            "designation": "",
            "source": "faculty_metrics.xlsx"
        })
    print(f"📄 Loaded {len(existing_records)} faculty records from faculty_metrics.xlsx.")

    # 4. Merge records
    existing_map = {}
    for er in existing_records:
        existing_map[(normalize_for_matching(er["name"]), er["department"])] = er
        existing_map[normalize_for_matching(er["name"])] = er

    merged_faculty = []
    processed_existing = set()
    matched_count = 0
    added_count = 0
    retained_count = 0

    for nr in new_records:
        norm_name = normalize_for_matching(nr["name"])
        match = existing_map.get((norm_name, nr["department"])) or existing_map.get(norm_name)
        if match:
            matched_count += 1
            processed_existing.add((normalize_for_matching(match["name"]), match["department"]))
            processed_existing.add(normalize_for_matching(match["name"]))
            final_sid = match["scholar_id"] or nr["scholar_id"]
            merged_faculty.append({
                "name": match["name"] if len(match["name"]) >= len(nr["name"]) else nr["name"],
                "department": nr["department"],
                "scholar_id": final_sid,
                "designation": nr["designation"] or match["designation"],
                "source": nr["source"],
                "status": "matched"
            })
        else:
            added_count += 1
            merged_faculty.append({
                "name": nr["name"],
                "department": nr["department"],
                "scholar_id": nr["scholar_id"],
                "designation": nr["designation"],
                "source": nr["source"],
                "status": "new"
            })

    # Retain remaining existing faculty (including all Biomedical & legacy records)
    for er in existing_records:
        key_with_dept = (normalize_for_matching(er["name"]), er["department"])
        key_name = normalize_for_matching(er["name"])
        if key_with_dept not in processed_existing and key_name not in processed_existing:
            retained_count += 1
            merged_faculty.append({
                "name": er["name"],
                "department": er["department"],
                "scholar_id": er["scholar_id"],
                "designation": er["designation"],
                "source": er["source"],
                "status": "retained_existing"
            })

    print(f"\n--- MERGE RESULTS ---")
    print(f"Total merged faculty: {len(merged_faculty)}")
    print(f"Matched with existing: {matched_count}")
    print(f"Genuinely new added: {added_count}")
    print(f"Retained existing (Biomedical, etc.): {retained_count}")

    # 5. Insert/Upsert into faculty table
    sid_count = 0
    no_sid_count = 0
    for f in merged_faculty:
        fac_id = db.upsert_faculty(
            name=f["name"],
            department=f["department"],
            scholar_id=f["scholar_id"],
            designation=f["designation"]
        )
        if f["scholar_id"]:
            sid_count += 1
        else:
            no_sid_count += 1
            # Mark metrics as no_id
            db.upsert_faculty_metrics(scholar_id=f"NO_ID_{fac_id}", status="no_id")

    print(f"✅ Upserted {len(merged_faculty)} faculty records to database.")
    print(f"   - With Google Scholar ID: {sid_count}")
    print(f"   - Without Google Scholar ID: {no_sid_count}")

    # 6. Import scholar_data.json cache into database
    scholar_json_path = BASE / "scholar_data.json"
    imported_pubs = 0
    imported_authors = 0
    if scholar_json_path.exists():
        with open(scholar_json_path, "r", encoding="utf-8") as f:
            scholar_cache = json.load(f)

        for sid, author_data in scholar_cache.items():
            if not sid or author_data.get("error") or author_data.get("no_id"):
                continue

            imported_authors += 1
            db.upsert_faculty_metrics(
                scholar_id=sid,
                cited_by=author_data.get("cited_by", 0),
                cited_by_5y=author_data.get("cited_by_5y", 0),
                hindex=author_data.get("hindex", 0),
                hindex5y=author_data.get("hindex5y", 0),
                i10index=author_data.get("i10index", 0),
                i10index5y=author_data.get("i10index5y", 0),
                status="success"
            )

            pubs = author_data.get("publications", [])
            for p in pubs:
                imported_pubs += 1
                db.upsert_publication(
                    scholar_id=sid,
                    title=p.get("title", "Untitled"),
                    year=p.get("year", ""),
                    journal=p.get("journal", ""),
                    citations=p.get("citations", 0),
                    url=p.get("url", ""),
                    abstract=p.get("abstract", ""),
                    source="scholar",
                    date_added=p.get("date_added")
                )

    print(f"✅ Imported {imported_authors} author metrics and {imported_pubs} publications into database.")

    # 7. Sync manual publications from manual_publications table to publications table
    with db.DBConnection() as conn:
        manual_pubs = conn.fetchall("SELECT * FROM manual_publications")
        synced_manual = 0
        for mp in manual_pubs:
            db.upsert_publication(
                scholar_id=mp["scholar_id"],
                title=mp["title"],
                year=mp["year"],
                journal=mp["journal"],
                citations=mp.get("citations", 0),
                url=mp.get("url"),
                source="manual",
                manual_added_by=mp.get("added_by_email"),
                date_added=mp.get("added_at")
            )
            synced_manual += 1
        print(f"✅ Synced {synced_manual} existing manual publications to unified publications table.")

    # 8. Reset the approved demonstration passwords
    db.reset_account_password("student@vit.edu.in", "Student@123")
    db.reset_account_password("admin@vit.edu.in", "Admin@123")
    print("✅ Reset passwords for approved demo accounts: student@vit.edu.in and admin@vit.edu.in.")

    # 9. Verify demo accounts
    demo_check = [
        ("student@vit.edu.in", "student"),
        ("faculty@vit.edu.in", "faculty"),
        ("hod@vit.edu.in", "hod"),
        ("admin@vit.edu.in", "admin"),
    ]
    for email, expected_role in demo_check:
        u = db.get_user_by_email(email)
        if u:
            print(f"   ✔ Demo Account {email}: Role '{u['role']}', Name '{u['name']}', Dept '{u.get('department')}'")
        else:
            print(f"   ❌ Demo Account {email} MISSING!")

    # 10. Update metadata
    db.set_meta("migration_completed_at", db.now_iso())
    db.set_meta("scholar_last_updated", db.now_iso())
    print("\n✅ Migration complete!")


if __name__ == "__main__":
    run_migration()
