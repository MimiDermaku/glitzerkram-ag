import sqlite3, glob, os, sys
os.makedirs('seeds', exist_ok=True)
c = glob.glob(os.path.join('instance','*.db'))
if not c:
    print('Keine DB unter instance/*.db gefunden. Starte erst die App einmal oder nutze bootstrap.py.')
    sys.exit(1)
db = c[0]
con = sqlite3.connect(db)
with open('seeds/demo.sql','w',encoding='utf-8') as f:
    for line in con.iterdump():
        f.write(line+'\n')
con.close()
print('Export OK aus', db, '-> seeds/demo.sql')
