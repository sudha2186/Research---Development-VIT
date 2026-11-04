from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user
import pandas as pd
import json
import requests
import os
import uuid
from werkzeug.utils import secure_filename
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import threading

import config
import db
from auth import auth_bp, login_manager, role_required
from live_fetch import fetch_author_live

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = config.SESSION_COOKIE_HTTPONLY
app.config["SESSION_COOKIE_SAMESITE"] = config.SESSION_COOKIE_SAMESITE
app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE

app.register_blueprint(auth_bp)
login_manager.init_app(app)

BASE = Path(__file__).parent
SCHOLAR_DATA_FILE = BASE / "scholar_data.json"
CLOUDINARY_BASE = "https://res.cloudinary.com/dwuhswk2w/image/upload/v1751302313/"
DEFAULT_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/9/99/Sample_User_Icon.png"

# Initialize database schema and base demo accounts
db.init_db()


def get_faculty_dataframe():
    """Returns a pandas DataFrame of all faculty from the authoritative database."""
    fac_list = db.get_all_faculty()
    if fac_list:
        df = pd.DataFrame(fac_list)
        df.rename(columns={"name": "Name", "department": "Department", "scholar_id": "Scholar ID"}, inplace=True)
        return df
    # Fallback to Excel if database is uninitialized
    if (BASE / "faculty_metrics.xlsx").exists():
        return pd.read_excel(BASE / "faculty_metrics.xlsx")
    return pd.DataFrame(columns=["Name", "Department", "Scholar ID"])


def load_scholar_data():
    if SCHOLAR_DATA_FILE.exists():
        try:
            with open(SCHOLAR_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def get_profile_image_url(scholar_id):
    if not scholar_id or scholar_id in ("Not Found", "nan", "None", ""):
        return DEFAULT_IMAGE
    url = f"{CLOUDINARY_BASE}{scholar_id}.jpg"
    try:
        r = requests.head(url, timeout=3)
        return url if r.status_code == 200 else DEFAULT_IMAGE
    except Exception:
        return DEFAULT_IMAGE


_fetch_lock = threading.Lock()


def fetch_and_cache_single(scholar_id, name):
    """If a faculty member hasn't been fetched yet, fetch them live and cache."""
    with _fetch_lock:
        scholar_data = load_scholar_data()
        existing = scholar_data.get(scholar_id)
        if existing and not existing.get("error"):
            return existing

        try:
            result = fetch_author_live(scholar_id, name, existing)
            scholar_data[scholar_id] = result
            with open(SCHOLAR_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(scholar_data, f, indent=2, ensure_ascii=False)
            db.set_meta("scholar_last_updated", datetime.now(timezone.utc).isoformat())
            return result
        except Exception as e:
            return {"name": name, "error": True, "error_message": str(e), "scholar_id": scholar_id}


def get_all_publications(scholar_id, scholar_entry=None):
    """Returns deduplicated publications for this scholar_id from database or entry."""
    # 1. Query unified persistent publications table
    db_pubs = db.get_publications_for_scholar(scholar_id)
    if db_pubs:
        res = []
        for p in db_pubs:
            has_file = bool(p.get("file_path"))
            res.append({
                "id": p["id"],
                "title": p["title"],
                "year": p["year"],
                "journal": p["journal"],
                "citations": p["citations"],
                "url": p["url"],
                "date_added": p["date_added"],
                "source": p["source"],
                "manual_id": p["id"] if p["source"] == "manual" else None,
                "file_path": p.get("file_path"),
                "original_filename": p.get("original_filename"),
                "has_file": has_file,
                "download_url": f"/publication/{p['id']}/download" if has_file else None,
            })
        return res

    # 2. Fallback to scholar_entry + manual_publications
    seen_keys = set()
    pubs = []
    if scholar_entry and "publications" in scholar_entry:
        for p in scholar_entry.get("publications", []):
            k = (p.get("title", "").strip().lower(), str(p.get("year", "")).strip())
            seen_keys.add(k)
            pubs.append({
                "id": None,
                "title": p.get("title", ""),
                "year": p.get("year", ""),
                "journal": p.get("journal", ""),
                "citations": p.get("citations", 0),
                "url": p.get("url", ""),
                "date_added": p.get("date_added"),
                "source": "scholar",
                "manual_id": None,
                "file_path": None,
                "original_filename": None,
                "has_file": False,
                "download_url": None,
            })

    manual = db.get_manual_publications(scholar_id)
    for m in manual:
        k = (m["title"].strip().lower(), str(m.get("year", "")).strip())
        if k not in seen_keys:
            has_file = bool(m.get("file_path"))
            pubs.append({
                "id": m["id"],
                "title": m["title"],
                "year": m["year"],
                "journal": m["journal"],
                "citations": m["citations"],
                "url": m["url"],
                "date_added": m["added_at"],
                "source": "manual",
                "manual_id": m["id"],
                "file_path": m.get("file_path"),
                "original_filename": m.get("original_filename"),
                "has_file": has_file,
                "download_url": f"/publication/{m['id']}/download" if has_file else None,
            })

    pubs.sort(key=lambda x: str(x.get("year", "")), reverse=True)
    return pubs


def is_faculty_active(pubs):
    """
    A faculty member is active if they have published a paper in the current
    or previous calendar year, or manually added a paper in the last 180 days.
    Does NOT mark an author active simply because old papers were ingested recently.
    """
    if not pubs:
        return False

    now = datetime.now(timezone.utc)
    six_months_ago = now - timedelta(days=180)

    for p in pubs:
        # Check manual publications added recently
        if p.get("source") == "manual" and p.get("date_added"):
            try:
                dt = datetime.fromisoformat(str(p["date_added"]).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= six_months_ago:
                    return True
            except Exception:
                pass

        # Check true publication year
        year_str = str(p.get("year", "")).strip()
        if year_str.isdigit():
            pub_year = int(year_str)
            if pub_year >= (now.year - 1):
                return True

    return False


def format_last_updated():
    raw = db.get_meta("scholar_last_updated")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %I:%M %p UTC")
    except Exception:
        return raw


# -------------------------------------------------------------
# SUBDOMAIN / HOST-BASED HOD ROUTING MIDDLEWARE
# -------------------------------------------------------------
@app.before_request
def handle_subdomain_routing():
    host = request.host.split(":")[0].lower()
    target_dept = config.HOD_SUBDOMAIN_MAP.get(host)
    if target_dept:
        request.subdomain_dept = target_dept
        # If user is authenticated as HOD, ensure their department matches the subdomain
        if current_user.is_authenticated and current_user.role == "hod":
            if current_user.department and current_user.department != target_dept:
                flash(f"Access denied: This portal is dedicated to the {target_dept} department. Your account belongs to {current_user.department}.", "error")
                return redirect(url_for("home"))
    else:
        request.subdomain_dept = None


@app.context_processor
def inject_globals():
    unread = 0
    if current_user.is_authenticated and current_user.role in ("admin", "hod"):
        unread = db.unread_notification_count(
            department=current_user.department if current_user.role == "hod" else None
        )
    return {
        "last_updated": format_last_updated(),
        "unread_notifications": unread,
        "primary_departments": config.PRIMARY_DEPARTMENTS,
    }


# -------------------------------------------------------------
# PAGE ROUTES
# -------------------------------------------------------------
@app.route("/")
@login_required
def home():
    if current_user.role == "student":
        return redirect(url_for("faculty_profiles"))
    elif current_user.role == "faculty":
        return redirect(url_for("my_profile"))
    elif current_user.role == "hod":
        return redirect(url_for("department_analysis"))
    return render_template("home.html")


@app.route("/faculty-profiles")
@login_required
def faculty_profiles():
    return render_template("faculty_profiles.html")


@app.route("/department")
@role_required("admin", "hod")
def department():
    if current_user.role == "hod":
        departments = [current_user.department]
        locked_dept = current_user.department
    else:
        departments = config.PRIMARY_DEPARTMENTS
        locked_dept = None
    return render_template("department.html", departments=departments, locked_dept=locked_dept)


@app.route("/impact-metrics")
@role_required("admin", "hod")
def impact_metrics():
    locked_dept = current_user.department if current_user.role == "hod" else None
    return render_template("impact_metrics.html", locked_dept=locked_dept)


@app.route("/my-profile", methods=["GET", "POST"])
@role_required("faculty")
def my_profile():
    if not current_user.scholar_id:
        flash("Your account isn't linked to a Scholar ID yet. Contact admin.", "error")
        return redirect(url_for("faculty_profiles"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        year = request.form.get("year", "").strip()
        journal = request.form.get("journal", "").strip()
        url_ = request.form.get("url", "").strip()

        if not title:
            flash("Title is required.", "error")
            return redirect(url_for("my_profile"))

        file_rel_path = None
        orig_filename = None
        paper_file = request.files.get("paper_file")
        if paper_file and paper_file.filename:
            safe_fname = secure_filename(paper_file.filename)
            ext = safe_fname.rsplit(".", 1)[-1].lower() if "." in safe_fname else ""
            if ext in config.ALLOWED_EXTENSIONS:
                unique_name = f"{uuid.uuid4().hex[:10]}_{safe_fname}"
                save_dir = Path(config.UPLOAD_FOLDER)
                save_dir.mkdir(parents=True, exist_ok=True)
                full_path = save_dir / unique_name
                paper_file.save(str(full_path))
                file_rel_path = f"uploads/papers/{unique_name}"
                orig_filename = safe_fname
            else:
                flash(f"Invalid file extension. Allowed: {', '.join(config.ALLOWED_EXTENSIONS)}", "error")
                return redirect(url_for("my_profile"))

        db.add_manual_publication(
            current_user.scholar_id, title, year, journal, url_, current_user.email,
            file_path=file_rel_path, original_filename=orig_filename
        )
        flash("Publication added — marked as manually added, and admin has been notified.", "success")
        return redirect(url_for("my_profile"))

    metrics = db.get_metrics_for_scholar(current_user.scholar_id) or {}
    pubs = get_all_publications(current_user.scholar_id)
    entry = {
        "name": current_user.name,
        "cited_by": metrics.get("cited_by", "—"),
        "hindex": metrics.get("hindex", "—"),
    }
    return render_template("my_profile.html", entry=entry, pubs=pubs)


@app.route("/my-profile/delete/<int:pub_id>", methods=["POST"])
@role_required("faculty")
def delete_my_publication(pub_id):
    if not current_user.scholar_id:
        abort(403)

    pub = db.get_manual_publication_by_id(pub_id)
    if not pub:
        pub = db.get_publication_by_id(pub_id)

    if not pub:
        flash("Publication not found.", "error")
        return redirect(url_for("my_profile"))

    author_email = pub.get("added_by_email") or pub.get("manual_added_by")
    if author_email != current_user.email:
        abort(403)

    deleted = db.delete_manual_publication(
        pub_id, current_user.scholar_id,
        user_email=current_user.email,
        is_admin=False
    )
    if deleted:
        flash("Publication removed.", "info")
    else:
        flash("Could not delete publication.", "error")
    return redirect(url_for("my_profile"))


@app.route("/admin/notifications")
@role_required("admin", "hod")
def admin_notifications():
    dept = current_user.department if current_user.role == "hod" else None
    notes = db.get_notifications(department=dept)
    return render_template("notifications.html", notes=notes)


@app.route("/admin/notifications/read/<int:notif_id>", methods=["POST"])
@role_required("admin", "hod")
def mark_notification(notif_id):
    db.mark_notification_read(
        notif_id,
        user_department=current_user.department if current_user.role == "hod" else None,
        is_admin=(current_user.role == "admin")
    )
    return redirect(url_for("admin_notifications"))


@app.route("/analysis/department")
@role_required("admin", "hod")
def department_analysis():
    if current_user.role == "hod":
        departments = [current_user.department]
        locked_dept = current_user.department
    else:
        departments = config.PRIMARY_DEPARTMENTS
        locked_dept = getattr(request, "subdomain_dept", None)
    return render_template("department_analysis.html", departments=departments, locked_dept=locked_dept)


@app.route("/api/analysis/department")
@role_required("admin", "hod")
def api_department_analysis():
    req_dept = request.args.get("dept", "").strip()
    if current_user.role == "hod":
        if req_dept and req_dept.upper() != current_user.department.upper():
            return jsonify({"error": "Forbidden: Department access restricted"}), 403
        dept = current_user.department
    else:
        dept = req_dept

    faculty_list = db.get_all_faculty(department=dept if dept else None)
    
    total_citations = 0
    total_pubs = 0
    total_manual = 0
    citation_leaderboard = []
    inactive_faculty_list = []
    monthly_trend_counter = defaultdict(int)

    for fac in faculty_list:
        sid = fac.get("scholar_id") or ""
        pubs = get_all_publications(sid) if sid else []
        manual_count = sum(1 for p in pubs if p.get("source") == "manual")
        cited_by = fac.get("cited_by") or 0

        total_citations += cited_by
        total_pubs += len(pubs)
        total_manual += manual_count

        active = is_faculty_active(pubs)

        for p in pubs:
            date_added_str = p.get("date_added")
            if date_added_str:
                try:
                    dt = datetime.fromisoformat(str(date_added_str).replace("Z", "+00:00"))
                    m_key = dt.strftime("%b %Y")
                    monthly_trend_counter[m_key] += 1
                except Exception:
                    pass
            elif str(p.get("year", "")).isdigit():
                monthly_trend_counter[str(p["year"])] += 1

        fac_info = {
            "name": fac["name"],
            "scholar_id": sid,
            "department": fac["department"],
            "cited_by": cited_by,
            "pub_count": len(pubs),
            "is_active": active,
            "latest_manual_pub": db.get_latest_manual_pub_date(sid) if sid else None,
        }
        citation_leaderboard.append(fac_info)
        if not active:
            inactive_faculty_list.append(fac_info)

    citation_leaderboard.sort(key=lambda x: x["cited_by"], reverse=True)
    monthly_trend = [{"label": k, "count": v} for k, v in monthly_trend_counter.items()]

    # Department activity calculation across primary departments
    dept_activity = []
    min_score = 101.0

    target_departments = [current_user.department] if current_user.role == "hod" else config.PRIMARY_DEPARTMENTS

    for d_name in target_departments:
        d_faculty = db.get_all_faculty(department=d_name)
        d_total = len(d_faculty)
        d_active = 0
        for f in d_faculty:
            s_id = f.get("scholar_id") or ""
            p_list = get_all_publications(s_id) if s_id else []
            if is_faculty_active(p_list):
                d_active += 1
        score = round((d_active / d_total * 100), 1) if d_total > 0 else 0.0
        if score < min_score:
            min_score = score
        dept_activity.append({
            "department": d_name,
            "total_faculty": d_total,
            "active_faculty": d_active,
            "inactive_faculty": d_total - d_active,
            "activity_score": score,
            "needs_attention": False,
        })

    for da in dept_activity:
        if da["activity_score"] == min_score:
            da["needs_attention"] = True

    return jsonify({
        "department": dept or "All Departments",
        "faculty_count": len(faculty_list),
        "total_citations": total_citations,
        "total_publications": total_pubs,
        "manually_added_publications": total_manual,
        "top_faculty": citation_leaderboard,
        "inactive_faculty": inactive_faculty_list,
        "monthly_trend": monthly_trend,
        "department_activity": dept_activity,
    })


@app.route("/admin/faculty/<scholar_id>")
@role_required("admin", "hod")
def admin_faculty_detail(scholar_id):
    fac = db.get_faculty_by_scholar_id(scholar_id)
    if not fac and scholar_id.isdigit():
        fac = db.get_faculty_by_id(int(scholar_id))

    if not fac:
        flash("Faculty not found.", "error")
        return redirect(url_for("department_analysis"))

    # Server-side department locking for HOD
    if current_user.role == "hod" and fac.get("department", "").upper() != current_user.department.upper():
        abort(403)

    pubs = get_all_publications(scholar_id)
    metrics = db.get_metrics_for_scholar(scholar_id) or {}
    is_active = is_faculty_active(pubs)

    scholar_last_updated = format_last_updated()
    manual_last_raw = db.get_latest_manual_pub_date(scholar_id)
    manual_last_updated = None
    if manual_last_raw:
        try:
            dt = datetime.fromisoformat(manual_last_raw.replace("Z", "+00:00"))
            manual_last_updated = dt.strftime("%d %b %Y, %I:%M %p UTC")
        except Exception:
            manual_last_updated = manual_last_raw

    entry = {
        "cited_by": metrics.get("cited_by", 0),
        "hindex": metrics.get("hindex", 0),
        "affiliation": fac.get("affiliation", "VIT"),
        "interests": [fac.get("interests")] if fac.get("interests") else [],
    }

    faculty_dict = {
        "Name": fac["name"],
        "Department": fac["department"],
        "Scholar ID": fac.get("scholar_id") or "Unavailable",
    }

    return render_template(
        "admin_faculty_detail.html",
        faculty=faculty_dict,
        entry=entry,
        pubs=pubs,
        is_active=is_active,
        scholar_last_updated=scholar_last_updated,
        manual_last_updated=manual_last_updated,
    )


@app.route("/api/department/export-csv")
@role_required("admin", "hod")
def api_department_export_csv():
    import csv
    import io
    from flask import Response

    req_dept = request.args.get("dept", "").strip()
    if current_user.role == "hod":
        if req_dept and req_dept.upper() != current_user.department.upper():
            abort(403)
        dept = current_user.department
    else:
        dept = req_dept

    faculty_list = db.get_all_faculty(department=dept if dept else None)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Faculty Name", "Department", "Scholar ID",
        "Total Publications", "Total Citations", "h-index", "i10-index",
        "6-Month Activity Status", "Scholar Last Fetched", "Latest Manual Addition"
    ])

    scholar_last_fetched = format_last_updated() or "N/A"

    for fac in faculty_list:
        sid = fac.get("scholar_id") or ""
        pubs = get_all_publications(sid) if sid else []
        cited_by = fac.get("cited_by") or 0
        hindex = fac.get("hindex") or 0
        i10index = fac.get("i10index") or 0
        is_act = "Active" if is_faculty_active(pubs) else "Inactive"

        manual_last_raw = db.get_latest_manual_pub_date(sid) if sid else None
        manual_last_formatted = "None"
        if manual_last_raw:
            try:
                dt = datetime.fromisoformat(manual_last_raw.replace("Z", "+00:00"))
                manual_last_formatted = dt.strftime("%d %b %Y, %I:%M %p UTC")
            except Exception:
                manual_last_formatted = manual_last_raw

        writer.writerow([
            fac["name"], fac["department"], sid or "Unavailable",
            len(pubs), cited_by, hindex, i10index,
            is_act, scholar_last_fetched, manual_last_formatted
        ])

    csv_data = output.getvalue()
    filename = f"{(dept or 'all').lower()}_department_faculty_report.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/reports/monthly")
@role_required("admin", "hod")
def monthly_report_page():
    if current_user.role == "hod":
        departments = [current_user.department]
        locked_dept = current_user.department
    else:
        departments = config.PRIMARY_DEPARTMENTS
        locked_dept = getattr(request, "subdomain_dept", None)
    return render_template("monthly_report.html", locked_dept=locked_dept,
                           departments=departments)


@app.route("/api/reports/monthly")
@role_required("admin", "hod")
def api_monthly_report():
    raw_year = request.args.get("year", "").strip()
    raw_month = request.args.get("month", "").strip()
    req_dept = request.args.get("dept", "").strip()
    if current_user.role == "hod":
        if req_dept and req_dept.upper() != current_user.department.upper():
            return jsonify({"error": "Forbidden: Department access restricted"}), 403
        dept = current_user.department
    else:
        dept = req_dept

    if not raw_year or not raw_month:
        return jsonify({"error": "year and month are required"}), 400

    try:
        year, month = int(raw_year), int(raw_month)
    except ValueError:
        return jsonify({"error": "year and month must be valid integers"}), 400

    faculty_list = db.get_all_faculty(department=dept if dept else None)
    results = []

    for fac in faculty_list:
        sid = fac.get("scholar_id") or ""
        pubs = get_all_publications(sid) if sid else []

        for p in pubs:
            date_added = p.get("date_added")
            if not date_added:
                continue
            try:
                dt = datetime.fromisoformat(str(date_added).replace("Z", "+00:00"))
            except Exception:
                continue
            if dt.year == year and dt.month == month:
                results.append({
                    "faculty": fac["name"],
                    "department": fac["department"],
                    "title": p["title"],
                    "pub_year": p.get("year"),
                    "journal": p.get("journal"),
                    "source": p.get("source", "scholar"),
                    "date_added": date_added,
                })

    return jsonify({
        "year": year, "month": month, "department": dept or "All Departments",
        "count": len(results), "publications": results,
    })


# -------------------------------------------------------------
# API ROUTES
# -------------------------------------------------------------
@app.route("/api/faculty/search")
@login_required
def api_faculty_search():
    query = request.args.get("q", "").strip()
    dept_filter = current_user.department if current_user.role == "hod" else None

    if query:
        matches = db.search_faculty(query)
        if dept_filter:
            matches = [m for m in matches if m.get("department", "").upper() == dept_filter.upper()]
    else:
        matches = db.get_all_faculty(department=dept_filter)

    results = []
    for m in matches:
        results.append({
            "Name": m["name"],
            "Department": m["department"],
            "Scholar ID": m.get("scholar_id") or "",
            "id": m["id"],
        })
    return jsonify(results)


@app.route("/api/faculty/all")
@login_required
def api_faculty_all():
    dept_filter = current_user.department if current_user.role == "hod" else None
    faculty = db.get_all_faculty(department=dept_filter)
    results = []
    for f in faculty:
        results.append({
            "Name": f["name"],
            "Department": f["department"],
            "Scholar ID": f.get("scholar_id") or "Not Found",
            "id": f["id"],
        })
    return jsonify(results)


@app.route("/api/faculty/profile")
@login_required
def api_faculty_profile():
    scholar_id = request.args.get("id", "").strip()
    fac_name = request.args.get("name", "").strip()

    fac = None
    if scholar_id and not scholar_id.startswith("NO_ID") and scholar_id not in ("Not Found", "nan", "None", ""):
        fac = db.get_faculty_by_scholar_id(scholar_id)
    if not fac and fac_name:
        matches = db.search_faculty(fac_name)
        if matches:
            fac = matches[0]
    if not fac and scholar_id.isdigit():
        fac = db.get_faculty_by_id(int(scholar_id))

    if not fac:
        return jsonify({"error": "Faculty not found"}), 404

    # Department isolation for HOD
    if current_user.role == "hod" and fac.get("department", "").upper() != current_user.department.upper():
        return jsonify({"error": "Forbidden: Access restricted to assigned department"}), 403

    data = {
        "Name": fac["name"],
        "Department": fac["department"],
        "Scholar ID": fac.get("scholar_id") or "",
        "designation": fac.get("designation") or "",
        "id": fac["id"]
    }

    sid = data["Scholar ID"]
    has_valid_scholar_id = bool(sid and sid not in ("Not Found", "nan", "None", "") and not sid.startswith("NO_ID"))
    data["has_scholar_id"] = has_valid_scholar_id

    if not has_valid_scholar_id:
        data["scholar_status_message"] = (
            "Google Scholar profile is not linked for this faculty member. "
            "Citation metrics and publication data are currently unavailable."
        )
        data["scholar_status_badge"] = "Scholar ID Unavailable"
        data["data_source"] = "unavailable"
        data["cited_by"] = "—"
        data["cited_by_5y"] = "—"
        data["hindex"] = "—"
        data["hindex5y"] = "—"
        data["i10index"] = "—"
        data["i10index5y"] = "—"
        data["affiliation"] = fac.get("affiliation") or "Vidyalankar Institute of Technology"
        data["interests"] = []
        data["image_url"] = DEFAULT_IMAGE
        data["scholar_url"] = "#"
        data["publications"] = []
        return jsonify(data)

    data["image_url"] = get_profile_image_url(sid)
    data["scholar_url"] = f"https://scholar.google.com/citations?user={sid}"

    # Read cached metrics and publications from database
    metrics = db.get_metrics_for_scholar(sid)
    all_pubs = get_all_publications(sid)

    if metrics and metrics.get("last_fetch_status") == "success":
        data["cited_by"] = metrics.get("cited_by", 0)
        data["cited_by_5y"] = metrics.get("cited_by_5y", 0)
        data["hindex"] = metrics.get("hindex", 0)
        data["hindex5y"] = metrics.get("hindex5y", 0)
        data["i10index"] = metrics.get("i10index", 0)
        data["i10index5y"] = metrics.get("i10index5y", 0)
        data["affiliation"] = fac.get("affiliation") or "Vidyalankar Institute of Technology"
        data["interests"] = [fac.get("interests")] if fac.get("interests") else []
        data["data_source"] = "scholar"
    else:
        # Fallback to local JSON if DB is not yet populated for this author
        scholar_data = load_scholar_data()
        sd = scholar_data.get(sid, {})
        if sd and not sd.get("error"):
            data["cited_by"] = sd.get("cited_by", 0)
            data["cited_by_5y"] = sd.get("cited_by_5y", 0)
            data["hindex"] = sd.get("hindex", 0)
            data["hindex5y"] = sd.get("hindex5y", 0)
            data["i10index"] = sd.get("i10index", 0)
            data["i10index5y"] = sd.get("i10index5y", 0)
            data["affiliation"] = sd.get("affiliation", "VIT")
            data["interests"] = sd.get("interests", [])
            data["data_source"] = "scholar"
        else:
            # On-demand fetch
            sd = fetch_and_cache_single(sid, fac["name"])
            if not sd.get("error"):
                data["cited_by"] = sd.get("cited_by", 0)
                data["cited_by_5y"] = sd.get("cited_by_5y", 0)
                data["hindex"] = sd.get("hindex", 0)
                data["hindex5y"] = sd.get("hindex5y", 0)
                data["i10index"] = sd.get("i10index", 0)
                data["i10index5y"] = sd.get("i10index5y", 0)
                data["affiliation"] = sd.get("affiliation", "VIT")
                data["interests"] = sd.get("interests", [])
                data["data_source"] = "scholar"
                all_pubs = get_all_publications(sid, sd)
            else:
                data["cited_by"] = "—"
                data["cited_by_5y"] = "—"
                data["hindex"] = "—"
                data["hindex5y"] = "—"
                data["i10index"] = "—"
                data["i10index5y"] = "—"
                data["data_source"] = "pending"

    data["publications"] = all_pubs
    return jsonify(data)


@app.route("/api/department/faculty")
@login_required
def api_department_faculty():
    if current_user.role in ("student", "faculty"):
        abort(403)

    req_dept = request.args.get("dept", "").strip()
    if current_user.role == "hod":
        if req_dept and req_dept.upper() != current_user.department.upper():
            return jsonify({"error": "Forbidden: Department access restricted"}), 403
        dept = current_user.department
    else:
        dept = req_dept

    faculty_list = db.get_all_faculty(department=dept if dept else None)
    results = []

    for fac in faculty_list:
        sid = fac.get("scholar_id") or ""
        has_id = bool(sid and sid not in ("Not Found", "nan", "None", "") and not sid.startswith("NO_ID"))
        all_pubs = get_all_publications(sid) if sid else []

        entry = {
            "Name": fac["name"],
            "Department": fac["department"],
            "Scholar ID": sid if has_id else "Not Found",
            "id": fac["id"],
            "has_scholar_id": has_id,
            "pub_count": len(all_pubs),
            "cited_by": fac.get("cited_by") if has_id else "—",
            "hindex": fac.get("hindex") if has_id else "—",
            "i10index": fac.get("i10index") if has_id else "—",
            "data_source": "scholar" if has_id else "unavailable",
        }
        results.append(entry)

    return jsonify(results)


@app.route("/api/department/metrics")
@login_required
def api_department_metrics():
    """Returns dynamic department research metrics computed live from database."""
    if current_user.role in ("student", "faculty"):
        abort(403)
    metrics = db.get_department_aggregated_metrics()
    if current_user.role == "hod":
        metrics = [m for m in metrics if m["Department"].upper() == current_user.department.upper()]
    return jsonify(metrics)


@app.route("/publication/<int:pub_id>/download")
@login_required
def download_publication_paper(pub_id):
    """
    Secure paper download endpoint.
    RBAC:
      Student: All permitted faculty papers
      Faculty: Own and other faculty papers
      HOD: Only papers belonging to assigned department
      Admin: All papers
    """
    pub = db.get_publication_by_id(pub_id)
    if not pub:
        pub = db.get_manual_publication_by_id(pub_id)
    if not pub:
        abort(404)

    # Resolve author department
    author_fac = None
    sid = pub.get("scholar_id")
    if sid:
        author_fac = db.get_faculty_by_scholar_id(sid)

    author_dept = None
    if author_fac:
        author_dept = author_fac.get("department")
    elif pub.get("manual_added_by"):
        u = db.get_user_by_email(pub["manual_added_by"])
        if u:
            author_dept = u.get("department")

    # Access control
    if current_user.role == "hod":
        if not author_dept or author_dept.upper() != current_user.department.upper():
            abort(403)
    elif current_user.role not in ("student", "faculty", "admin"):
        abort(403)

    file_rel_path = pub.get("file_path")
    if not file_rel_path:
        ext_url = pub.get("url")
        if ext_url and (ext_url.startswith("http://") or ext_url.startswith("https://")):
            return redirect(ext_url)
        abort(404)

    # Path traversal validation
    upload_dir = Path(config.UPLOAD_FOLDER).resolve()
    target_path = (BASE / file_rel_path).resolve()

    try:
        target_path.relative_to(upload_dir)
    except ValueError:
        # Directory traversal attempt detected
        abort(403)

    if not target_path.is_file():
        abort(404)

    download_name = pub.get("original_filename") or target_path.name
    download_name = os.path.basename(download_name)

    return send_file(
        str(target_path),
        as_attachment=True,
        download_name=download_name
    )


@app.route("/api/cron/refresh", methods=["GET", "POST"])
def api_cron_refresh():
    """Scheduled refresh endpoint for Vercel Cron or Admin triggers."""
    auth_header = request.headers.get("Authorization", "")
    secret_header = request.headers.get("X-Cron-Secret", "")
    expected = config.CRON_SECRET
    if not expected:
        return jsonify({"error": "Cron secret is not configured"}), 503

    is_authorized = False
    if auth_header and auth_header.startswith("Bearer "):
        is_authorized = (auth_header.split("Bearer ")[1].strip() == expected)
    elif secret_header:
        is_authorized = (secret_header.strip() == expected)
    elif current_user.is_authenticated and current_user.role == "admin":
        is_authorized = True

    if not is_authorized:
        return jsonify({"error": "Unauthorized"}), 401

    limit = request.args.get("limit", 5, type=int)
    dept = request.args.get("dept", "").strip() or None
    force = request.args.get("force", "false").lower() == "true"

    from fetch_data import refresh_batch
    res = refresh_batch(limit=limit, department=dept, max_age_days=7, force_refetch=force)
    return jsonify(res)


@app.route("/api/status")
@login_required
def api_status():
    """Shows how many faculty have been fetched and system status."""
    all_fac = db.get_all_faculty()
    total = len(all_fac)
    fetched = sum(1 for f in all_fac if f.get("last_fetch_status") == "success")
    errors = sum(1 for f in all_fac if f.get("last_fetch_status") == "error")
    no_id = sum(1 for f in all_fac if not f.get("scholar_id") or f.get("last_fetch_status") == "no_id")

    return jsonify({
        "total_faculty": total,
        "fetched": fetched,
        "errors": errors,
        "no_id": no_id,
        "remaining": total - (fetched + no_id),
        "ready": fetched > 0,
        "last_updated": format_last_updated(),
    })


if __name__ == "__main__":
    app.run(debug=config.DEBUG)
