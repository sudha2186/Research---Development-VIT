"""
Comprehensive Automated Test Suite for VIT Research Portal - Phase 2 Verification & Correction.
Tests:
1. Faculty Data & Department Integration (CMPN, FE, IT, EXTC, Biomedical)
2. Graceful Scholar ID Unavailable Handling
3. Demo Accounts Authentication (Student, Faculty, HOD, Admin)
4. Unauthenticated Access Protection & Public Registration Disabled
5. Student Role: Faculty search across all 5 departments, view profiles & papers, download papers, read-only (403 on metrics/admin/edit)
6. Faculty Role: Own & other faculty profiles and papers, paper submission with file, own deletion, forbidden from other's deletion, 403 on metrics/admin
7. HOD Role: Assigned department isolation (CMPN), 403 on cross-department parameter manipulation, department scoped downloads
8. Admin Role: Full system-wide access across all 5 departments
9. Paper Download Functionality & Path Traversal Security
10. Role-Based Navigation Menu Rendering
11. Dynamic Metrics & Deduplication
12. HOD Subdomain Routing & Cron Endpoint Authorization
"""
import sys
import unittest
import io
import os
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import check_password_hash

sys.stdout.reconfigure(encoding='utf-8')

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import config
import db
from app import app


class TestPhase2Verification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        cls.client = app.test_client()

        # Mock network scraping calls so tests run fast, offline, and reliably
        cls.patcher = patch("app.fetch_and_cache_single", return_value={
            "name": "Mock Faculty", "cited_by": 10, "hindex": 2, "i10index": 1, "publications": []
        })
        cls.patcher.start()

        db.init_db()

        # Seed test stored paper file
        sample_dir = Path(config.UPLOAD_FOLDER)
        sample_dir.mkdir(parents=True, exist_ok=True)
        cls.test_file_path = sample_dir / "test_sample_paper.pdf"
        with open(cls.test_file_path, "wb") as f:
            f.write(b"%PDF-1.4 test research paper content for VIT portal")

        # Insert a CMPN publication with a stored file
        cls.cmpn_pub_id = db.upsert_publication(
            scholar_id="dLBhK18AAAAJ",  # Demo Faculty (CMPN)
            title="Automated Deep Learning for Medical Imaging",
            year="2026",
            journal="IEEE Transactions on Medical Imaging",
            citations=12,
            source="manual",
            manual_added_by="faculty@vit.edu.in",
            file_path="uploads/papers/test_sample_paper.pdf",
            original_filename="Automated_Deep_Learning_Paper.pdf"
        )

        # Insert an IT publication with a stored file (for cross-department HOD test)
        it_fac = db.get_all_faculty(department="IT")
        it_sid = it_fac[0]["scholar_id"] if it_fac else "test_it_sid"
        cls.it_pub_id = db.upsert_publication(
            scholar_id=it_sid,
            title="Cloud Security Protocols in Distributed Systems",
            year="2025",
            journal="ACM Computing Surveys",
            citations=8,
            source="scholar",
            file_path="uploads/papers/test_sample_paper.pdf",
            original_filename="Cloud_Security_Paper.pdf"
        )

        # Insert a publication with external URL only (no file)
        cls.ext_url_pub_id = db.upsert_publication(
            scholar_id="dLBhK18AAAAJ",
            title="External Paper on Distributed AI",
            year="2024",
            journal="Springer LNCS",
            citations=3,
            url="https://doi.org/10.1007/sample-paper",
            source="scholar"
        )

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()

    # -------------------------------------------------------------
    # 1. FACULTY DATA INTEGRATION & DEPARTMENTS
    # -------------------------------------------------------------
    def test_01_five_primary_departments_exist(self):
        """Verify the 5 primary departments are supported and have faculty."""
        all_fac = db.get_all_faculty()
        dept_counts = {}
        for f in all_fac:
            d = f["department"]
            dept_counts[d] = dept_counts.get(d, 0) + 1

        print("\n[TEST 01] Department counts in DB:", dept_counts)
        for dept in ["CMPN", "FE", "IT", "EXTC", "Biomedical"]:
            self.assertIn(dept, dept_counts, f"Primary department {dept} must exist in database")
            self.assertGreater(dept_counts[dept], 0, f"Department {dept} must have faculty members")

    def test_02_biomedical_faculty_preserved(self):
        """Verify Biomedical faculty are preserved from faculty_metrics.xlsx."""
        biomed_fac = db.get_all_faculty(department="Biomedical")
        self.assertEqual(len(biomed_fac), 6, "All 6 Biomedical faculty must be preserved")
        names = [f["name"] for f in biomed_fac]
        self.assertIn("Dr. Gajanan Nagare", names)
        self.assertIn("Geetha Narayanan Thekkedath", names)
        print("[TEST 02] Verified 6 Biomedical faculty preserved:", names[:3], "...")

    def test_03_first_year_engineering_imported(self):
        """Verify FE (First Year Engineering) faculty are imported from faculty_list."""
        fe_fac = db.get_all_faculty(department="FE")
        self.assertEqual(len(fe_fac), 24, "All 24 FE faculty must be imported")
        print("[TEST 03] Verified 24 FE faculty imported successfully.")

    def test_04_faculty_without_scholar_id_handling(self):
        """Verify faculty without Scholar IDs remain searchable with professional unavailable status."""
        all_fac = db.get_all_faculty()
        no_sid_fac = [f for f in all_fac if not f.get("scholar_id") or f["scholar_id"].startswith("NO_ID")]
        self.assertGreater(len(no_sid_fac), 0, "There should be faculty without Scholar IDs")

        sample = no_sid_fac[0]
        search_res = db.search_faculty(sample["name"])
        self.assertTrue(len(search_res) > 0, "Faculty without Scholar ID must remain searchable")

        fresh_client = app.test_client()
        fresh_client.post("/login", data={"email": "student@vit.edu.in", "password": "Student@123"})
        res = fresh_client.get(f"/api/faculty/profile?id={sample['id']}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get("has_scholar_id"), "has_scholar_id must be false")
        self.assertEqual(data.get("scholar_status_badge"), "Scholar ID Unavailable")
        self.assertIn("not linked", data.get("scholar_status_message", ""))
        self.assertEqual(data.get("cited_by"), "—")
        print(f"[TEST 04] Verified 'Scholar ID Unavailable' profile response for '{sample['name']}'.")

    # -------------------------------------------------------------
    # 2. DEMO ACCOUNTS & AUTHENTICATION
    # -------------------------------------------------------------
    def test_05_demo_accounts_credentials(self):
        """Verify the 4 required demonstration credentials authenticate successfully."""
        demo_logins = [
            ("student@vit.edu.in", "Student@123", "student"),
            ("faculty@vit.edu.in", "Faculty@123", "faculty"),
            ("hod@vit.edu.in", "HOD@123", "hod"),
            ("admin@vit.edu.in", "Admin@123", "admin"),
        ]

        for email, password, expected_role in demo_logins:
            user = db.get_user_by_email(email)
            self.assertIsNotNone(user, f"User {email} must exist in database")
            self.assertEqual(user["role"], expected_role, f"User {email} must have role {expected_role}")
            self.assertTrue(check_password_hash(user["password_hash"], password), f"Password hash check failed for {email}")

            fresh_client = app.test_client()
            res = fresh_client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
            self.assertEqual(res.status_code, 302, f"Login for {email} must redirect on success")
            print(f"[TEST 05] Verified login and password check for {email} ({expected_role}).")

    def test_06_unauthenticated_access_blocked(self):
        """Verify unauthenticated users cannot access protected views or APIs."""
        protected_routes = [
            "/",
            "/faculty-profiles",
            "/department",
            "/impact-metrics",
            "/analysis/department",
            "/reports/monthly",
            "/my-profile",
            "/admin/notifications",
            "/api/faculty/all",
            "/api/faculty/search",
            "/api/department/faculty?dept=CMPN",
            "/api/department/metrics"
        ]

        fresh_client = app.test_client()
        for r in protected_routes:
            res = fresh_client.get(r, follow_redirects=False)
            self.assertEqual(res.status_code, 302, f"Unauthenticated access to {r} must redirect to login")
            self.assertIn("/login", res.location, f"Redirect target for {r} must be /login")
        print("[TEST 06] Verified unauthenticated access blocked on all 12 protected endpoints.")

    def test_07_public_registration_disabled(self):
        """Verify public self-registration is completely disabled."""
        fresh_client = app.test_client()
        res_get = fresh_client.get("/register", follow_redirects=False)
        self.assertEqual(res_get.status_code, 302)
        self.assertIn("/login", res_get.location)

        res_post = fresh_client.post("/register", data={
            "name": "Attacker",
            "email": "attacker@vit.edu.in",
            "password": "Password123",
            "role": "admin"
        }, follow_redirects=False)
        self.assertEqual(res_post.status_code, 302)
        self.assertIn("/login", res_post.location)

        attacker = db.get_user_by_email("attacker@vit.edu.in")
        self.assertIsNone(attacker, "Public registration must not create accounts in the database")
        print("[TEST 07] Verified public self-registration is disabled and blocked.")

    # -------------------------------------------------------------
    # 3. STUDENT ROLE: PERMISSIONS & RESTRICTIONS
    # -------------------------------------------------------------
    def test_08_student_role_permissions_and_restrictions(self):
        """
        Verify Student role:
        - Can search faculty across all 5 departments
        - Can view faculty profiles across all 5 departments
        - Can view research papers and download available papers
        - Strictly read-only: cannot create, edit, delete publications
        - Cannot access Department Metrics, Impact Metrics, HOD or Admin routes (HTTP 403)
        """
        c = app.test_client()
        c.post("/login", data={"email": "student@vit.edu.in", "password": "Student@123"})

        # Can search across all 5 departments
        for dept in ["CMPN", "FE", "IT", "EXTC", "Biomedical"]:
            fac = db.get_all_faculty(department=dept)[0]
            search_res = c.get(f"/api/faculty/search?q={fac['name'][:4]}")
            self.assertEqual(search_res.status_code, 200)
            data = search_res.get_json()
            self.assertTrue(any(f["Department"] == dept for f in data), f"Student must find faculty in {dept}")

        # Can view faculty profiles across departments
        for dept in ["CMPN", "FE", "IT", "EXTC", "Biomedical"]:
            fac = db.get_all_faculty(department=dept)[0]
            prof_res = c.get(f"/api/faculty/profile?id={fac['id']}")
            self.assertEqual(prof_res.status_code, 200, f"Student must view profile for {dept} faculty")

        # Can download available research papers
        dl_res = c.get(f"/publication/{self.cmpn_pub_id}/download")
        self.assertEqual(dl_res.status_code, 200, "Student must be able to download available papers")
        dl_res.close()

        # RESTRICTIONS: Cannot access Department Metrics / Impact Metrics
        self.assertEqual(c.get("/department").status_code, 403, "Student must get 403 on /department")
        self.assertEqual(c.get("/impact-metrics").status_code, 403, "Student must get 403 on /impact-metrics")
        self.assertEqual(c.get("/api/department/metrics").status_code, 403, "Student must get 403 on /api/department/metrics")
        self.assertEqual(c.get("/api/department/faculty?dept=CMPN").status_code, 403, "Student must get 403 on /api/department/faculty")

        # RESTRICTIONS: Cannot access HOD / Admin routes
        self.assertEqual(c.get("/analysis/department").status_code, 403, "Student must get 403 on /analysis/department")
        self.assertEqual(c.get("/reports/monthly").status_code, 403, "Student must get 403 on /reports/monthly")
        self.assertEqual(c.get("/admin/notifications").status_code, 403, "Student must get 403 on /admin/notifications")
        self.assertEqual(c.get("/my-profile").status_code, 403, "Student must get 403 on /my-profile")

        # RESTRICTIONS: Read-only, cannot submit or delete publications
        post_sub = c.post("/my-profile", data={"title": "Unauthorized Paper"})
        self.assertEqual(post_sub.status_code, 403, "Student POST to /my-profile must return 403")
        del_sub = c.post(f"/my-profile/delete/{self.cmpn_pub_id}")
        self.assertEqual(del_sub.status_code, 403, "Student POST to delete publication must return 403")

        print("[TEST 08] Verified Student role: Full search/view/download across 5 depts, read-only, 403 on metrics/admin/edit.")

    # -------------------------------------------------------------
    # 4. FACULTY ROLE: PERMISSIONS & RESTRICTIONS
    # -------------------------------------------------------------
    def test_09_faculty_role_permissions_and_restrictions(self):
        """
        Verify Faculty role:
        - View own profile and research papers
        - View other faculty members' profiles and papers
        - Download own and other faculty members' papers
        - Submit own publications (with optional file)
        - Delete own publications
        - CANNOT delete another faculty member's publication (HTTP 403)
        - CANNOT access Department Metrics or Impact Metrics (HTTP 403)
        - CANNOT access HOD or Admin routes (HTTP 403)
        """
        c = app.test_client()
        c.post("/login", data={"email": "faculty@vit.edu.in", "password": "Faculty@123"})

        # View own profile / dashboard
        own_res = c.get("/my-profile")
        self.assertEqual(own_res.status_code, 200)

        # View other faculty profiles and papers across departments
        fe_fac = db.get_all_faculty(department="FE")[0]
        other_res = c.get(f"/api/faculty/profile?id={fe_fac['id']}")
        self.assertEqual(other_res.status_code, 200, "Faculty must be able to view other faculty profiles")

        # Download own and other faculty papers
        own_dl = c.get(f"/publication/{self.cmpn_pub_id}/download")
        self.assertEqual(own_dl.status_code, 200, "Faculty can download own paper")
        own_dl.close()
        other_dl = c.get(f"/publication/{self.it_pub_id}/download")
        self.assertEqual(other_dl.status_code, 200, "Faculty can download other faculty paper")
        other_dl.close()

        # Submit own publication with file attachment
        test_file = io.BytesIO(b"%PDF-1.4 Faculty uploaded research paper")
        sub_res = c.post("/my-profile", data={
            "title": "Quantum Computation Advances",
            "year": "2026",
            "journal": "IEEE Quantum",
            "paper_file": (test_file, "quantum_advances.pdf")
        }, follow_redirects=True)
        self.assertEqual(sub_res.status_code, 200)

        # Verify publication was created in DB
        pubs = db.get_publications_for_scholar("dLBhK18AAAAJ")
        quantum_pub = next((p for p in pubs if p["title"] == "Quantum Computation Advances"), None)
        self.assertIsNotNone(quantum_pub, "Submitted publication must exist in database")
        self.assertTrue(quantum_pub.get("file_path"), "Uploaded file path must be recorded")

        # Delete own publication
        del_own = c.post(f"/my-profile/delete/{quantum_pub['id']}", follow_redirects=True)
        self.assertEqual(del_own.status_code, 200)

        # RESTRICTIONS: Cannot delete another faculty member's publication
        # Create a publication by another faculty member
        other_pub_id = db.upsert_publication(
            scholar_id="other_scholar_id",
            title="Other Faculty Autonomous Work",
            year="2026",
            journal="Nature",
            source="manual",
            manual_added_by="other_faculty@vit.edu.in"
        )
        del_other = c.post(f"/my-profile/delete/{other_pub_id}")
        self.assertEqual(del_other.status_code, 403, "Faculty deleting another faculty's publication must return 403")

        # RESTRICTIONS: Cannot access Department Metrics / Impact Metrics
        self.assertEqual(c.get("/department").status_code, 403)
        self.assertEqual(c.get("/impact-metrics").status_code, 403)
        self.assertEqual(c.get("/api/department/metrics").status_code, 403)

        # RESTRICTIONS: Cannot access HOD / Admin routes
        self.assertEqual(c.get("/analysis/department").status_code, 403)
        self.assertEqual(c.get("/reports/monthly").status_code, 403)
        self.assertEqual(c.get("/admin/notifications").status_code, 403)

        print("[TEST 09] Verified Faculty role: Own/other profiles & downloads OK, submission OK, 403 on other's delete, 403 on metrics/admin.")

    # -------------------------------------------------------------
    # 5. HOD ROLE: ASSIGNED DEPARTMENT ISOLATION
    # -------------------------------------------------------------
    def test_10_hod_department_isolation_and_restrictions(self):
        """
        Verify HOD role:
        - Scoped strictly to assigned department (CMPN)
        - Can view CMPN metrics, impact metrics, faculty, analysis, reports
        - CANNOT access other departments by changing URL or API parameters (HTTP 403)
        - Can download papers belonging to CMPN, but 403 when downloading paper from another department
        """
        c = app.test_client()
        c.post("/login", data={"email": "hod@vit.edu.in", "password": "HOD@123"})

        # Access assigned department (CMPN) views
        self.assertEqual(c.get("/analysis/department").status_code, 200)
        self.assertEqual(c.get("/department").status_code, 200)
        self.assertEqual(c.get("/impact-metrics").status_code, 200)
        self.assertEqual(c.get("/reports/monthly").status_code, 200)

        # Access assigned department (CMPN) APIs
        res_metrics = c.get("/api/department/metrics")
        self.assertEqual(res_metrics.status_code, 200)
        data_metrics = res_metrics.get_json()
        self.assertEqual(len(data_metrics), 1, "HOD should only receive assigned department in /api/department/metrics")
        self.assertEqual(data_metrics[0]["Department"], "CMPN")

        res_fac = c.get("/api/department/faculty?dept=CMPN")
        self.assertEqual(res_fac.status_code, 200)

        res_analysis = c.get("/api/analysis/department?dept=CMPN")
        self.assertEqual(res_analysis.status_code, 200)

        # HOD faculty search & list is scoped to CMPN
        res_search = c.get("/api/faculty/search?q=dr")
        self.assertEqual(res_search.status_code, 200)
        for f in res_search.get_json():
            self.assertEqual(f["Department"], "CMPN", "HOD faculty search must only return CMPN faculty")

        # Can view CMPN faculty profile
        cmpn_fac = db.get_all_faculty(department="CMPN")[0]
        self.assertEqual(c.get(f"/api/faculty/profile?id={cmpn_fac['id']}").status_code, 200)

        # Can download paper belonging to CMPN
        cmpn_dl = c.get(f"/publication/{self.cmpn_pub_id}/download")
        self.assertEqual(cmpn_dl.status_code, 200)
        cmpn_dl.close()

        # BACKEND ENFORCEMENT: Changing department parameter to IT/FE/EXTC/Biomedical must return HTTP 403
        self.assertEqual(c.get("/api/analysis/department?dept=IT").status_code, 403, "HOD accessing IT analysis must return 403")
        self.assertEqual(c.get("/api/department/faculty?dept=FE").status_code, 403, "HOD accessing FE faculty must return 403")
        self.assertEqual(c.get("/api/reports/monthly?year=2026&month=11&dept=EXTC").status_code, 403, "HOD accessing EXTC reports must return 403")
        self.assertEqual(c.get("/api/department/export-csv?dept=Biomedical").status_code, 403, "HOD exporting Biomedical CSV must return 403")

        # HOD requesting another department faculty profile must return 403
        it_fac = db.get_all_faculty(department="IT")[0]
        self.assertEqual(c.get(f"/api/faculty/profile?id={it_fac['id']}").status_code, 403, "HOD viewing IT faculty profile must return 403")

        # HOD attempting to download paper from another department (IT) must return 403
        self.assertEqual(c.get(f"/publication/{self.it_pub_id}/download").status_code, 403, "HOD downloading IT paper must return 403")

        print("[TEST 10] Verified HOD role: Strictly locked to CMPN, cross-dept URL/API manipulation blocked with 403.")

    # -------------------------------------------------------------
    # 6. ADMIN ROLE: FULL SYSTEM ACCESS PRESERVED
    # -------------------------------------------------------------
    def test_11_admin_full_system_access(self):
        """Verify Admin retains full system-wide access across all 5 departments."""
        c = app.test_client()
        c.post("/login", data={"email": "admin@vit.edu.in", "password": "Admin@123"})

        # Access all core pages
        for page in ["/", "/faculty-profiles", "/department", "/impact-metrics", "/analysis/department", "/reports/monthly", "/admin/notifications"]:
            res = c.get(page)
            self.assertEqual(res.status_code, 200, f"Admin must have access to {page}")

        # Metrics for all 5 departments
        metrics_res = c.get("/api/department/metrics")
        self.assertEqual(metrics_res.status_code, 200)
        data = metrics_res.get_json()
        depts = [d["Department"] for d in data]
        for p_dept in ["CMPN", "FE", "IT", "EXTC", "Biomedical"]:
            self.assertIn(p_dept, depts, f"Admin metrics must include {p_dept}")

        # Faculty profiles across all departments
        for dept in ["CMPN", "FE", "IT", "EXTC", "Biomedical"]:
            fac = db.get_all_faculty(department=dept)[0]
            self.assertEqual(c.get(f"/api/faculty/profile?id={fac['id']}").status_code, 200)

        # Download any paper across departments
        ad_cmpn = c.get(f"/publication/{self.cmpn_pub_id}/download")
        self.assertEqual(ad_cmpn.status_code, 200)
        ad_cmpn.close()
        ad_it = c.get(f"/publication/{self.it_pub_id}/download")
        self.assertEqual(ad_it.status_code, 200)
        ad_it.close()

        print("[TEST 11] Verified Admin role: Full access across all 5 departments and system management.")

    # -------------------------------------------------------------
    # 7. RESEARCH PAPER DOWNLOAD FUNCTIONALITY & SECURITY
    # -------------------------------------------------------------
    def test_12_paper_download_functionality_and_security(self):
        """
        Verify:
        - Stored paper file download works and returns attachment with original filename
        - External URL paper redirects to external URL
        - Paper without file or external link returns 404
        - Directory traversal attempt returns 403
        - Invalid paper ID returns 404
        """
        c = app.test_client()
        c.post("/login", data={"email": "student@vit.edu.in", "password": "Student@123"})

        # 1. Stored paper file download
        res_dl = c.get(f"/publication/{self.cmpn_pub_id}/download")
        self.assertEqual(res_dl.status_code, 200)
        self.assertIn("Automated_Deep_Learning_Paper.pdf", res_dl.headers.get("Content-Disposition", ""))
        res_dl.close()

        # 2. External paper link redirects
        res_ext = c.get(f"/publication/{self.ext_url_pub_id}/download")
        self.assertEqual(res_ext.status_code, 302)
        self.assertEqual(res_ext.location, "https://doi.org/10.1007/sample-paper")

        # 3. Paper without file or external link
        import uuid
        empty_pub_id = db.upsert_publication(
            scholar_id="dLBhK18AAAAJ",
            title=f"Paper Without Any File Or Link {uuid.uuid4().hex[:6]}",
            year="2020",
            journal="VIT Internal Proceedings",
            source="scholar"
        )
        res_empty = c.get(f"/publication/{empty_pub_id}/download")
        self.assertEqual(res_empty.status_code, 404)

        # 4. Invalid paper ID
        self.assertEqual(c.get("/publication/999999/download").status_code, 404)

        # 5. Directory traversal security check
        traversal_pub_id = db.upsert_publication(
            scholar_id="dLBhK18AAAAJ",
            title=f"Traversal Test Paper {uuid.uuid4().hex[:6]}",
            year="2020",
            journal="Security Proceedings",
            source="scholar",
            file_path="../../config.py"
        )
        res_traversal = c.get(f"/publication/{traversal_pub_id}/download")
        self.assertEqual(res_traversal.status_code, 403, "Path traversal attempt must be blocked with 403")

        print("[TEST 12] Verified Paper Download: File download OK, external URL redirect OK, traversal blocked with 403.")

    # -------------------------------------------------------------
    # 8. NAVIGATION TEMPLATE VISIBILITY PER ROLE
    # -------------------------------------------------------------
    def test_13_navigation_template_visibility_per_role(self):
        """
        Verify rendered HTML navigation menu for each role:
        - Student: Shows ONLY Faculty Directory. Hides Department Metrics, Impact Metrics, Analysis, Reports.
        - Faculty: Shows Faculty Dashboard, Faculty Directory. Hides Metrics, Analysis, Reports.
        - HOD: Shows HOD Dashboard, Department Metrics, Impact Metrics, Faculty Directory, Reports, Notifications.
        - Admin: Shows all navigation options.
        """
        # 1. Student navigation
        c_student = app.test_client()
        c_student.post("/login", data={"email": "student@vit.edu.in", "password": "Student@123"})
        res_st = c_student.get("/faculty-profiles")
        html_st = res_st.get_data(as_text=True)
        self.assertIn("Faculty Directory", html_st)
        self.assertNotIn("Department Metrics", html_st)
        self.assertNotIn("Impact Metrics", html_st)
        self.assertNotIn("Department Analysis", html_st)
        self.assertNotIn("Monthly Report", html_st)
        self.assertNotIn("HOD Dashboard", html_st)

        # 2. Faculty navigation
        c_fac = app.test_client()
        c_fac.post("/login", data={"email": "faculty@vit.edu.in", "password": "Faculty@123"})
        res_fc = c_fac.get("/my-profile")
        html_fc = res_fc.get_data(as_text=True)
        self.assertIn("Faculty Dashboard", html_fc)
        self.assertIn("Faculty Directory", html_fc)
        self.assertNotIn("Department Metrics", html_fc)
        self.assertNotIn("Impact Metrics", html_fc)
        self.assertNotIn("Department Analysis", html_fc)
        self.assertNotIn("Monthly Report", html_fc)

        # 3. HOD navigation
        c_hod = app.test_client()
        c_hod.post("/login", data={"email": "hod@vit.edu.in", "password": "HOD@123"})
        res_hd = c_hod.get("/analysis/department")
        html_hd = res_hd.get_data(as_text=True)
        self.assertIn("HOD Dashboard", html_hd)
        self.assertIn("Department Metrics", html_hd)
        self.assertIn("Impact Metrics", html_hd)
        self.assertIn("Faculty Directory", html_hd)
        self.assertIn("Monthly Report", html_hd)

        # 4. Admin navigation
        c_admin = app.test_client()
        c_admin.post("/login", data={"email": "admin@vit.edu.in", "password": "Admin@123"})
        res_ad = c_admin.get("/")
        html_ad = res_ad.get_data(as_text=True)
        self.assertIn("Home", html_ad)
        self.assertIn("Profiles", html_ad)
        self.assertIn("Department", html_ad)
        self.assertIn("Impact Metrics", html_ad)
        self.assertIn("Department Analysis", html_ad)
        self.assertIn("Monthly Report", html_ad)

        print("[TEST 13] Verified clean role-based navigation rendering across Student, Faculty, HOD, and Admin.")

    # -------------------------------------------------------------
    # 9. DYNAMIC METRICS & DEDUPLICATION
    # -------------------------------------------------------------
    def test_14_dynamic_department_metrics(self):
        """Verify dynamic metrics aggregation from database."""
        c = app.test_client()
        c.post("/login", data={"email": "admin@vit.edu.in", "password": "Admin@123"})
        res = c.get("/api/department/metrics")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        depts = [d["Department"] for d in data]
        for p_dept in ["CMPN", "FE", "IT", "EXTC", "Biomedical"]:
            self.assertIn(p_dept, depts)
        print("[TEST 14] Verified dynamic department metrics for 5 primary departments.")

    def test_15_publication_deduplication(self):
        """Verify publication deduplication prevents duplicate records."""
        test_sid = "test_dedup_scholar_id"
        id1 = db.upsert_publication(
            scholar_id=test_sid,
            title="Deduplication Verification Paper",
            year="2026",
            journal="VIT Journal of Engineering",
            citations=5,
            source="scholar"
        )
        id2 = db.upsert_publication(
            scholar_id=test_sid,
            title="Deduplication Verification Paper",
            year="2026",
            journal="VIT Journal of Engineering",
            citations=7,
            source="scholar"
        )
        self.assertEqual(id1, id2)
        pubs = db.get_publications_for_scholar(test_sid)
        self.assertEqual(len(pubs), 1)
        self.assertEqual(pubs[0]["citations"], 7)
        print("[TEST 15] Verified publication deduplication and citation merge.")

    # -------------------------------------------------------------
    # 10. SUBDOMAIN ROUTING & CRON ENDPOINT
    # -------------------------------------------------------------
    def test_16_subdomain_routing_isolation(self):
        """Verify department-specific HOD hostname scoping."""
        c = app.test_client()
        c.post("/login", data={"email": "hod@vit.edu.in", "password": "HOD@123"}, base_url="http://hodcpmn.vit.edu.in")
        res_cmpn = c.get("/analysis/department", base_url="http://hodcpmn.vit.edu.in")
        self.assertEqual(res_cmpn.status_code, 200)

        c2 = app.test_client()
        c2.post("/login", data={"email": "hod@vit.edu.in", "password": "HOD@123"}, base_url="http://hodit.vit.edu.in")
        res_it = c2.get("/analysis/department", base_url="http://hodit.vit.edu.in", follow_redirects=False)
        self.assertEqual(res_it.status_code, 302)
        self.assertEqual(res_it.location, "/")
        print("[TEST 16] Verified subdomain routing and isolation for 'hodcpmn.vit.edu.in'.")

    def test_17_repeatable_migration_idempotence(self):
        """Verify migration script idempotence."""
        with db.DBConnection() as conn:
            fac_count_before = conn.fetchone("SELECT count(*) as c FROM faculty")["c"]
            user_count_before = conn.fetchone("SELECT count(*) as c FROM users")["c"]

        import migrate_to_db
        migrate_to_db.run_migration()

        with db.DBConnection() as conn:
            fac_count_after = conn.fetchone("SELECT count(*) as c FROM faculty")["c"]
            user_count_after = conn.fetchone("SELECT count(*) as c FROM users")["c"]

        self.assertEqual(fac_count_before, fac_count_after)
        self.assertEqual(user_count_before, user_count_after)
        print(f"[TEST 17] Verified migration idempotence: Faculty ({fac_count_after}), Users ({user_count_after}).")

    def test_18_cron_endpoint_authorization(self):
        """Verify /api/cron/refresh endpoint security."""
        c = app.test_client()
        res_unauth = c.get("/api/cron/refresh")
        self.assertEqual(res_unauth.status_code, 401)

        res_auth = c.get("/api/cron/refresh?limit=0", headers={"X-Cron-Secret": config.CRON_SECRET})
        self.assertEqual(res_auth.status_code, 200)
        data = res_auth.get_json()
        self.assertIn("refreshed", data)
        print("[TEST 18] Verified /api/cron/refresh authorization and secret check.")


if __name__ == "__main__":
    unittest.main()
