# set_weekly_minutes.py
import os, sqlite3, sys
BASE = os.path.dirname(__file__)
DB   = os.path.join(BASE, "instance", "users.db")

if len(sys.argv) != 3:
    raise SystemExit("Aufruf: set_weekly_minutes.py <username> <minuten_pro_woche>  (z.B. 2400)")

user = sys.argv[1]
mins = int(sys.argv[2])

con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute("UPDATE users SET weekly_minutes=? WHERE LOWER(username)=LOWER(?)", (mins, user))
con.commit()
print(f"OK: weekly_minutes für '{user}' -> {mins} (Betroffene Zeilen: {cur.rowcount})")
con.close()
