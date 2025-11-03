

import os, sqlite3
import psycopg2
from psycopg2.extras import DictCursor

from urllib.parse import urlparse
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, g, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from init_db import get_connection

# ---------------------- Initialize Database ----------------------
from init_db import init_db  # ✅ make sure init_db.py is in the same folder

try:
    print("🔄 Initializing database...")
    init_db()
except Exception as e:
    print(f"⚠️ Database init skipped or failed: {e}")


# -------------------------- App setup --------------------------
app = Flask(__name__, instance_relative_config=True)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
os.makedirs(app.instance_path, exist_ok=True)
#DB_PATH = os.path.join(app.instance_path, "tourism.db")

# ----------------------- DB helpers ----------------------------

def get_db():
    if "db" not in g:
        db_url = os.environ.get("DATABASE_URL")  # Render will provide this
        if db_url:
            # --- Use PostgreSQL when deployed on Render ---
            url = urlparse(db_url)
            g.db = psycopg2.connect(
                database=url.path[1:],
                user=url.username,
                password=url.password,
                host=url.hostname,
                port=url.port,
                cursor_factory=DictCursor
            )
            g.db.autocommit = True
        else:
            # --- Use SQLite when running locally ---
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON;")

    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db:
        try:
            db.close()
        except Exception as e:
            print("DB close error:", e)

@app.route("/ping")
def ping():
    return "✅ Flask app running & DB initialized"


# ---------------------- Helper: log cloud actions --------------
def log_action(user_id, role, action):
    db = get_db()
    try:
        if os.environ.get("DATABASE_URL"):  # PostgreSQL on Render
            cur = db.cursor()
            if role == "admin":
                cur.execute("INSERT INTO admin_activity(admin_id, role, action) VALUES (%s, %s, %s)", (user_id, role, action))
            else:
                cur.execute("INSERT INTO cloud_activity(user_id, role, action) VALUES (%s, %s, %s)", (user_id, role, action))
            db.commit()
        else:  # SQLite local
            db.execute("INSERT INTO admin_activity(admin_id, role, action) VALUES (?, ?, ?)", (user_id, role, action))
            db.commit()
    except Exception as e:
        print("❌ Log error:", e)







# ---------------------- Decorators ------------------------------
def login_required(f):
    from functools import wraps
    @wraps(f)
    def _wrap(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first!", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return _wrap

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def _wrap(*args, **kwargs):
        if "admin_id" not in session:
            flash("Admin login required.", "warning")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return _wrap
def get_db_cursor():
    db = get_db()
    return db.cursor()


# ----------------------- Public routes --------------------------

@app.route("/")
def index():
    cur = get_db_cursor()
    cur.execute("SELECT * FROM packages ORDER BY created_at DESC LIMIT 3")
    pkgs = cur.fetchall()
    cur.close()
    return render_template("index.html", packages=pkgs)



@app.route("/about")
def about():
    return render_template("about_us.html")

@app.route("/contact", methods=["GET","POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        subject = request.form.get("subject")
        msg = request.form.get("message")
        if msg:
            db = get_db()
            db.execute("INSERT INTO feedback(user_name,user_email,subject,message) VALUES (?,?,?,?)",
                       (name, email, subject, msg))
            db.commit()
            flash("Thanks for your feedback!", "success")
            log_action(None, "guest", f"Feedback submitted by {email}")
            return redirect(url_for("contact"))
    return render_template("contact_us.html")

# ---------------------- Booking flow ----------------------------
@app.route("/user_change_password", methods=["GET", "POST"])
def user_change_password():
    if "user_id" not in session:
        return redirect("/login")

    message = ""
    if request.method == "POST":
        current_password = request.form["current_password"]
        new_password = request.form["new_password"]
        confirm_password = request.form["confirm_password"]

        db = get_db()
        user = db.execute("SELECT password FROM users WHERE id = ?", (session["user_id"],)).fetchone()

        if not user or user["password"] != current_password:
            message = "Incorrect current password."
        elif new_password != confirm_password:
            message = "New passwords do not match."
        else:
            db.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, session["user_id"]))
            db.commit()
            message = "Password updated successfully!"

    return render_template("user_change_password.html", message=message)




@app.route("/package/<int:pid>")
def package_detail(pid):
    db = get_db()
    pkg = db.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
    if not pkg: abort(404)
    return render_template("book_package.html", package=pkg)






# ---------------------- Booking flow ----------------------------
@app.route("/explore")
def explore_packages():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        pkgs = db.execute(
            "SELECT * FROM packages WHERE title LIKE ? OR location LIKE ?",
            (f"%{q}%", f"%{q}%")
        ).fetchall()
    else:
        pkgs = db.execute("SELECT * FROM packages").fetchall()
    return render_template("explore_packages.html", packages=pkgs, q=q)


# 🔹 NEW: Book package route (matches book_package.html)


@app.route("/book/<int:package_id>", methods=["GET", "POST"])
@login_required
def book_package(package_id):
    db = get_db()
    package = db.execute("SELECT * FROM packages WHERE id=?", (package_id,)).fetchone()

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
                amount = package["price"] * persons

                cursor = db.execute("""
                    INSERT INTO bookings (user_id, package_id, name, email, travel_date, persons, status, booked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (user_id, package_id, name, email, travel_date, persons, "Confirmed", datetime.now()))
                
                booking_id = cursor.lastrowid

                db.execute("""
                    INSERT INTO payments (booking_id, user_id, amount, payment_status, payment_method, paid_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (booking_id, user_id, amount, "SUCCESS", "ONLINE", datetime.now()))

                # 🔹 Make absolutely sure the booking is marked confirmed
                db.execute("UPDATE bookings SET status='Confirmed' WHERE id=?", (booking_id,))
                db.commit()

                flash(f"Booking confirmed! Total: ₹{amount:.2f}", "success")
                log_action(user_id, "user", f"Booked package: {package['title']} | Amount: ₹{amount:.2f}")

                return redirect(url_for("my_bookings"))

            except Exception as e:
                db.rollback()
                print("❌ Booking/payment error:", e)
                flash("Something went wrong during booking!", "error")

    user = db.execute("SELECT fullname, email FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return render_template("book_package.html", package=package, user=user)






# 🔹 UPDATED: My Bookings route (matches my_bookings.html)

@app.route("/my_bookings")
@login_required
def my_bookings():
    db = get_db()
    bookings = db.execute("""
        SELECT 
            b.id,
            b.travel_date,
            b.persons,
            b.status,
            p.title,
            p.description,
            p.price,
            p.image_url
        FROM bookings b
        JOIN packages p ON b.package_id = p.id
        WHERE b.user_id = ?
        ORDER BY b.booked_at DESC
    """, (session["user_id"],)).fetchall()

    return render_template("my_bookings.html", bookings=bookings)


# ----------------------- User auth ------------------------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        password = request.form.get("password")
        if not (fullname and email and password):
            flash("All fields are required.", "error")
        else:
            try:
                db = get_db()
                db.execute("INSERT INTO users(fullname,email,password_hash) VALUES (?,?,?)",
                           (fullname, email, generate_password_hash(password)))
                db.commit()
                flash("Registration successful! Please log in.", "success")
                log_action(None, "guest", f"User registered: {email}")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Email already registered.", "error")
    return render_template("user_register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            flash("Email not found. Please register first.", "error")
            return redirect(url_for("login"))
        if check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["fullname"]
            log_action(user["id"], "user", "User logged in")
            return redirect(url_for("main_dashboard"))
        flash("Incorrect password.", "error")
    return render_template("user_login.html")


@app.route("/check_email")
def check_email():
    email = request.args.get("email")
    db = get_db()
    existing_user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
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
    db = get_db()
    user_id = session["user_id"]

    # --- Total bookings ---
    total_bookings = db.execute(
        "SELECT COUNT(*) AS c FROM bookings WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]

    # --- Upcoming trips (based on travel_date in future) ---
    upcoming_trips = db.execute("""
        SELECT COUNT(*) AS c 
        FROM bookings 
        WHERE user_id = ? AND date(travel_date) >= date('now')
    """, (user_id,)).fetchone()["c"]

    # --- Completed trips (travel_date in past) ---
    completed_trips = db.execute("""
        SELECT COUNT(*) AS c 
        FROM bookings 
        WHERE user_id = ? AND date(travel_date) < date('now')
    """, (user_id,)).fetchone()["c"]

    # --- Recent bookings ---
    recent_bookings = db.execute("""
        SELECT p.title, p.location, b.travel_date
        FROM bookings b
        JOIN packages p ON p.id = b.package_id
        WHERE b.user_id = ?
        ORDER BY date(b.travel_date) DESC
        LIMIT 5
    """, (user_id,)).fetchall()

    # --- Notifications ---
    notifications = [
        "🎉 Your booking has been confirmed!",
        "🧳 New destinations added this week!",
        "💰 Exclusive offers available this month!"
    ]

    # --- Travel Tips ---
    travel_tips = [
        "Pack light and smart for your trip!",
        "Always carry a power bank and travel adapter.",
        "Check your passport validity before booking.",
        "Travel insurance gives peace of mind.",
        "Explore local food and culture wherever you go!"
    ]

    # --- Profile Picture (optional) ---
    profile_pic_url = None

    return render_template(
        "main_dashboard.html",
        total_bookings=total_bookings,
        upcoming_trips=upcoming_trips,
        completed_trips=completed_trips,
        recent_bookings=recent_bookings,
        notifications=notifications,
        travel_tips=travel_tips,
        profile_pic_url=profile_pic_url
    )


@app.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    name = request.form["name"]
    email = request.form["email"]
    phone = request.form["phone"]
    location = request.form["location"]

    db = get_db()
    db.execute("""
        UPDATE users SET fullname=?, email=?, phone=?, location=? WHERE id=?
    """, (name, email, phone, location, session["user_id"]))
    db.commit()

    flash("Profile updated successfully!", "success")
    return redirect(url_for("profile"))

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    db = get_db()

    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        address = request.form['address']
        
        db.execute(
            "UPDATE users SET name = ?, phone = ?, address = ? WHERE id = ?",
            (name, phone, address, session['user_id'])
        )
        db.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for('profile'))

    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (session['user_id'],)
    ).fetchone()

    return render_template('profile.html', user=user)



# ---------------------- Admin section ---------------------------
@app.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    db = get_db()
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
            db.execute(
                "INSERT INTO admins (fullname, email, password_hash) VALUES (?, ?, ?)",
                (fullname, email, generate_password_hash(password)),
            )
            db.commit()
            flash("New admin registered successfully!", "success")
            return redirect(url_for("admin_login"))
        except sqlite3.IntegrityError:
            flash("Email already exists.", "error")

    return render_template("admin_register.html")


@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        db = get_db()
        a = db.execute("SELECT * FROM admins WHERE email=?", (email,)).fetchone()
        if not a:
            flash("Admin email not found.", "error")
            return redirect(url_for("admin_login"))
        if check_password_hash(a["password_hash"], password):
            session.clear()
            session["admin_id"] = a["id"]
            session["admin_name"] = a["fullname"]
            log_action(a["id"], "admin", "Admin logged in")
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect password.", "error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    if "admin_id" in session:
        log_action(session["admin_id"], "admin", "Admin logged out")
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()

    # --- Basic counts ---
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_bookings = db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]

    # --- Total Revenue (robust check) ---
    total_revenue = db.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM payments
        WHERE TRIM(LOWER(payment_status)) = 'success';
    """).fetchone()[0]

    # --- New Feedback Messages ---
    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feedback'").fetchone():
        new_messages = db.execute("SELECT COUNT(*) FROM feedback;").fetchone()[0]
    else:
        new_messages = 0

    # --- Admin Info ---
    admin_id = session.get("admin_id")
    admin = (
        db.execute("SELECT fullname, email FROM admins WHERE id = ?", (admin_id,)).fetchone()
        if admin_id else None
    )
    admin_name = admin["fullname"] if admin else "Admin"
    admin_email = admin["email"] if admin else "admin@example.com"
    admin_avatar_url = url_for("static", filename="admin_default.png")

    # --- Render Template ---
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
    db = get_db()
    pkgs = db.execute("SELECT * FROM packages").fetchall()
    return render_template("manage_packages.html", packages=pkgs)

# ---------------------- Admin: Package CRUD ----------------------
@app.route("/admin/add-package", methods=["GET", "POST"])
@admin_required
def add_package():
    db = get_db()
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
            db.execute("""
                INSERT INTO packages (title, location, description, price, days, image_url, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (title, location, description, price, days, image_url, "Available"))
            db.commit()
            flash("Package added successfully!", "success")
            log_action(session["admin_id"], "admin", f"Added new package: {title}")
            return redirect(url_for("admin_packages"))
    return render_template("add_package.html")
@app.route("/admin/profile/edit", methods=["GET", "POST"])
@admin_required
def edit_admin_profile():   # ✅ must match the template name
    db = get_db()
    admin_id = session["admin_id"]
    admin = db.execute("SELECT * FROM admins WHERE id=?", (admin_id,)).fetchone()

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")

        if not (name and email):
            flash("Name and email are required.", "error")
        else:
            db.execute(
                "UPDATE admins SET fullname=?, email=?, phone=? WHERE id=?",
                (name, email, phone, admin_id)
            )
            db.commit()
            flash("Profile updated successfully!", "success")
            return redirect(url_for("admin_profile"))

    return render_template("edit_admin_profile.html", admin=admin)


@app.route("/admin/edit-package/<int:pid>", methods=["GET", "POST"])
@admin_required
def edit_package(pid):
    db = get_db()
    package = db.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
    if not package:
        abort(404)
    if request.method == "POST":
        title = request.form.get("title")
        location = request.form.get("location")
        description = request.form.get("description")
        price = request.form.get("price")
        days = request.form.get("days")
        image_url = request.form.get("image_url")
        status = request.form.get("status")
        db.execute("""
            UPDATE packages
            SET title=?, location=?, description=?, price=?, days=?, image_url=?, status=?
            WHERE id=?
        """, (title, location, description, price, days, image_url, status, pid))
        db.commit()
        flash("Package updated successfully!", "success")
        log_action(session["admin_id"], "admin", f"Edited package ID {pid}")
        return redirect(url_for("admin_packages"))
    return render_template("edit_package.html", package=package)






@app.route("/admin/change-password", methods=["GET", "POST"])
@admin_required
def change_password():
    db = get_db()
    admin_id = session["admin_id"]

    if request.method == "POST":
        current_pwd = request.form.get("current_password")
        new_pwd = request.form.get("new_password")
        confirm_pwd = request.form.get("confirm_password")

        admin = db.execute("SELECT * FROM admins WHERE id=?", (admin_id,)).fetchone()

        if not check_password_hash(admin["password_hash"], current_pwd):
            flash("Incorrect current password.", "error")
        elif new_pwd != confirm_pwd:
            flash("New passwords do not match.", "error")
        else:
            db.execute("UPDATE admins SET password_hash=? WHERE id=?", (generate_password_hash(new_pwd), admin_id))
            db.commit()
            flash("Password changed successfully!", "success")
            return redirect(url_for("admin_profile"))

    return render_template("change_password.html")

@app.route("/admin/delete-package/<int:pid>", methods=["POST"])
@admin_required
def delete_package(pid):
    db = get_db()
    package = db.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
    if not package:
        abort(404)
    db.execute("DELETE FROM packages WHERE id=?", (pid,))
    db.commit()
    flash(f"Package '{package['title']}' deleted.", "info")
    log_action(session["admin_id"], "admin", f"Deleted package ID {pid}")
    return redirect(url_for("admin_packages"))


@app.route("/admin/bookings")
@admin_required
def all_bookings():
    db = get_db()
    bks = db.execute("""
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
    """).fetchall()
    return render_template("all_bookings.html", bookings=bks)

@app.route("/check_admin_email")
def check_admin_email():
    email = request.args.get("email")
    db = get_db()
    admin = db.execute("SELECT id FROM admins WHERE email = ?", (email,)).fetchone()
    return {"exists": bool(admin)}
@app.route("/admin/profile")
@admin_required
def admin_profile():
    db = get_db()
    admin_id = session.get("admin_id")

    # Fetch admin data
    admin = db.execute("SELECT * FROM admins WHERE id=?", (admin_id,)).fetchone()

    # Prevent NoneType errors
    if not admin:
        flash("Admin not found.", "error")
        return redirect(url_for("admin_dashboard"))

    # Stats for the cards
    stats = {
        "total_packages": db.execute("SELECT COUNT(*) FROM packages").fetchone()[0],
        "total_bookings": db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
        "total_feedbacks": db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0],
    }

    # Add fallback avatar

    # Use admin avatar if available, otherwise fallback
    avatar_url = admin["avatar_url"] if admin["avatar_url"] else url_for("static", filename="admin_default.png")


    return render_template(
        "admin_profile.html",
        admin={
            "fullname": admin["fullname"],
            "email": admin["email"],
            "phone": admin["phone"] if "phone" in admin.keys() else "Not Provided",
            "role": admin["role"] if "role" in admin.keys() else "Administrator",
            "avatar_url": avatar_url
        },
        stats=stats
    )



@app.route("/admin/users")
@admin_required
def view_users():
    db = get_db()
    users = db.execute("SELECT id, fullname, email, phone, created_at FROM users").fetchall()
    return render_template("user_list.html", users=users)

@app.route("/admin/feedback")
@admin_required
def feedback_reports():
    db = get_db()
    fb = db.execute("SELECT * FROM feedback ORDER BY created_at DESC").fetchall()
    return render_template("feedback_reports.html", feedbacks=fb)



# ----------------------- Error page -----------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

# ---------------------- Run locally -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
