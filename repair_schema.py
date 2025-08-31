# repair_schema.py  (nicht-destruktive Schema-Reparatur)
import sqlite3, os, glob

# 1) DB finden – passe das bei Bedarf an
candidates = glob.glob(os.path.join("instance", "*.db")) + glob.glob("*.db")
if not candidates:
    raise SystemExit("Keine .db gefunden. Bitte DB-Pfad in repair_schema.py anpassen.")
DB_PATH = candidates[0]

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

def table_exists(name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (name,))
    return cur.fetchone() is not None

def column_names(table):
    cur.execute(f"PRAGMA table_info({table});")
    return [r[1] for r in cur.fetchall()]

# --- bookings: nur anlegen, wenn komplett fehlt ---
if not table_exists("bookings"):
    # Minimal-Schema, damit die App wieder lauffähig ist.
    # (Fügt NICHTS Bestehendes an, löscht NICHTS.)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        started_at TEXT,      -- ISO-String
        ended_at   TEXT,      -- ISO-String
        action     TEXT,      -- z.B. "work", "break", "drive" ...
        notes      TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

# Beispiel: wenn die App bestimmte Spalten erwartet, fügen wir sie bei Bedarf hinzu.
# (Nur falls Tabelle existiert, aber Spalte fehlt)
needed_cols = {
    "bookings": ["user_id","started_at","ended_at","action","notes","created_at"]
}
for tbl, cols in needed_cols.items():
    if table_exists(tbl):
        have = set(column_names(tbl))
        for col in cols:
            if col not in have:
                # konservatives Typing
                cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT;")
        conn.commit()

print(f"Schema-Check fertig. Verwendete DB: {DB_PATH}")
conn.close()
