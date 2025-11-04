# ----------------------------- init_db.py -----------------------------
import os
import sqlite3
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash

# Use same env names as app.py
DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL)


def get_connection():
    """
    Returns a DB connection:
    - Postgres when DATABASE_URL present (uses DictCursor)
    - SQLite otherwise (file at instance/tourism.db)
    """
    # Try Postgres if configured
    if IS_POSTGRES:
        try:
            url = DATABASE_URL
            # Normalize old-style URLs (Heroku style)
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            conn = psycopg2.connect(
                url,
                sslmode="require",
                cursor_factory=psycopg2.extras.DictCursor
            )
            return conn
        except Exception as e:
            print("⚠️ Could not connect to Postgres in init_db:", e)
            print("➡️ Falling back to SQLite for init.")

    # SQLite fallback
    os.makedirs("instance", exist_ok=True)
    db_path = os.path.join("instance", "tourism.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # ✅ Important for dict-style access
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Auto-handling of SQL placeholders
    id_column = "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    placeholder = "%s" if IS_POSTGRES else "?"

    # -------------------- USERS --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS users (
        id {id_column},
        fullname TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        phone TEXT,
        location TEXT,
        address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- ADMINS --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS admins (
        id {id_column},
        fullname TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        phone TEXT,
        role TEXT DEFAULT 'Administrator',
        avatar_url TEXT DEFAULT '/static/admin_default.png',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- PACKAGES --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT,
    price REAL NOT NULL,
    days TEXT NOT NULL,
    image_url TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- ADMIN ACTIVITY --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS admin_activity (
        id {id_column},
        admin_id INTEGER,
        role TEXT,
        action TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- BOOKINGS --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS bookings (
        id {id_column},
        user_id INTEGER NOT NULL,
        package_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        travel_date TEXT NOT NULL,
        persons INTEGER NOT NULL,
        status TEXT DEFAULT 'CONFIRMED',
        booked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- PAYMENTS --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS payments (
        id {id_column},
        booking_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        payment_status TEXT DEFAULT 'SUCCESS',
        payment_method TEXT DEFAULT 'ONLINE',
        paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- FEEDBACK --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS feedback (
        id {id_column},
        user_name TEXT,
        user_email TEXT,
        subject TEXT,
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- CLOUD ACTIVITY --------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS cloud_activity (
        id {id_column},
        user_id INTEGER,
        role TEXT,
        action TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -------------------- Default Admin --------------------
    try:
        if IS_POSTGRES:
            cur.execute("SELECT COUNT(*) as c FROM admins WHERE email = %s", ("admin@demo.com",))
            res = cur.fetchone()
            count = res["c"]
        else:
            cur.execute("SELECT COUNT(*) FROM admins WHERE email = ?", ("admin@demo.com",))
            res = cur.fetchone()
            count = res[0] if res else 0
    except Exception:
        count = 0

    if count == 0:
        cur.execute(
            f"INSERT INTO admins (fullname, email, password_hash) VALUES ({placeholder}, {placeholder}, {placeholder})",
            ("Admin", "admin@demo.com", generate_password_hash("admin123"))
        )
        print("🧑‍💼 Default admin added (admin@demo.com / admin123)")
    else:
        print("ℹ️ Default admin exists — skipping.")

    # -------------------- Demo Packages --------------------
    try:
        cur.execute("SELECT COUNT(*) FROM packages")
        res = cur.fetchone()
        pkg_count = res[0] if not isinstance(res, dict) else list(res.values())[0]
    except Exception:
        pkg_count = 0

    if pkg_count == 0:
        demo_packages = [
            ("Beach Escape", "Goa", "3N/4D seaside fun", 12999, 4, "https://picsum.photos/seed/goa/800/500", "Available"),
            ("Mountain Retreat", "Manali", "4N/5D snow experience", 17999, 5, "https://picsum.photos/seed/manali/800/500", "Available"),
        ]
        cur.executemany(
            f"INSERT INTO packages (title, location, description, price, days, image_url, status) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
            demo_packages
        )
        print("🏖️ Demo packages inserted")
    else:
        print("ℹ️ Demo packages exist — skipping.")

    # -------------------- Done --------------------
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database initialized successfully!")


if __name__ == "__main__":
    init_db()
