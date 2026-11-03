"""
Authentication: Flask-Login setup, User model, login/logout routes,
and a @role_required decorator for restricting pages by role
(admin, hod, faculty, student).
"""
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

import db

auth_bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to view this page."


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.email = row["email"]
        self.role = row["role"]
        self.name = row["name"]
        self.department = row["department"]
        self.scholar_id = row["scholar_id"]


@login_manager.user_loader
def load_user(user_id):
    row = db.get_user_by_id(user_id)
    return User(row) if row else None


def role_required(*roles):
    """Restrict a route to one or more roles, e.g. @role_required('admin', 'hod')"""
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        row = db.get_user_by_email(email)

        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row))
            flash(f"Welcome back, {row['name']}!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("home"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    flash("Public registration is disabled. Please log in with your assigned demonstration credentials.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been logged out.", "info")
    return redirect(url_for("auth.login"))
