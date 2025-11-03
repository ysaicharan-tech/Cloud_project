# ----------------------------- init_db.py -----------------------------
import os
import sqlite3
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
from werkzeug.security import generate_password_hash

# ---------------------------------------------------------------------
#  Detect environment and set flags
# ---------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL)


def get_connection():
    """
    Returns a database connection.
    - Uses PostgreSQL on cloud (Railway/Render)
    - Uses SQLite locally
    """
    if IS_POSTGRES:
        # psycopg2 can connect directly using DATABASE_URL
        conn = psycopg2.connect(
            DATABASE_URL,
            sslmode="require",
            cursor_factory=psycopg2.extras.DictCursor
        )
        return conn
    else:
        os.makedirs("instance", exist_ok=True)
        db_path = os.path.join("instance", "tourism.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Postgres vs SQLite compatible definitions
    id_column = "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    placeholder = "%s" if IS_POSTGRES else "?"

    # -----------------------------------------------------------------
    #  Table creation — compatible with both SQLite and PostgreSQL
    # -----------------------------------------------------------------
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS users (
        id {id_column},
        fullname TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        phone TEXT,
        location TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

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

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS packages (
        id {id_column},
        title TEXT NOT NULL,
        location TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        days INTEGER NOT NULL,
        image_url TEXT,
        status TEXT DEFAULT 'Available',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS admin_activity (
        id {id_column},
        admin_id INTEGER,
        role TEXT,
        action TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

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

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS cloud_activity (
        id {id_column},
        user_id INTEGER,
        role TEXT,
        action TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # -----------------------------------------------------------------
    #  Default admin (created if no admin exists)
    # -----------------------------------------------------------------
    cur.execute("SELECT COUNT(*) FROM admins;")
    result = cur.fetchone()
    count = result[0] if isinstance(result, (tuple, list)) else result["count"] if isinstance(result, dict) else 0

    if count == 0:
        cur.execute(
            f"INSERT INTO admins (fullname, email, password_hash) VALUES ({placeholder}, {placeholder}, {placeholder})",
            ("Admin", "admin@demo.com", generate_password_hash("admin123"))
        )
        print("🧑‍💼 Default admin added (admin@demo.com / admin123)")

    # -----------------------------------------------------------------
    #  Demo packages (created if no packages exist)
    # -----------------------------------------------------------------
    cur.execute("SELECT COUNT(*) FROM packages;")
    result = cur.fetchone()
    count = result[0] if isinstance(result, (tuple, list)) else result.get("count", 0) if isinstance(result, dict) else 0

    if count == 0:
        demo_packages = [
            ("Beach Escape", "Goa", "3N/4D seaside fun", 12999, 4, "https://picsum.photos/seed/goa/800/500", "Available"),
            ("Mountain Retreat", "Manali", "4N/5D snow experience", 17999, 5, "https://picsum.photos/seed/manali/800/500", "Available"),
        ]
        cur.executemany(
            f"INSERT INTO packages (title, location, description, price, days, image_url, status) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
            demo_packages
        )
        print("🏖️  Demo packages inserted")

    # -----------------------------------------------------------------
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database initialized successfully!")


if __name__ == "__main__":
    init_db()
