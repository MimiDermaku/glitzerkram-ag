# bootstrap.py – erstellt Projektstruktur + Dateien
import os, textwrap, secrets

os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("instance", exist_ok=True)

# requirements
with open("requirements.txt", "w", encoding="utf-8") as f:
    f.write("Flask>=3.0\npython-dotenv>=1.0\n")

# .env mit SECRET_KEY
with open(".env", "w", encoding="utf-8") as f:
    f.write(f"SECRET_KEY={secrets.token_hex(32)}\n")

# CSS
with open("static/style.css", "w", encoding="utf-8") as f:
    f.write(textwrap.dedent("""
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 2rem; }
    .card { max-width: 440px; border: 1px solid #ddd; border-radius: 12px; padding: 1.2rem; box-shadow: 0 2px 10px rgba(0,0,0,.05); }
    label { display:block; margin-top:.6rem; }
    input { width: 100%; padding: .6rem; margin-top:.2rem; border-radius: 8px; border: 1px solid #ccc; }
    button { margin-top: 1rem; padding:.7rem 1rem; border:0; border-radius:10px; cursor:pointer; }
    .primary { background:#222; color:#fff; }
    .link { text-decoration:none; color:#0a58ca; }
    .topbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:1.2rem; }
    .muted { color:#666; font-size:.95rem; }
    .ok { color: #2e7d32; }
    .err { color: #c62828; }
    .grid { display:grid; gap:.8rem; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
    .box { padding:1rem; border:1px dashed #ccc; border-radius:10px; }
    """).strip()+"\n")

# Templates
base_html = """\
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>{{ title or "App" }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <div class="topbar">
    <div><strong>{{ title or "App" }}</strong></div>
    <div>
      {% if session.get('username') %}
        Angemeldet als <strong>{{ session.username }}</strong> ({{ session.role }}) |
        <a class="link" href="{{ url_for('logout') }}">Logout</a>
      {% endif %}
    </div>
  </div>
  {% block content %}{% endblock %}
</body>
</html>
"""

login_html = """\
{% extends "base.html" %}
{% block content %}
  <div class="card">
    <h2>Login</h2>
    <form method="POST">
      <label for="username">Nutzername</label>
      <input id="username" name="username" type="text" required autofocus>

      <label for="password">Passwort</label>
      <input id="password" name="password" type="password" required>

      <button class="primary" type="submit">Anmelden</button>
    </form>
    {% if error %}<p class="err">{{ error }}</p>{% endif %}
    <p class="muted">Demo: chef/secret123 (admin), mimi/geheim123 (user)</p>
  </div>
{% endblock %}
"""

admin_html = """\
{% extends "base.html" %}
{% block content %}
  <h2>Admin-Dashboard</h2>
  <p class="ok">Willkommen, {{ session.username }}! Du hast Admin-Rechte.</p>
  <div class="grid">
    <div class="box"><strong>Benutzerverwaltung</strong><br>(hier später User anlegen/löschen, Rollen ändern)</div>
    <div class="box"><strong>Reports</strong><br>(geschützte Auswertungen, Logs, ...)</div>
  </div>
{% endblock %}
"""

user_html = """\
{% extends "base.html" %}
{% block content %}
  <h2>User-Start</h2>
  <p class="ok">Hi {{ session.username }} 👋 – hier ist deine Nutzeransicht.</p>
  <div class="grid">
    <div class="box"><strong>Meine Aufgaben</strong><br>(Platzhalter für user-spezifische Inhalte)</div>
    <div class="box"><strong>Profil</strong><br>(Profil, Passwort ändern, ...)</div>
  </div>
{% endblock %}
"""

unauth_html = """\
{% extends "base.html" %}
{% block content %}
  <div class="card">
    <h3>Kein Zugriff</h3>
    <p class="err">Du bist eingeloggt, aber hast nicht die nötige Rolle für diese Seite.</p>
    <p><a class="link" href="{{ url_for('dashboard') }}">Zurück zum Dashboard</a></p>
  </div>
{% endblock %}
"""

for name, content in {
    "base.html": base_html,
    "login.html": login_html,
    "admin.html": admin_html,
    "user.html": user_html,
    "unauthorized.html": unauth_html,
}.items():
    with open(os.path.join("templates", name), "w", encoding="utf-8") as f:
        f.write(content)

# app.py
app_py = r'''from flask import Flask, request, redirect, url_for, render_template, session
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import sqlite3, os
from functools import wraps

load_dotenv()

app = Flask(__name__, instance_relative_config=True)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")

# ensure instance dir exists (SQLite liegt dort)
try:
    os.makedirs(app.instance_path, exist_ok=True)
except OSError:
    pass

DB_PATH = os.path.join(app.instance_path, "users.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    first_time = not os.path.exists(DB_PATH)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user'))
        )
    """)
    conn.commit()
    if first_time:
        seed = [
            ("chef", generate_password_hash("secret123"), "admin"),
            ("mimi", generate_password_hash("geheim123"), "user"),
        ]
        conn.executemany("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)", seed)
        conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                return redirect(url_for("unauthorized"))
            return f(*args, **kwargs)
        return wrapper
    return decorator

@app.route("/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        row = conn.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if row and check_password_hash(row["password_hash"], password):
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            session["role"] = row["role"]
            return redirect(url_for("dashboard"))
        else:
            error = "Falscher Nutzername oder Passwort."
    return render_template("login.html", error=error, title="Login")

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    role = session.get("role")
    if role == "admin":
        return render_template("admin.html", title="Admin-Dashboard")
    return render_template("user.html", title="User-Start")

@app.route("/admin")
def admin_only():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))
    return render_template("admin.html", title="Admin-Dashboard")

@app.route("/user")
def user_only():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") not in ("user","admin"):
        return redirect(url_for("unauthorized"))
    return render_template("user.html", title="User-Start")

@app.route("/unauthorized")
def unauthorized():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("unauthorized.html", title="Kein Zugriff")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
'''
with open("app.py", "w", encoding="utf-8") as f:
    f.write(app_py)

print("✅ Projekt aufgebaut.")
print("Nächste Schritte:")
print("  1) pip install -r requirements.txt")
print("  2) py app.py   (http://127.0.0.1:5000)")
print("Test-Logins: admin -> chef/secret123 | user -> mimi/geheim123")
