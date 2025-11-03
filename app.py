# app.py
import os
import sqlite3
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, g, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# If you have an init_db.py that exposes get_connection and init_db, prefer using it.
# Otherwise get_connection below will create connections on demand.
try:
    from init_db import get_connection as init_get_connection, init_db as init_db_func
    HAS_INIT_DB = True
except Exception:
    init_get_connection = None
    init_db_func = None
    HAS_INIT_DB = False

app = Flask(__name__, instance_relative_config=True)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
os.makedirs(app.instance_path, exist_ok=True)

# Detect environment
DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL)

# Local DB path for SQLite
DB_PATH = os.path.join(app.instance_path, "tourism.db")

# If init_db function present, call it at startup (safe-guarded)
if init_db_func:
    try:
        print("🔄 Running init_db() ...")
        init_db_func()
    except Exception as e:
        print("⚠️ init_db() failed or skipped:", e)


# ---------------- Database connection helper ----------------
def get_connection():
    """
    Return a DB connection:
    - If init_db provided get_connection, use that (it may set cursor factory).
    - Else create connection here:
      - PostgreSQL: use psycopg2 with RealDictCursor for dict-like rows
      - SQLite: sqlite3 with row_factory sqlite3.Row
    """
    if init_get_connection:
        return init_get_connection()

    if IS_POSTGRES:
        # Parse DATABASE_URL and connect
        url = urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode="require"
        )
        # don't set autocommit globally; we'll commit where needed or for safety enable autocommit
        conn.autocommit = False
        return conn
    else:
        os.makedirs(app.instance_path, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        conn.row_factory = sqlite3.Row
        # enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn


def get_db():
    """Attach connection to g and reuse per-request."""
    if "db" not in g:
        g.db = get_connection()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        try:
            db.close()
        except Exception as e:
            print("DB close error:", e)


# ---------------- Unified DB executor ----------------
def _adapt_placeholders(sql: str) -> str:
    """Convert '?' placeholders to '%s' for Postgres usage."""
    if IS_POSTGRES:
        return sql.replace("?", "%s")
    return sql


def db_execute(sql, params=(), fetchone=False, fetchall=False, commit=False, return_lastrowid=False):
    """
    Unified executor.
    - Use ? placeholders in SQL (easier) — they'll be adapted for Postgres.
    - Returns:
        - fetchone -> single row (dict-like for Postgres via RealDictCursor; sqlite3.Row for SQLite)
        - fetchall -> list of rows
        - return_lastrowid -> newly inserted id (works on both DBs)
    - commit -> commits when True
    """
    db = get_db()
    sql2 = _adapt_placeholders(sql)

    # PostgreSQL path
    if IS_POSTGRES:
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            if return_lastrowid:
                # Add RETURNING id for Postgres
                sql_exec = sql2.strip()
                if sql_exec.endswith(";"):
                    sql_exec = sql_exec[:-1]
                sql_exec = f"{sql_exec} RETURNING id"
                cur.execute(sql_exec, params or ())
                new_row = cur.fetchone()
                if commit:
                    db.commit()
                return new_row["id"] if new_row and "id" in new_row else (new_row[0] if new_row else None)
            else:
                cur.execute(sql2, params or ())
                result = None
                if fetchone:
                    result = cur.fetchone()
                elif fetchall:
                    result = cur.fetchall()
                if commit:
                    db.commit()
                return result
        except Exception as e:
            # Rollback on error
            try:
                db.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()

    # SQLite path
    else:
        cur = db.cursor()
        try:
            cur.execute(sql2, params or ())
            if return_lastrowid:
                new_id = cur.lastrowid
                if commit:
                    db.commit()
                return new_id
            if fetchone:
                row = cur.fetchone()
                if commit:
                    db.commit()
                return row
            if fetchall:
                rows = cur.fetchall()
                if commit:
                    db.commit()
                return rows
            if commit:
                db.commit()
            return None
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()


# Backwards-compatible wrapper used in some earlier code
def execute_query(db_connection, query, params=(), fetch=False, fetchone=False, commit=False):
    # This wrapper ignores the passed db_connection and uses db_execute to keep things simple
    return db_execute(query, params=params, fetchall=fetch, fetchone=fetchone, commit=commit)


# ---------------- Logging helper ----------------
def log_action(user_id, role, action):
    try:
        if role == "admin":
            if IS_POSTGRES:
                db_execute("INSERT INTO admin_activity(admin_id, role, action) VALUES (%s, %s, %s)" if IS_POSTGRES else
                           "INSERT INTO admin_activity(admin_id, role, action) VALUES (?, ?, ?)",
                           (user_id, role, action), commit=True)
            else:
                db_execute("INSERT INTO admin_activity(admin_id, role, action) VALUES (?, ?, ?)",
                           (user_id, role, action), commit=True)
        else:
            db_execute("INSERT INTO cloud_activity(user_id, role, action) VALUES (%s, %s, %s)" if IS_POSTGRES else
                       "INSERT INTO cloud_activity(user_id, role, action) VALUES (?, ?, ?)",
                       (user_id, role, action), commit=True)
    except Exception as e:
        print("❌ Log error:", e)


# ---------------- Auth decorators ----------------
def login_required(f):
    @wraps(f)
    def _wrap(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first!", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return _wrap


def admin_required(f):
    @wraps(f)
    def _wrap(*args, **kwargs):
        if "admin_id" not in session:
            flash("Admin login required.", "warning")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return _wrap


# ---------------- Routes (full) ----------------

@app.route("/ping")
def ping():
    return "✅ Flask app running & DB initialized"


@app.route("/")
def index():
    rows = db_execute("SELECT * FROM packages ORDER BY created_at DESC LIMIT 3", fetchall=True)
    return render_template("index.html", packages=rows or [])


@app.route("/about")
def about():
    return render_template("about_us.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        subject = request.form.get("subject")
        msg = request.form.get("message")
        if msg:
            db_execute("INSERT INTO feedback(user_name,user_email,subject,message) VALUES (?, ?, ?, ?)",
                       (name, email, subject, msg), commit=True)
            flash("Thanks for your feedback!", "success")
            log_action(None, "guest", f"Feedback submitted by {email}")
            return redirect(url_for("contact"))
    return render_template("contact_us.html")


@app.route("/user_change_password", methods=["GET", "POST"])
@login_required
def user_change_password():
    message = ""
    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        user = db_execute("SELECT * FROM users WHERE id = ?", (session["user_id"],), fetchone=True)
        # user may be dict-like (Postgres RealDict) or sqlite3.Row
        stored_hash = None
        if user:
            if isinstance(user, dict):
                stored_hash = user.get("password_hash")
            else:
                stored_hash = user["password_hash"] if "password_hash" in user.keys() else None

        if stored_hash and not check_password_hash(stored_hash, current_password):
            message = "Incorrect current password."
        elif new_password != confirm_password:
            message = "New passwords do not match."
        else:
            db_execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(new_password), session["user_id"]), commit=True)
            message = "Password updated successfully!"
    return render_template("user_change_password.html", message=message)


@app.route("/package/<int:pid>")
def package_detail(pid):
    pkg = db_execute("SELECT * FROM packages WHERE id = ?", (pid,), fetchone=True)
    if not pkg:
        abort(404)
    return render_template("book_package.html", package=pkg)


@app.route("/explore")
def explore_packages():
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        rows = db_execute("SELECT * FROM packages WHERE title LIKE ? OR location LIKE ?", (like, like), fetchall=True)
    else:
        rows = db_execute("SELECT * FROM packages", fetchall=True)
    return render_template("explore_packages.html", packages=rows or [], q=q)


@app.route("/book/<int:package_id>", methods=["GET", "POST"])
@login_required
def book_package(package_id):
    package = db_execute("SELECT * FROM packages WHERE id = ?", (package_id,), fetchone=True)
    if not package:
        flash("Package not found.", "error")
        return redirect(url_for("explore_packages"))

    if request.method == "POST":
        user_id = session["user_id"]
        name = request.form.get("name")
        email = request.form.get("email")
        travel_date = request.form.get("travel_date")
        persons = request.form.get("persons")
        if not (name and email and travel_date and persons):
            flash("Please fill all fields.", "error")
        else:
            try:
                persons = int(persons)
                amount = float(package["price"]) * persons

                # Insert booking and get id
                booking_sql = """
                    INSERT INTO bookings (user_id, package_id, name, email, travel_date, persons, status, booked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """
                booking_id = db_execute(booking_sql,
                                        (user_id, package_id, name, email, travel_date, persons, "Confirmed", datetime.now()),
                                        commit=True, return_lastrowid=True)

                # Insert payment
                db_execute("INSERT INTO payments (booking_id, user_id, amount, payment_status, payment_method, paid_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (booking_id, user_id, amount, "SUCCESS", "ONLINE", datetime.now()), commit=True)

                # Ensure booking status set
                db_execute("UPDATE bookings SET status = ? WHERE id = ?", ("Confirmed", booking_id), commit=True)

                flash(f"Booking confirmed! Total: ₹{amount:.2f}", "success")
                log_action(user_id, "user", f"Booked package: {package['title']} | Amount: ₹{amount:.2f}")
                return redirect(url_for("my_bookings"))
            except Exception as e:
                # attempt rollback
                try:
                    get_db().rollback()
                except Exception:
                    pass
                print("❌ Booking/payment error:", e)
                flash("Something went wrong during booking!", "error")

    user = db_execute("SELECT fullname, email FROM users WHERE id = ?", (session["user_id"],), fetchone=True)
    return render_template("book_package.html", package=package, user=user)


@app.route("/my_bookings")
@login_required
def my_bookings():
    rows = db_execute("""
        SELECT b.id, b.travel_date, b.persons, b.status, p.title, p.description, p.price, p.image_url
        FROM bookings b
        JOIN packages p ON b.package_id = p.id
        WHERE b.user_id = ?
        ORDER BY b.booked_at DESC
    """, (session["user_id"],), fetchall=True)
    return render_template("my_bookings.html", bookings=rows or [])


# ---------------- Admin package CRUD & admin profile ----------------

@app.route("/admin/add-package", methods=["GET", "POST"])
@admin_required
def add_package():
    if request.method == "POST":
        title = request.form.get("title")
        location = request.form.get("location")
        description = request.form.get("description")
        price = request.form.get("price")
        days = request.form.get("days")
        image_url = request.form.get("image_url") or "https://picsum.photos/seed/default/800/500"
        if not (title and location and price and days):
            flash("All fields marked * are required.", "error")
        else:
            db_execute("INSERT INTO packages (title, location, description, price, days, image_url, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (title, location, description, price, days, image_url, "Available"), commit=True)
            flash("Package added successfully!", "success")
            log_action(session.get("admin_id"), "admin", f"Added new package: {title}")
            return redirect(url_for("admin_packages"))
    return render_template("add_package.html")


@app.route("/admin/profile/edit", methods=["GET", "POST"])
@admin_required
def edit_admin_profile():
    admin_id = session["admin_id"]
    admin = db_execute("SELECT * FROM admins WHERE id = ?", (admin_id,), fetchone=True)
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        if not (name and email):
            flash("Name and email are required.", "error")
        else:
            db_execute("UPDATE admins SET fullname=?, email=?, phone=? WHERE id=?", (name, email, phone, admin_id), commit=True)
            flash("Profile updated successfully!", "success")
            return redirect(url_for("admin_profile"))
    return render_template("edit_admin_profile.html", admin=admin)


@app.route("/admin/edit-package/<int:pid>", methods=["GET", "POST"])
@admin_required
def edit_package(pid):
    package = db_execute("SELECT * FROM packages WHERE id = ?", (pid,), fetchone=True)
    if not package:
        abort(404)
    if request.method == "POST":
        data = (
            request.form.get("title"),
            request.form.get("location"),
            request.form.get("description"),
            request.form.get("price"),
            request.form.get("days"),
            request.form.get("image_url"),
            request.form.get("status"),
            pid
        )
        db_execute("""
            UPDATE packages
            SET title=?, location=?, description=?, price=?, days=?, image_url=?, status=?
            WHERE id=?
        """, data, commit=True)
        flash("Package updated successfully!", "success")
        log_action(session.get("admin_id"), "admin", f"Edited package ID {pid}")
        return redirect(url_for("admin_packages"))
    return render_template("edit_package.html", package=package)


@app.route("/admin/change-password", methods=["GET", "POST"])
@admin_required
def change_password():
    admin_id = session["admin_id"]
    if request.method == "POST":
        current_pwd = request.form.get("current_password")
        new_pwd = request.form.get("new_password")
        confirm_pwd = request.form.get("confirm_password")
        admin = db_execute("SELECT * FROM admins WHERE id = ?", (admin_id,), fetchone=True)
        stored_hash = admin.get("password_hash") if admin else None
        if stored_hash and not check_password_hash(stored_hash, current_pwd):
            flash("Incorrect current password.", "error")
        elif new_pwd != confirm_pwd:
            flash("New passwords do not match.", "error")
        else:
            db_execute("UPDATE admins SET password_hash=? WHERE id=?", (generate_password_hash(new_pwd), admin_id), commit=True)
            flash("Password changed successfully!", "success")
            return redirect(url_for("admin_profile"))
    return render_template("change_password.html")


@app.route("/admin/delete-package/<int:pid>", methods=["POST"])
@admin_required
def delete_package(pid):
    package = db_execute("SELECT * FROM packages WHERE id = ?", (pid,), fetchone=True)
    if not package:
        abort(404)
    db_execute("DELETE FROM packages WHERE id = ?", (pid,), commit=True)
    flash(f"Package '{package.get('title') if isinstance(package, dict) else package['title']}' deleted.", "info")
    log_action(session.get("admin_id"), "admin", f"Deleted package ID {pid}")
    return redirect(url_for("admin_packages"))


@app.route("/admin/bookings")
@admin_required
def all_bookings():
    rows = db_execute("""
        SELECT 
            b.id, 
            u.fullname AS user_name, 
            u.email AS user_email, 
            p.title AS package_name, 
            b.booked_at AS booking_date, 
            COALESCE(b.status, 'Pending') AS status
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN packages p ON p.id = b.package_id
        ORDER BY b.booked_at DESC
    """, fetchall=True)
    return render_template("all_bookings.html", bookings=rows or [])


@app.route("/check_admin_email")
def check_admin_email():
    email = request.args.get("email")
    a = db_execute("SELECT id FROM admins WHERE email = ?", (email,), fetchone=True)
    return {"exists": bool(a)}


@app.route("/admin/profile")
@admin_required
def admin_profile():
    admin_id = session.get("admin_id")
    admin = db_execute("SELECT * FROM admins WHERE id = ?", (admin_id,), fetchone=True)
    if not admin:
        flash("Admin not found.", "error")
        return redirect(url_for("admin_dashboard"))

    stats = {
        "total_packages": db_execute("SELECT COUNT(*) as c FROM packages", fetchone=True).get("c", 0) if IS_POSTGRES else db_execute("SELECT COUNT(*) FROM packages", fetchone=True)[0],
        "total_bookings": db_execute("SELECT COUNT(*) as c FROM bookings", fetchone=True).get("c", 0) if IS_POSTGRES else db_execute("SELECT COUNT(*) FROM bookings", fetchone=True)[0],
        "total_feedbacks": db_execute("SELECT COUNT(*) as c FROM feedback", fetchone=True).get("c", 0) if IS_POSTGRES else db_execute("SELECT COUNT(*) FROM feedback", fetchone=True)[0],
    }

    avatar_url = admin.get("avatar_url") if isinstance(admin, dict) else (admin["avatar_url"] if "avatar_url" in admin.keys() else None)
    avatar_url = avatar_url or url_for("static", filename="admin_default.png")

    return render_template("admin_profile.html",
                           admin={
                               "fullname": admin.get("fullname") if isinstance(admin, dict) else admin["fullname"],
                               "email": admin.get("email") if isinstance(admin, dict) else admin["email"],
                               "phone": admin.get("phone", "Not Provided") if isinstance(admin, dict) else (admin["phone"] if "phone" in admin.keys() else "Not Provided"),
                               "role": admin.get("role", "Administrator") if isinstance(admin, dict) else "Administrator",
                               "avatar_url": avatar_url
                           },
                           stats=stats)


@app.route("/admin/users")
@admin_required
def view_users():
    rows = db_execute("SELECT id, fullname, email, phone, created_at FROM users", fetchall=True)
    return render_template("user_list.html", users=rows or [])


@app.route("/admin/feedback")
@admin_required
def feedback_reports():
    rows = db_execute("SELECT * FROM feedback ORDER BY created_at DESC", fetchall=True)
    return render_template("feedback_reports.html", feedbacks=rows or [])


# ---------------- User auth ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        password = request.form.get("password")
        if not (fullname and email and password):
            flash("All fields are required.", "error")
        else:
            try:
                db_execute("INSERT INTO users(fullname,email,password_hash) VALUES (?, ?, ?)",
                           (fullname, email, generate_password_hash(password)), commit=True)
                flash("Registration successful! Please log in.", "success")
                log_action(None, "guest", f"User registered: {email}")
                return redirect(url_for("login"))
            except Exception as e:
                print("Register error:", e)
                flash("Email already registered or some error occurred.", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = db_execute("SELECT * FROM users WHERE email = ?", (email,), fetchone=True)
        if not user:
            flash("Email not found. Please register first.", "error")
            return redirect(url_for("login"))
        stored_hash = user.get("password_hash") if isinstance(user, dict) else user["password_hash"]
        if check_password_hash(stored_hash, password):
            # success
            user_id = user.get("id") if isinstance(user, dict) else user["id"]
            user_fullname = user.get("fullname") if isinstance(user, dict) else user["fullname"]
            session.clear()
            session["user_id"] = user_id
            session["user_name"] = user_fullname
            log_action(user_id, "user", "User logged in")
            return redirect(url_for("main_dashboard"))
        flash("Incorrect password.", "error")
    return render_template("login.html")


@app.route("/check_email")
def check_email():
    email = request.args.get("email")
    existing_user = db_execute("SELECT id FROM users WHERE email = ?", (email,), fetchone=True)
    return {"exists": bool(existing_user)}


@app.route("/logout")
def logout():
    if "user_id" in session:
        log_action(session["user_id"], "user", "User logged out")
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def main_dashboard():
    user_id = session["user_id"]

    # counts: using SQL alias consistent with dict or tuple results
    total_bookings_row = db_execute("SELECT COUNT(*) as c FROM bookings WHERE user_id = ?", (user_id,), fetchone=True)
    if IS_POSTGRES:
        total_bookings = total_bookings_row.get("c", 0)
    else:
        total_bookings = total_bookings_row[0] if total_bookings_row else 0

    upcoming_row = db_execute("SELECT COUNT(*) as c FROM bookings WHERE user_id = ? AND date(travel_date) >= date('now')", (user_id,), fetchone=True)
    upcoming_trips = upcoming_row.get("c", 0) if IS_POSTGRES else (upcoming_row[0] if upcoming_row else 0)

    completed_row = db_execute("SELECT COUNT(*) as c FROM bookings WHERE user_id = ? AND date(travel_date) < date('now')", (user_id,), fetchone=True)
    completed_trips = completed_row.get("c", 0) if IS_POSTGRES else (completed_row[0] if completed_row else 0)

    recent_bookings = db_execute("""
        SELECT p.title, p.location, b.travel_date
        FROM bookings b
        JOIN packages p ON p.id = b.package_id
        WHERE b.user_id = ?
        ORDER BY date(b.travel_date) DESC
        LIMIT 5
    """, (user_id,), fetchall=True)

    notifications = [
        "🎉 Your booking has been confirmed!",
        "🧳 New destinations added this week!",
        "💰 Exclusive offers available this month!"
    ]

    travel_tips = [
        "Pack light and smart for your trip!",
        "Always carry a power bank and travel adapter.",
        "Check your passport validity before booking.",
        "Travel insurance gives peace of mind.",
        "Explore local food and culture wherever you go!"
    ]

    return render_template("main_dashboard.html",
                           total_bookings=total_bookings,
                           upcoming_trips=upcoming_trips,
                           completed_trips=completed_trips,
                           recent_bookings=recent_bookings or [],
                           notifications=notifications,
                           travel_tips=travel_tips,
                           profile_pic_url=None)


# ---------------- Admin login & packages list ----------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        a = db_execute("SELECT * FROM admins WHERE email = ?", (email,), fetchone=True)
        if not a:
            flash("Admin email not found.", "error")
            return redirect(url_for("admin_login"))
        stored_hash = a.get("password_hash") if isinstance(a, dict) else a["password_hash"]
        if check_password_hash(stored_hash, password):
            admin_id = a.get("id") if isinstance(a, dict) else a["id"]
            admin_name = a.get("fullname") if isinstance(a, dict) else a["fullname"]
            session.clear()
            session["admin_id"] = admin_id
            session["admin_name"] = admin_name
            log_action(admin_id, "admin", "Admin logged in")
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect password.", "error")
    return render_template("admin_login.html")

# -------------------- USER PROFILE (fixed) --------------------

@app.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    # Use db_execute (no need to pass a db connection)
    db_execute(
        "UPDATE users SET fullname=?, email=?, phone=?, location=? WHERE id=?",
        (request.form["name"], request.form["email"], request.form["phone"], request.form["location"], session["user_id"]),
        commit=True
    )
    flash("Profile updated successfully!", "success")
    return redirect(url_for("profile"))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        db_execute(
            "UPDATE users SET fullname=?, phone=?, address=? WHERE id=?",
            (request.form['name'], request.form['phone'], request.form['address'], session['user_id']),
            commit=True
        )
        flash("Profile updated successfully!", "success")
        return redirect(url_for('profile'))

    user = db_execute("SELECT * FROM users WHERE id=?", (session['user_id'],), fetchone=True)
    return render_template('profile.html', user=user)


# -------------------- ADMIN REGISTER / LOGOUT / DASHBOARD (fixed) --------------------

@app.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    if request.method == "POST":
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if not (fullname and email and password and confirm_password):
            flash("All fields are required!", "error")
            return redirect(url_for("admin_register"))

        if password != confirm_password:
            flash("Passwords do not match!", "error")
            return redirect(url_for("admin_register"))

        try:
            db_execute(
                "INSERT INTO admins (fullname, email, password_hash) VALUES (?, ?, ?)",
                (fullname, email, generate_password_hash(password)),
                commit=True
            )
            flash("New admin registered successfully!", "success")
            return redirect(url_for("admin_login"))
        except Exception as e:
            # unique constraint or other DB error
            print("Admin register error:", e)
            flash("Email already exists or DB error.", "error")

    return render_template("admin_register.html")


@app.route("/admin/logout")
def admin_logout():
    if "admin_id" in session:
        log_action(session["admin_id"], "admin", "Admin logged out")
    session.clear()
    # Redirect to the admin login page (nice UX)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    # Get counts robustly and support both SQLite and Postgres return shapes
    # Use SQL that aliases the count as 'c' so we can handle both dict and tuple results
    total_users_row = db_execute("SELECT COUNT(*) AS c FROM users", fetchone=True)
    total_bookings_row = db_execute("SELECT COUNT(*) AS c FROM bookings", fetchone=True)
    total_revenue_row = db_execute(
        "SELECT COALESCE(SUM(amount), 0) AS c FROM payments WHERE TRIM(LOWER(payment_status)) = 'success'",
        fetchone=True
    )

    def _val(row):
        if row is None:
            return 0
        if isinstance(row, dict):
            return row.get("c", 0)
        # sqlite3.Row acts like a sequence + mapping; prefer index 0 or key 'c'
        try:
            return row["c"]
        except Exception:
            return row[0]

    total_users = _val(total_users_row)
    total_bookings = _val(total_bookings_row)
    total_revenue = _val(total_revenue_row)

    # Feedback table existence: use the proper metadata table depending on DB
    if IS_POSTGRES:
        fb_table_exists = db_execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'feedback') AS exists",
            fetchone=True
        )
        new_messages = 0
        if fb_table_exists and ((isinstance(fb_table_exists, dict) and fb_table_exists.get("exists")) or (not isinstance(fb_table_exists, dict) and fb_table_exists[0])):
            cnt = db_execute("SELECT COUNT(*) AS c FROM feedback", fetchone=True)
            new_messages = _val(cnt)
    else:
        # SQLite: check sqlite_master
        fb_table = db_execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback'", fetchone=True)
        new_messages = db_execute("SELECT COUNT(*) AS c FROM feedback", fetchone=True)
        new_messages = _val(new_messages) if fb_table else 0

    admin_id = session.get("admin_id")
   
    placeholder = "%s" if IS_POSTGRES else "?"
    admin = db_execute(f"SELECT fullname, email, avatar_url, phone, role FROM admins WHERE id = {placeholder}", (admin_id,), fetchone=True)

    # Normalize admin fields for template
    if admin:
        if isinstance(admin, dict):
            admin_name = admin.get("fullname", "Admin")
            admin_email = admin.get("email", "admin@example.com")
            admin_avatar_url = admin.get("avatar_url") or url_for("static", filename="admin_default.png")
        else:
            admin_name = admin["fullname"] if "fullname" in admin.keys() else "Admin"
            admin_email = admin["email"] if "email" in admin.keys() else "admin@example.com"
            admin_avatar_url = admin["avatar_url"] if "avatar_url" in admin.keys() and admin["avatar_url"] else url_for("static", filename="admin_default.png")
    else:
        admin_name = "Admin"
        admin_email = "admin@example.com"
        admin_avatar_url = url_for("static", filename="admin_default.png")
    

    return render_template(
        "admin_dashboard.html",
        admin_name=admin_name,
        admin_email=admin_email,
        admin_avatar_url=admin_avatar_url,
        total_users=total_users,
        total_bookings=total_bookings,
        total_revenue=float(total_revenue) if total_revenue else 0,
        new_messages=new_messages
    )

@app.route("/admin/packages")
@admin_required
def admin_packages():
    rows = db_execute("SELECT * FROM packages", fetchall=True)
    return render_template("manage_packages.html", packages=rows or [])


# ---------------- Error handler & run ----------------
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # debug True locally; in production Railway sets env, so you may want debug=False there
    debug_mode = bool(os.environ.get("FLASK_DEBUG", "1")) if not IS_POSTGRES else False
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
