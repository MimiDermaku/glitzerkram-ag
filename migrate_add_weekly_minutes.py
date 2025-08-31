# migrate_add_weekly_minutes.py
import os, sqlite3

BASE = os.path.dirname(__file__)
DB   = os.path.join(BASE, "instance", "users.db")  # dein Pfad

def main():
    if not os.path.exists(DB):
        raise SystemExit(f"❌ DB nicht gefunden: {DB}")

    con = sqlite3.connect(DB)
    cur = con.cursor()

    # Prüfen, ob Tabelle users existiert
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cur.fetchone():
        con.close()
        raise SystemExit("❌ Tabelle 'users' existiert nicht (App einmal starten, damit Schema angelegt wird).")

    # Spalte schon da?
    cur.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}

    if "weekly_minutes" in cols:
        print("ℹ️ 'weekly_minutes' existiert bereits – nichts zu tun.")
        con.close()
        return

    # Spalte hinzufügen (Default: 2400 = 40h/Woche)
    cur.execute("ALTER TABLE users ADD COLUMN weekly_minutes INTEGER DEFAULT 2400")
    con.commit()
    con.close()
    print("✅ 'weekly_minutes' wurde hinzugefügt (DEFAULT 2400).")

if __name__ == "__main__":
    main()
