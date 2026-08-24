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

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-fallback-key-change-me")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
DATABASE_PATH = str(BASE_DIR / os.getenv("DATABASE_PATH", "app_data.db"))
FLASK_ENV = os.getenv("FLASK_ENV", "development")
DEBUG = FLASK_ENV != "production"
