"""
Central place for all configuration & secrets.
Everything here is loaded from the .env file (which is git-ignored)
so no API keys or secret keys ever get pushed to GitHub.

If you're deploying (Render/Railway/PythonAnywhere/etc.), set these same
variable names in that platform's "Environment Variables" panel instead
of uploading a .env file.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

FLASK_ENV = os.getenv("FLASK_ENV", "development")
DEBUG = FLASK_ENV != "production"

SECRET_KEY = os.getenv("SECRET_KEY", "dev-fallback-key-change-me")
if FLASK_ENV == "production" and SECRET_KEY == "dev-fallback-key-change-me":
    # In production, default fallback key is unsafe
    import logging
    logging.warning("WARNING: SECRET_KEY is set to insecure default in production! Set SECRET_KEY in environment.")

SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
CRON_SECRET = os.getenv("CRON_SECRET", "vit-research-portal-cron-secret")

# Database configuration: Neon PostgreSQL or SQLite fallback
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    # Fix for SQLAlchemy / psycopg2 which requires postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATABASE_PATH = str(BASE_DIR / os.getenv("DATABASE_PATH", "app_data.db"))
IS_POSTGRES = bool(DATABASE_URL and not DATABASE_URL.startswith("sqlite"))

# Session Security Configuration
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = (FLASK_ENV == "production")

# Official Portal Departments
PRIMARY_DEPARTMENTS = ["CMPN", "FE", "IT", "EXTC", "Biomedical"]

# Department-Specific HOD Hostname Mappings
# Preserves exact spelling of supplied hostname 'hodcpmn.vit.edu.in'
HOD_SUBDOMAIN_MAP = {
    "hodcpmn.vit.edu.in": "CMPN",
    "hodfe.vit.edu.in": "FE",
    "hodit.vit.edu.in": "IT",
    "hodextc.vit.edu.in": "EXTC",
    "hodbiomedical.vit.edu.in": "Biomedical"
}

# Upload Configuration for Research Papers
UPLOAD_FOLDER = str(BASE_DIR / "uploads" / "papers")
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt"}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload


