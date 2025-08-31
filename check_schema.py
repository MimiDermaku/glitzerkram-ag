import sqlite3
con = sqlite3.connect(r".\instance\users.db")
print(con.execute("SELECT sql FROM sqlite_master WHERE name='bookings'").fetchone()[0])
con.close()
