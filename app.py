from flask import flash
from flask import Flask, request, redirect, url_for, render_template, session
from flask import abort
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import sqlite3, os
from functools import wraps
import calendar
from datetime import date, datetime, timedelta  
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
LOCAL_TZ = ZoneInfo("Europe/Berlin")

def _parse_utc(dt_str: str) -> datetime:
    # SQLite speichert als "YYYY-MM-DD HH:MM:SS" in UTC (naiv) -> wir markieren als UTC
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

def to_local_str(dt_str_utc: str) -> str:
    """UTC-Text aus DB -> lokales ISO-ähnliches Format 'YYYY-MM-DD HH:MM:SS' in Europe/Berlin."""
    return _parse_utc(dt_str_utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")

def utc_window_for_local_day(d: date) -> tuple[str, str]:
    """Lokal 00:00..24:00 -> UTC-Grenzen als Strings für SQL BETWEEN/>=,<."""
    start_local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )

def utc_window_for_local_month(year: int, month: int) -> tuple[str, str]:
    start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=LOCAL_TZ)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=LOCAL_TZ)
    else:
        end_local = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=LOCAL_TZ)
    return (
        start_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


ACTIONS = [
    ("kommt", "Kommt"),
    ("geht", "Geht"),
    ("abwesend", "Abwesend"),
    ("wieder_da", "Wieder da"),
    ("pause", "Pause"),
    ("pausenende", "Pausenende"),
]


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
    # Users-Tabelle
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user'))
        )
    """)
    # Bookings-Tabelle
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('kommt','geht','abwesend','wieder_da','pause','pausenende')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            note TEXT,
            needs_review INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
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

def ensure_booking_ticket_fields():
    """Fügt Spalten für Ticket-Details hinzu, falls sie fehlen (SQLite ALTER TABLE)."""
    conn = get_db()
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(bookings)")}
    changed = False
    if "ticket_action" not in cols:
        conn.execute("ALTER TABLE bookings ADD COLUMN ticket_action TEXT CHECK(ticket_action IN ('aendern','loeschen'))")
        changed = True
    if "ticket_message" not in cols:
        conn.execute("ALTER TABLE bookings ADD COLUMN ticket_message TEXT")
        changed = True
    if changed:
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

@app.route("/dashboard", endpoint="dashboard")
def dashboard_alias():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if session.get("role") == "admin" else "user_only"))

# --- ADMIN: Dashboard (nur offene Tickets) ---
@app.route("/admin")
def admin_dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    conn = get_db()
    rows_raw = conn.execute("""
        SELECT b.id, b.user_id, b.action, b.created_at, b.note, b.needs_review,
               b.ticket_action, b.ticket_message,
               u.username
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        WHERE b.needs_review = 1
        ORDER BY b.created_at DESC, b.id DESC
    """).fetchall()
    conn.close()

    tickets = [{**dict(r), "local_created_at": to_local_str(r["created_at"])} for r in rows_raw]
    return render_template("admin.html", title="Chef-Dashboard", tickets=tickets, actions=ACTIONS)


# --- ADMIN: Ticket bearbeiten/auflösen ---
@app.post("/admin/resolve/<int:booking_id>")
def admin_resolve(booking_id: int):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    resolution = (request.form.get("resolution") or "").strip()       # 'aendern' | 'loeschen' | 'schliessen'
    new_action = (request.form.get("new_action") or "").strip()       # z. B. 'kommt', 'geht', ...
    admin_comment = (request.form.get("admin_comment") or "").strip()

    conn = get_db()

    # existierende Notiz holen (falls wir sie ergänzen wollen)
    row = conn.execute("SELECT note FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not row:
        conn.close()
        return redirect(url_for("admin_dashboard"))

    note_now = row["note"] or ""
    suffix = f" [Admin {session.get('username','?')}: {admin_comment}]" if admin_comment else f" [Admin {session.get('username','?')}]"

    if resolution == "loeschen":
        # Ticket per Löschung der Buchung lösen
        conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_dashboard"))

    elif resolution == "aendern":
        # Absicherung: nur erlaubte Actions
        valid_keys = {k for k, _label in ACTIONS}
        if new_action not in valid_keys:
            conn.close()
            return redirect(url_for("admin_dashboard"))

        new_note = (note_now + suffix).strip()
        conn.execute("""
            UPDATE bookings
               SET action = ?, note = ?, needs_review = 0, ticket_action = NULL, ticket_message = NULL
             WHERE id = ?
        """, (new_action, new_note, booking_id))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_dashboard"))

    else:
        # 'schliessen' (ohne Änderung/Löschung) – Ticket nur abhaken
        new_note = (note_now + suffix).strip() if admin_comment else note_now
        conn.execute("""
            UPDATE bookings
               SET note = ?, needs_review = 0, ticket_action = NULL, ticket_message = NULL
             WHERE id = ?
        """, (new_note, booking_id))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_dashboard"))


@app.route("/admin_only")
def admin_only():
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/ticket/<int:booking_id>/resolve", methods=["POST"])
@role_required("admin")
def admin_ticket_resolve(booking_id):
    conn = get_db()
    exists = conn.execute("SELECT id FROM bookings WHERE id = ? AND needs_review = 1", (booking_id,)).fetchone()
    if not exists:
        conn.close()
        return redirect(url_for("admin_only"))
    conn.execute("UPDATE bookings SET needs_review = 0 WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_only"))

@app.route("/user")
def user_only():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") not in ("user", "admin"):
        return redirect(url_for("unauthorized"))

    # --- Kalender-Parameter ---
    today = datetime.now(LOCAL_TZ).date()  # statt date.today(): TZ-sicher
    try:
        year = int(request.args.get("y", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.args.get("m", today.month))
    except (TypeError, ValueError):
        month = today.month
    if month < 1:
        month = 1
    if month > 12:
        month = 12

    d_arg = request.args.get("d")
    if d_arg:
        try:
            selected = date.fromisoformat(f"{year:04d}-{month:02d}-{int(d_arg):02d}")
        except Exception:
            selected = today
    else:
        selected = today if (year == today.year and month == today.month) else date(year, month, 1)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    # --- Monatsfenster in UTC, dann lokal gruppieren ---
    month_start_utc, month_end_utc = utc_window_for_local_month(year, month)

    conn = get_db()
    rows_for_counts = conn.execute(
        """
        SELECT created_at FROM bookings
        WHERE user_id = ?
          AND created_at >= ?
          AND created_at < ?
        """,
        (session["user_id"], month_start_utc, month_end_utc),
    ).fetchall()

    counts_by_day = {}
    for r in rows_for_counts:
        local_iso = to_local_str(r["created_at"])[:10]  # 'YYYY-MM-DD' (lokal)
        counts_by_day[local_iso] = counts_by_day.get(local_iso, 0) + 1

    # --- Wochen/Kacheln bauen ---
    weeks = []
    cal = calendar.Calendar(firstweekday=0)  # 0=Montag
    for w in cal.monthdatescalendar(year, month):
        row = []
        for day in w:
            iso_local = day.isoformat()
            row.append({
                "iso": iso_local,
                "day": day.day,
                "in_month": (day.month == month),
                "count": counts_by_day.get(iso_local, 0),
                "is_selected": (day == selected),
                "is_today": (day == today and day.month == month),  # heute (nur im Monat)
            })
        weeks.append(row)

    # --- Tagesliste (lokal) ---
    day_start_utc, day_end_utc = utc_window_for_local_day(selected)
    day_raw = conn.execute(
        """
        SELECT id, action, created_at, note, needs_review, ticket_action, ticket_message
        FROM bookings
        WHERE user_id = ?
          AND created_at >= ?
          AND created_at < ?
        ORDER BY created_at ASC, id ASC
        """,
        (session["user_id"], day_start_utc, day_end_utc),
    ).fetchall()
    day_rows = [{**dict(r), "local_created_at": to_local_str(r["created_at"])} for r in day_raw]

    # --- Letzte 5 ---
    last5_raw = conn.execute(
        """
        SELECT id, action, created_at, note, needs_review, ticket_action, ticket_message
        FROM bookings
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 5
        """,
        (session["user_id"],),
    ).fetchall()
    last5 = [{**dict(r), "local_created_at": to_local_str(r["created_at"])} for r in last5_raw]

    conn.close()

    return render_template(
        "user.html",
        title="User-Start",
        actions=ACTIONS,
        year=year, month=month,
        month_name=calendar.month_name[month],
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        selected_date=selected.isoformat(),
        weeks=weeks,
        day_bookings=day_rows,
        bookings=last5,
    )

@app.route("/unauthorized")
def unauthorized():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("unauthorized.html", title="Kein Zugriff")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/book", methods=["POST"])
def book():
    if "user_id" not in session:
        return redirect(url_for("login"))

    action = (request.form.get("action") or "").strip()
    note = (request.form.get("note") or "").strip()

    valid_keys = {k for k, _ in ACTIONS}
    if action not in valid_keys:
        return redirect(url_for("user_only"))

    conn = get_db()
    conn.execute(
        "INSERT INTO bookings (user_id, action, note) VALUES (?,?,?)",
        (session["user_id"], action, note if note else None)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("user_only"))

@app.route("/ticket/<int:booking_id>/close", methods=["POST"])
def ticket_close(booking_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    # User darf sein eigenes Ticket auch wieder schließen (optional)
    conn = get_db()
    row = conn.execute("SELECT id, user_id, needs_review FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not row or row["user_id"] != session["user_id"]:
        conn.close()
        return abort(403)
    if row["needs_review"] == 1:
        conn.execute("UPDATE bookings SET needs_review = 0 WHERE id = ?", (booking_id,))
        conn.commit()
    conn.close()
    return redirect(url_for("user_only"))

@app.route("/ticket/open", methods=["POST"])
def ticket_open_simple():
    if "user_id" not in session:
        return redirect(url_for("login"))

    # booking_id & Formularfelder aus dem Popup
    try:
        booking_id = int(request.form.get("booking_id", "0"))
    except ValueError:
        return redirect(url_for("user_only"))

    ticket_action = (request.form.get("ticket_action") or "").strip()  # "aendern" | "loeschen" | ""
    ticket_message = (request.form.get("ticket_message") or "").strip()

    # Sicherheitscheck: User darf nur eigene Buchungen flaggen
    conn = get_db()
    row = conn.execute("SELECT id, user_id FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not row or row["user_id"] != session["user_id"]:
        conn.close()
        return redirect(url_for("user_only"))

    # speichern
    conn.execute(
        "UPDATE bookings SET needs_review = 1, ticket_action = ?, ticket_message = ? WHERE id = ?",
        (ticket_action if ticket_action else None, ticket_message if ticket_message else None, booking_id)
    )
    conn.commit()
    conn.close()

    # Zurück zur User-Seite
    return redirect(url_for("user_only"))


if __name__ == "__main__":
    init_db()
    ensure_booking_ticket_fields()
    app.run(debug=True)

