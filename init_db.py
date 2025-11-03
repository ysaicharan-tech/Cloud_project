import os
import sqlite3
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse
from werkzeug.security import generate_password_hash

# ------------------- Database Connection Setup -------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL)


def get_connection():
    """Return a database connection (PostgreSQL on cloud, SQLite locally)."""
    if IS_POSTGRES:
        result = urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port,
            sslmode="require",
            cursor_factory=DictCursor
        )
        print("🔗 Connected to PostgreSQL database")
    else:
        os.makedirs("instance", exist_ok=True)
        DB_PATH = os.path.join("instance", "tourism.db")
        conn = sqlite3.connect(DB_PATH)
        print("🔗 Connected to SQLite database")
    return conn


# ------------------- Database Initialization -------------------
def init_db():
    conn = get_connection()
    cur = conn.cursor()

    id_column = "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    placeholder = "%s" if IS_POSTGRES else "?"

    # ---------------- Tables ----------------
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

    # ---------------- Default Admin ----------------
    cur.execute("SELECT COUNT(*) FROM admins")
    if cur.fetchone()[0] == 0:
        cur.execute(
            f"INSERT INTO admins(fullname, email, password_hash) VALUES ({placeholder}, {placeholder}, {placeholder})",
            ("Admin", "admin@demo.com", generate_password_hash("admin123"))
        )

    # ---------------- Demo Packages ----------------
    cur.execute("SELECT COUNT(*) FROM packages")
    if cur.fetchone()[0] == 0:
        demo = [
            ("Beach Escape", "Goa", "3N/4D seaside fun", 12999, 4, "https://picsum.photos/seed/goa/800/500", "Available"),
            ("Mountain Retreat", "Manali", "4N/5D snow experience", 17999, 5, "https://picsum.photos/seed/manali/800/500", "Available"),
        ]
        cur.executemany(
            f"INSERT INTO packages(title, location, description, price, days, image_url, status) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
            demo
        )

    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database initialized successfully!")


# ------------------- Run directly -------------------
if __name__ == "__main__":
    init_db()
