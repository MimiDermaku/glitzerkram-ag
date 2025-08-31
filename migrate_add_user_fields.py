# migrate_add_user_fields.py
import os, sys, sqlite3

def main():
    base = os.path.dirname(__file__)
    # Standard: instance/users.db — per Argument überschreibbar
    db_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(base, "instance", "users.db")

    if not os.path.exists(db_path):
        print(f"❌ DB nicht gefunden: {db_path}")
        print(r"Tipp: so starten: .\.venv312\Scripts\python.exe .\migrate_add_user_fields.py .\instance\users.db")
        raise SystemExit(1)

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # existiert 'users'?
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cur.fetchone():
        print("❌ Tabelle 'users' existiert nicht. Erst Bootstrap/Schema ausführen.")
        con.close()
        raise SystemExit(2)

    # vorhandene Spalten ermitteln
    cur.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}

    added = []
    if "join_date" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN join_date TEXT")
        added.append("join_date")
    if "weekly_minutes" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN weekly_minutes INTEGER DEFAULT 2400")
        added.append("weekly_minutes")
    if "is_active" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
        added.append("is_active")

    con.commit()
    con.close()

    if added:
        print(f"✅ Ergänzt in {db_path}: {', '.join(added)}")
    else:
        print(f"ℹ️ Nichts zu tun – Spalten bereits vorhanden in {db_path}")

if __name__ == "__main__":
    main()
