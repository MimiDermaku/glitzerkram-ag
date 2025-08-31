import os, sqlite3

BASE = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE, "instance", "users.db")  # dein Pfad

con = sqlite3.connect(DB_PATH)
cur = con.cursor()
cur.execute("UPDATE bookings SET action='aktiv' WHERE action='kommt'")
con.commit()
con.close()
print("OK: action 'kommt' -> 'aktiv' umbenannt")
