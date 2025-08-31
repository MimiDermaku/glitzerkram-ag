# manage_users.py
import os, sqlite3, argparse
from getpass import getpass
from werkzeug.security import generate_password_hash  # kommt mit Flask/Werkzeug

BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "instance", "users.db")  # <— fester Pfad

def connect():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"DB nicht gefunden: {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def ensure_users_table(con):
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cur.fetchone():
        raise SystemExit("Tabelle 'users' existiert nicht (erst App/Bootstrap starten).")

def add_missing_columns(con):
    cur = con.cursor()
    cur.execute("PRAGMA table_info(users)")
    cols = {r[1] for r in cur.fetchall()}
    if "join_date" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN join_date TEXT")
    if "weekly_minutes" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN weekly_minutes INTEGER DEFAULT 2400")
    if "is_active" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    con.commit()

def user_exists(con, username) -> bool:
    cur = con.cursor()
    cur.execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)", (username,))
    return cur.fetchone() is not None

def cmd_add(args):
    con = connect(); ensure_users_table(con); add_missing_columns(con)
    if user_exists(con, args.username):
        con.close(); raise SystemExit(f"User '{args.username}' existiert bereits. Nutze 'passwd' zum Ändern des Passworts.")
    pw1 = getpass("Passwort: ")
    pw2 = getpass("Passwort (wiederholen): ")
    if pw1 != pw2:
        con.close(); raise SystemExit("Passwörter unterschiedlich.")
    phash = generate_password_hash(pw1)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO users (username, password_hash, role, join_date, weekly_minutes, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (args.username, phash, args.role, args.join, args.weekly, 0 if args.inactive else 1))
    con.commit(); con.close()
    print(f"OK: User '{args.username}' ({args.role}) angelegt.")

def cmd_passwd(args):
    con = connect(); ensure_users_table(con)
    if not user_exists(con, args.username):
        con.close(); raise SystemExit(f"User '{args.username}' nicht gefunden.")
    pw1 = getpass("Neues Passwort: "); pw2 = getpass("Neues Passwort (wiederholen): ")
    if pw1 != pw2:
        con.close(); raise SystemExit("Passwörter unterschiedlich.")
    phash = generate_password_hash(pw1)
    cur = con.cursor()
    cur.execute("UPDATE users SET password_hash=? WHERE LOWER(username)=LOWER(?)", (phash, args.username))
    con.commit(); con.close()
    print(f"OK: Passwort für '{args.username}' aktualisiert.")

def cmd_list(args):
    con = connect(); ensure_users_table(con); add_missing_columns(con)
    cur = con.cursor()
    cur.execute("SELECT id, username, role, COALESCE(join_date,''), COALESCE(weekly_minutes,0), COALESCE(is_active,1) FROM users ORDER BY username")
    rows = cur.fetchall(); con.close()
    if not rows:
        print("(keine Nutzer)")
        return
    for i,u,r,j,w,a in rows:
        print(f"[{i:>3}] {u:<20} role={r:<5} active={a} join={j} weekly_min={w}")

def cmd_meta(args):
    con = connect(); ensure_users_table(con); add_missing_columns(con)
    sets, params = [], []
    if args.join is not None:
        sets.append("join_date=?"); params.append(args.join)
    if args.weekly is not None:
        sets.append("weekly_minutes=?"); params.append(int(args.weekly))
    if args.active is not None:
        sets.append("is_active=?"); params.append(1 if args.active=="1" else 0)
    if not sets:
        con.close(); print("Nichts zu ändern."); return
    params.append(args.username)
    cur = con.cursor()
    cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE LOWER(username)=LOWER(?)", params)
    if cur.rowcount == 0:
        con.close(); raise SystemExit(f"User '{args.username}' nicht gefunden.")
    con.commit(); con.close()
    print(f"OK: Metadaten für '{args.username}' aktualisiert.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="User-Management (SQLite)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="User anlegen")
    p_add.add_argument("username")
    p_add.add_argument("--role", choices=["admin","user"], default="user")
    p_add.add_argument("--join", help="Eintrittsdatum YYYY-MM-DD")
    p_add.add_argument("--weekly", type=int, default=2400, help="Wochenminuten (z.B. 2400 = 40h)")
    p_add.add_argument("--inactive", action="store_true")
    p_add.set_defaults(func=cmd_add)

    p_pw = sub.add_parser("passwd", help="Passwort setzen/ändern")
    p_pw.add_argument("username")
    p_pw.set_defaults(func=cmd_passwd)

    p_ls = sub.add_parser("list", help="User auflisten")
    p_ls.set_defaults(func=cmd_list)

    p_meta = sub.add_parser("meta", help="Metadaten setzen")
    p_meta.add_argument("username")
    p_meta.add_argument("--join")
    p_meta.add_argument("--weekly", type=int)
    p_meta.add_argument("--active", choices=["0","1"], help="1=aktiv, 0=inaktiv")
    p_meta.set_defaults(func=cmd_meta)

    args = ap.parse_args()
    args.func(args)
