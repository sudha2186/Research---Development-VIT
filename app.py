from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_login import login_required, current_user
import pandas as pd
import json
import requests
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import threading
from live_fetch import fetch_author_live

import config
import db
from auth import auth_bp, login_manager, role_required

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.register_blueprint(auth_bp)
login_manager.init_app(app)

BASE = Path(__file__).parent
faculty_df = pd.read_excel(BASE / "faculty_metrics.xlsx")
dept_df = pd.read_excel(BASE / "department_metrics.xlsx")
SCHOLAR_DATA_FILE = BASE / "scholar_data.json"

CLOUDINARY_BASE = "https://res.cloudinary.com/dwuhswk2w/image/upload/v1751302313/"
DEFAULT_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/9/99/Sample_User_Icon.png"

db.init_db()


def load_scholar_data():
    if SCHOLAR_DATA_FILE.exists():
        with open(SCHOLAR_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_profile_image_url(scholar_id):
    url = f"{CLOUDINARY_BASE}{scholar_id}.jpg"
    try:
        r = requests.head(url, timeout=4)
        return url if r.status_code == 200 else DEFAULT_IMAGE
    except Exception:
        return DEFAULT_IMAGE

_fetch_lock = threading.Lock()

def fetch_and_cache_single(scholar_id, name):
    """If a faculty member hasn't been fetched yet, fetch them live right now
    and cache the result into scholar_data.json for next time."""
    with _fetch_lock:
        scholar_data = load_scholar_data()
        existing = scholar_data.get(scholar_id)
        if existing and not existing.get("error"):
            return existing  # someone else's request already fetched it

        try:
            result = fetch_author_live(scholar_id, name, existing)
            scholar_data[scholar_id] = result
            with open(SCHOLAR_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(scholar_data, f, indent=2, ensure_ascii=False)
            db.set_meta("scholar_last_updated", datetime.now(timezone.utc).isoformat())
            return result
        except Exception as e:
            return {"name": name, "error": True, "error_message": str(e), "scholar_id": scholar_id}

def get_all_publications(scholar_id, scholar_entry):
    """Merge Scholar-sourced publications with manually-added ones."""
    pubs = list(scholar_entry.get("publications", [])) if scholar_entry else []
    for p in pubs:
        p.setdefault("source", "scholar")

    manual = db.get_manual_publications(scholar_id)
    for m in manual:
        pubs.append({
            "title": m["title"],
            "year": m["year"],
            "journal": m["journal"],
            "citations": m["citations"],
            "url": m["url"],
            "date_added": m["added_at"],
            "source": "manual",
            "manual_id": m["id"],
        })

    pubs.sort(key=lambda x: str(x.get("year", "")), reverse=True)
    return pubs


def is_faculty_active(pubs):
    """
    A faculty member is active if they have at least one publication (Scholar or manual)
    with a date_added or publication year in the last 6 months (180 days).
    """
    if not pubs:
        return False

    now = datetime.now(timezone.utc)
    six_months_ago = now - timedelta(days=180)

    for p in pubs:
        date_added_str = p.get("date_added")
        if date_added_str:
            try:
                dt = datetime.fromisoformat(date_added_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= six_months_ago:
                    return True
            except Exception:
                pass

        year_str = str(p.get("year", ""))
        if year_str.isdigit():
            pub_year = int(year_str)
            if pub_year >= now.year:
                return True

    return False


def format_last_updated():
    raw = db.get_meta("scholar_last_updated")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%d %b %Y, %I:%M %p UTC")
    except Exception:
        return raw


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
    }


# ---------- PAGE ROUTES ----------

@app.route("/")
@login_required
def home():
    return render_template("home.html")


@app.route("/faculty-profiles")
@login_required
def faculty_profiles():
    return render_template("faculty_profiles.html")


@app.route("/department")
@login_required
def department():
    departments = faculty_df["Department"].unique().tolist()
    return render_template("department.html", departments=departments)


@app.route("/impact-metrics")
@login_required
def impact_metrics():
    return render_template("impact_metrics.html")


@app.route("/my-profile", methods=["GET", "POST"])
@role_required("faculty")
def my_profile():
    if not current_user.scholar_id:
        flash("Your account isn't linked to a Scholar ID yet. Contact admin.", "error")
        return redirect(url_for("home"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        year = request.form.get("year", "").strip()
        journal = request.form.get("journal", "").strip()
        url_ = request.form.get("url", "").strip()

        if not title:
            flash("Title is required.", "error")
        else:
            db.add_manual_publication(current_user.scholar_id, title, year, journal, url_, current_user.email)
            flash("Publication added — marked as manually added, and admin has been notified.", "success")
        return redirect(url_for("my_profile"))

    scholar_data = load_scholar_data()
    entry = scholar_data.get(current_user.scholar_id, {})
    pubs = get_all_publications(current_user.scholar_id, entry)
    return render_template("my_profile.html", entry=entry, pubs=pubs)


@app.route("/my-profile/delete/<int:pub_id>", methods=["POST"])
@role_required("faculty")
def delete_my_publication(pub_id):
    if not current_user.scholar_id:
        flash("Not authorized.", "error")
        return redirect(url_for("home"))
    db.delete_manual_publication(pub_id, current_user.scholar_id)
    flash("Publication removed.", "info")
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
    db.mark_notification_read(notif_id)
    return redirect(url_for("admin_notifications"))


@app.route("/analysis/department")
@role_required("admin", "hod")
def department_analysis():
    departments = faculty_df["Department"].unique().tolist()
    # HOD is locked to their own department
    locked_dept = current_user.department if current_user.role == "hod" else None
    return render_template("department_analysis.html", departments=departments, locked_dept=locked_dept)


@app.route("/api/analysis/department")
@role_required("admin", "hod")
def api_department_analysis():
    dept = request.args.get("dept", "").strip()
    if current_user.role == "hod":
        dept = current_user.department  # HOD cannot view other departments

    df = faculty_df[faculty_df["Department"] == dept] if dept else faculty_df
    scholar_data = load_scholar_data()

    total_citations = total_pubs = total_manual = 0
    citation_leaderboard = []
    inactive_faculty_list = []
    monthly_trend_counter = defaultdict(int)

    for _, row in df.iterrows():
        sid = str(row["Scholar ID"])
        entry = scholar_data.get(sid, {})
        pubs = get_all_publications(sid, entry) if not entry.get("error") else db.get_manual_publications(sid)
        manual_count = sum(1 for p in pubs if p.get("source") == "manual")
        cited_by = entry.get("cited_by", 0) if not entry.get("error") else 0

        total_citations += cited_by
        total_pubs += len(pubs)
        total_manual += manual_count

        active = is_faculty_active(pubs)

        # Monthly trend calculation
        for p in pubs:
            date_added_str = p.get("date_added")
            if date_added_str:
                try:
                    dt = datetime.fromisoformat(date_added_str.replace("Z", "+00:00"))
                    m_key = dt.strftime("%b %Y")
                    monthly_trend_counter[m_key] += 1
                except Exception:
                    pass
            elif str(p.get("year", "")).isdigit():
                monthly_trend_counter[str(p["year"])] += 1

        fac_info = {
            "name": row["Name"],
            "scholar_id": sid,
            "department": row["Department"],
            "cited_by": cited_by,
            "pub_count": len(pubs),
            "is_active": active,
            "latest_manual_pub": db.get_latest_manual_pub_date(sid),
        }
        citation_leaderboard.append(fac_info)
        if not active:
            inactive_faculty_list.append(fac_info)

    citation_leaderboard.sort(key=lambda x: x["cited_by"], reverse=True)

    # Convert trend counter to sorted list
    monthly_trend = [{"label": k, "count": v} for k, v in monthly_trend_counter.items()]

    # Department activity calculation across all 5 departments
    all_depts = sorted(faculty_df["Department"].unique().tolist())
    dept_activity = []
    min_score = 101.0

    for d_name in all_depts:
        d_df = faculty_df[faculty_df["Department"] == d_name]
        d_total = len(d_df)
        d_active = 0
        for _, r in d_df.iterrows():
            s_id = str(r["Scholar ID"])
            s_data = scholar_data.get(s_id, {})
            p_list = get_all_publications(s_id, s_data) if not s_data.get("error") else db.get_manual_publications(s_id)
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
        "faculty_count": len(df),
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
    row = faculty_df[faculty_df["Scholar ID"].astype(str) == str(scholar_id)]
    if row.empty:
        flash("Faculty not found.", "error")
        return redirect(url_for("department_analysis"))

    faculty = row.iloc[0].to_dict()

    # Enforce server-side locking for HOD
    if current_user.role == "hod" and faculty.get("Department") != current_user.department:
        flash("Access denied: You can only view faculty members in your department.", "error")
        return redirect(url_for("home"))

    scholar_data = load_scholar_data()
    sd = scholar_data.get(scholar_id, {})
    all_pubs = get_all_publications(scholar_id, sd)
    is_active = is_faculty_active(all_pubs)

    scholar_last_updated = format_last_updated()
    manual_last_raw = db.get_latest_manual_pub_date(scholar_id)
    manual_last_updated = None
    if manual_last_raw:
        try:
            dt = datetime.fromisoformat(manual_last_raw.replace("Z", "+00:00"))
            manual_last_updated = dt.strftime("%d %b %Y, %I:%M %p UTC")
        except Exception:
            manual_last_updated = manual_last_raw

    return render_template(
        "admin_faculty_detail.html",
        faculty=faculty,
        entry=sd,
        pubs=all_pubs,
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

    dept = request.args.get("dept", "").strip()
    if current_user.role == "hod":
        dept = current_user.department  # HOD is locked to their department

    df = faculty_df[faculty_df["Department"] == dept] if dept else faculty_df
    scholar_data = load_scholar_data()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Faculty Name", "Department", "Scholar ID",
        "Total Publications", "Total Citations", "h-index", "i10-index",
        "6-Month Activity Status", "Scholar Last Fetched", "Latest Manual Addition"
    ])

    scholar_last_fetched = format_last_updated() or "N/A"

    for _, row in df.iterrows():
        sid = str(row["Scholar ID"])
        sd = scholar_data.get(sid, {})
        pubs = get_all_publications(sid, sd) if not sd.get("error") else db.get_manual_publications(sid)

        cited_by = sd.get("cited_by", 0) if not sd.get("error") else 0
        hindex = sd.get("hindex", 0) if not sd.get("error") else 0
        i10index = sd.get("i10index", 0) if not sd.get("error") else 0
        is_act = "Active" if is_faculty_active(pubs) else "Inactive"

        manual_last_raw = db.get_latest_manual_pub_date(sid)
        manual_last_formatted = "None"
        if manual_last_raw:
            try:
                dt = datetime.fromisoformat(manual_last_raw.replace("Z", "+00:00"))
                manual_last_formatted = dt.strftime("%d %b %Y, %I:%M %p UTC")
            except Exception:
                manual_last_formatted = manual_last_raw

        writer.writerow([
            row["Name"], row["Department"], sid,
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
    locked_dept = current_user.department if current_user.role == "hod" else None
    return render_template("monthly_report.html", locked_dept=locked_dept,
                            departments=faculty_df["Department"].unique().tolist())


@app.route("/api/reports/monthly")
@role_required("admin", "hod")
def api_monthly_report():
    if current_user.role not in ("admin", "hod"):
        return jsonify({"error": "Forbidden"}), 403

    year = request.args.get("year", "").strip()
    month = request.args.get("month", "").strip()  # "1"-"12"
    dept = request.args.get("dept", "").strip()
    if current_user.role == "hod":
        dept = current_user.department

    if not year or not month:
        return jsonify({"error": "year and month are required"}), 400

    year, month = int(year), int(month)
    scholar_data = load_scholar_data()
    df = faculty_df[faculty_df["Department"] == dept] if dept else faculty_df

    results = []
    for _, row in df.iterrows():
        sid = str(row["Scholar ID"])
        entry = scholar_data.get(sid, {})
        pubs = get_all_publications(sid, entry) if not entry.get("error") else db.get_manual_publications(sid)

        for p in pubs:
            date_added = p.get("date_added")
            if not date_added:
                continue
            try:
                dt = datetime.fromisoformat(date_added)
            except Exception:
                continue
            if dt.year == year and dt.month == month:
                results.append({
                    "faculty": row["Name"],
                    "department": row["Department"],
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


# ---------- API ROUTES ----------

@app.route("/api/faculty/search")
@login_required
def api_faculty_search():
    query = request.args.get("q", "").strip().lower()
    df = faculty_df[faculty_df["Name"].str.lower().str.contains(query, na=False)] if query else faculty_df
    return jsonify(df.to_dict(orient="records"))


@app.route("/api/faculty/all")
@login_required
def api_faculty_all():
    return jsonify(faculty_df.to_dict(orient="records"))


@app.route("/api/faculty/profile")
@login_required
def api_faculty_profile():
    scholar_id = request.args.get("id", "").strip()
    row = faculty_df[faculty_df["Scholar ID"] == scholar_id]
    if row.empty:
        return jsonify({"error": "Not found"}), 404

    scholar_data = load_scholar_data()
    data = row.iloc[0].to_dict()
    data["image_url"] = get_profile_image_url(scholar_id)
    data["scholar_url"] = f"https://scholar.google.com/citations?user={scholar_id}"

    sd = scholar_data.get(scholar_id, {})
    if not sd or sd.get("error") or sd.get("no_id"):
        # Not fetched yet — fetch this one faculty member live, right now
        sd = fetch_and_cache_single(scholar_id, data.get("Name", ""))
    all_pubs = get_all_publications(scholar_id, sd)

    if not sd.get("error") and not sd.get("no_id"):
        data["cited_by"] = sd.get("cited_by", "N/A")
        data["cited_by_5y"] = sd.get("cited_by_5y", "N/A")
        data["hindex"] = sd.get("hindex", "N/A")
        data["hindex5y"] = sd.get("hindex5y", "N/A")
        data["i10index"] = sd.get("i10index", "N/A")
        data["i10index5y"] = sd.get("i10index5y", "N/A")
        data["affiliation"] = sd.get("affiliation", "VIT")
        data["interests"] = sd.get("interests", [])
        data["data_source"] = "scholar"
        data["freshly_fetched"] = sd.get("fetched_live", False)  
    else:
        data["data_source"] = "excel" if all_pubs else "excel_only"

    data["publications"] = all_pubs
    return jsonify(data)


@app.route("/api/department/faculty")
@login_required
def api_department_faculty():
    dept = request.args.get("dept", "").strip()
    df = faculty_df[faculty_df["Department"] == dept] if dept else faculty_df
    scholar_data = load_scholar_data()

    results = []
    for _, row in df.iterrows():
        entry = row.to_dict()
        sid = str(entry.get("Scholar ID", ""))
        sd = scholar_data.get(sid, {})
        all_pubs = get_all_publications(sid, sd)

        if not sd.get("error") and not sd.get("no_id"):
            entry["cited_by"] = sd.get("cited_by", "N/A")
            entry["hindex"] = sd.get("hindex", "N/A")
            entry["i10index"] = sd.get("i10index", "N/A")
            entry["data_source"] = "scholar"
        else:
            entry["data_source"] = "excel" if all_pubs else "excel_only"

        entry["pub_count"] = len(all_pubs)
        results.append(entry)

    return jsonify(results)


@app.route("/api/department/metrics")
@login_required
def api_department_metrics():
    return jsonify(dept_df.to_dict(orient="records"))


@app.route("/api/status")
@login_required
def api_status():
    """Shows how many faculty have been fetched so far."""
    scholar_data = load_scholar_data()
    total = len(faculty_df)
    fetched = sum(1 for v in scholar_data.values() if not v.get("error") and not v.get("no_id"))
    errors = sum(1 for v in scholar_data.values() if v.get("error"))
    no_id = sum(1 for v in scholar_data.values() if v.get("no_id"))
    return jsonify({
        "total_faculty": total,
        "fetched": fetched,
        "errors": errors,
        "no_id": no_id,
        "remaining": total - len(scholar_data),
        "ready": fetched > 0,
        "last_updated": format_last_updated(),
    })


if __name__ == "__main__":
    app.run(debug=config.DEBUG)
