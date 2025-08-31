# migrate_actions_rebuild.py
import os, sqlite3

BASE = os.path.dirname(__file__)
DB   = os.path.join(BASE, "instance", "users.db")  # <-- dein DB-Pfad

NEW_ALLOWED = ["bin da", "gehe", "afk", "wieder da", "päuschen", "mache weiter"]

# Mapping Alt->Neu
MAP = {
    "kommt":      "bin da",
    "geht":       "gehe",
    "abwesend":   "afk",
    "wieder_da":  "wieder da",
    "pause":      "päuschen",
    "pausenende": "mache weiter",
}

def main():
    if not os.path.exists(DB):
        raise SystemExit(f"DB nicht gefunden: {DB}")

    con = sqlite3.connect(DB)
    cur = con.cursor()

    # Existiert bookings?
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bookings'")
    if not cur.fetchone():
        con.close()
        raise SystemExit("Tabelle 'bookings' existiert nicht.")

    # FKs aus, solange wir umbauen
    cur.execute("PRAGMA foreign_keys=OFF")
    con.commit()

    # 1) Alte Tabelle umbenennen
    cur.execute("ALTER TABLE bookings RENAME TO bookings_old")

    # 2) Neue Tabelle mit neuem CHECK anlegen
    allowed_sql = ",".join(["'" + v.replace("'", "''") + "'" for v in NEW_ALLOWED])
    cur.execute(f"""
        CREATE TABLE bookings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            action         TEXT NOT NULL CHECK (action IN ({allowed_sql})),
            created_at     TEXT NOT NULL,
            note           TEXT,
            needs_review   INTEGER DEFAULT 0,
            ticket_action  TEXT,
            ticket_message TEXT
        )
    """)

    # 3) Daten rüberkopieren + Action mappen
    cur.execute("SELECT id,user_id,action,created_at,note,needs_review,ticket_action,ticket_message FROM bookings_old")
    rows = cur.fetchall()
    new_rows = []
    for i, uid, a, ts, note, nr, ta, tm in rows:
        a_new = MAP.get(a, "bin da")  # Fallback: "bin da"
        new_rows.append((i, uid, a_new, ts, note, nr, ta, tm))

    cur.executemany("""
        INSERT INTO bookings (id,user_id,action,created_at,note,needs_review,ticket_action,ticket_message)
        VALUES (?,?,?,?,?,?,?,?)
    """, new_rows)

    # 4) (optionale) Indizes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_created ON bookings(created_at)")

    # 5) Alte Tabelle weg
    cur.execute("DROP TABLE bookings_old")

    con.commit()
    con.close()
    print("OK: bookings migriert -> neue Actions:", ", ".join(NEW_ALLOWED))

if __name__ == "__main__":
    main()
