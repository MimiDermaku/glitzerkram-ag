from flask import Flask, request, redirect, url_for, render_template, session, make_response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import sqlite3, os, csv, io, calendar
from functools import wraps
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---- Aktionen (DB-Keys + Anzeige-Labels) ----
ACTIONS = [
    ("bin da",       "Bin da"),
    ("gehe",         "Gehe"),
    ("afk",          "Afk"),
    ("wieder da",    "Wieder da"),
    ("päuschen",     "Päuschen"),
    ("mache weiter", "Mache weiter"),
]
ACTION_LABELS = {
    "bin da": "Bin da", "kommt": "Bin da",
    "gehe": "Gehe", "geht": "Gehe",
    "afk": "AFK", "abwesend": "AFK",
    "wieder da": "Wieder da", "wieder_da": "Wieder da",
    "päuschen": "Päuschen", "pause": "Päuschen",
    "mache weiter": "Mache weiter", "pausenende": "Mache weiter",
}

load_dotenv()

# ---- Zeitzone: Europe/Berlin erzwingen (für SQLite localtime etc.) ----
os.environ.setdefault("TZ", "Europe/Berlin")
try:
    import time as _time
    _time.tzset()  # Unix (auch auf PythonAnywhere)
except Exception:
    pass
# -----------------------------------------------------------------------

app = Flask(__name__, instance_relative_config=True)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")

def _resolve_back_ep():
    for ep in ["admin_only", "admin_dashboard", "admin", "dashboard", "index"]:
        if ep in app.view_functions:
            return ep
    return "index"

# ---- Session-Decorator (ersetzt login_required) ----
def session_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# ensure instance dir exists (SQLite liegt dort)
try:
    os.makedirs(app.instance_path, exist_ok=True)
except OSError:
    pass

DB_PATH = os.path.join(app.instance_path, "users.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn

def init_db():
    first_time = not os.path.exists(DB_PATH)
    conn = get_db()

    # Users (mit weekly_minutes)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user')),
            weekly_minutes INTEGER DEFAULT 2400
        )
    """)

    # Journal
    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            entry_date TEXT NOT NULL,         -- YYYY-MM-DD
            content   TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_user_date ON journal_entries(user_id, entry_date)")

    # Bookings
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL,           -- UTC 'YYYY-MM-DD HH:MM:SS'
            note TEXT,
            needs_review INTEGER NOT NULL DEFAULT 0,
            ticket_action TEXT,                 -- 'aendern' | 'loeschen' | 'blank' | NULL
            ticket_message TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user_created ON bookings(user_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_review ON bookings(needs_review)")

    # Migration: weekly_minutes sicherstellen (alte DBs)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "weekly_minutes" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN weekly_minutes INTEGER")
    conn.execute("UPDATE users SET weekly_minutes = COALESCE(weekly_minutes, 2400)")
    conn.commit()

    # Seed nur bei frischer DB
    if first_time:
        seed = [
            ("chef", generate_password_hash("secret123"), "admin", 2400),
            ("mimi", generate_password_hash("geheim123"), "user", 2400),
        ]
        conn.executemany(
            "INSERT INTO users (username, password_hash, role, weekly_minutes) VALUES (?,?,?,?)",
            seed
        )
        conn.commit()
    conn.close()

# auch beim Import ausführen (WSGI)
try:
    init_db()
except Exception as e:
    print("init_db() failed:", e)

# ---- PERIODEN-HELPER --------------------------------------------------------
def _period_range_safe(period, anchor):
    p = (period or "month").lower()
    if p == "day":
        start = datetime.combine(anchor, datetime.min.time())
        end = start + timedelta(days=1)
    elif p == "week":
        start = datetime.combine(anchor - timedelta(days=anchor.weekday()), datetime.min.time())
        end = start + timedelta(days=7)
    elif p == "year":
        start = datetime(anchor.year, 1, 1)
        end = datetime(anchor.year + 1, 1, 1)
    else:  # month
        start = datetime(anchor.year, anchor.month, 1)
        end = datetime(anchor.year + (1 if anchor.month==12 else 0),
                       1 if anchor.month==12 else anchor.month+1, 1)
    return start, end

def _normalize_anchor(period, anchor):
    p = (period or "month").lower()
    if p == "day":
        return anchor
    elif p == "week":
        return anchor - timedelta(days=anchor.weekday())
    elif p == "year":
        return date(anchor.year, 1, 1)
    else:
        return date(anchor.year, anchor.month, 1)

def _iter_days_in_range(start_dt, end_dt):
    d = start_dt.date()
    last = (end_dt - timedelta(days=1)).date()
    while d <= last:
        yield d
        d += timedelta(days=1)

# ---- Wochen-Helper fürs Tagebuch --------------------------------------------
def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

def _week_days_mon_fri(anchor: date):
    mon = _monday_of(anchor)
    return [mon + timedelta(days=i) for i in range(5)]
# ----------------------------------------------------------------------------

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
        return redirect(url_for("admin_only"))
    return redirect(url_for("user_only"))

@app.route("/admin")
def admin_only():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    conn = get_db()
    rows = conn.execute("""
        SELECT
          b.id,
          CASE
            WHEN b.needs_review=1 AND IFNULL(b.ticket_action,'')='blank' THEN 'ticket'
            ELSE b.action
          END AS action,
          b.created_at, b.note, b.needs_review,
          b.ticket_action, b.ticket_message,
          u.username,
          datetime(b.created_at,'localtime') AS local_created_at
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        WHERE b.needs_review = 1
        ORDER BY b.created_at DESC, b.id DESC
    """).fetchall()
    conn.close()

    return render_template(
        "admin.html",
        title="Admin-Dashboard",
        tickets=rows,
        actions=ACTIONS
    )

@app.route("/admin/reports")
def admin_reports():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    back_ep = _resolve_back_ep()

    conn = get_db()
    users = conn.execute("SELECT id, username FROM users ORDER BY username").fetchall()

    today = date.today()
    raw_period = (request.args.get("period") or "month").lower()

    try:
        year = int(request.args.get("y", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.args.get("m", today.month))
    except (TypeError, ValueError):
        month = today.month
    month = min(12, max(1, month))

    try:
        uid = int(request.args.get("uid", users[0]["id"] if users else 0))
    except (TypeError, ValueError, IndexError):
        uid = 0

    if not users or uid == 0:
        conn.close()
        return render_template(
            "reports.html",
            title="Reports",
            users=users, uid=uid,
            year=year, month=month,
            month_name=["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"][month-1],
            rows=[], sums={"work":0,"breaks":0,"afk":0,"net":0},
            weekly_minutes=None, soll_minutes=0, delta_minutes=0, username="",
            back_ep=back_ep,
            anchor_norm_iso=f"{year:04d}-{month:02d}-01"
        )

    date_arg = request.args.get("date")
    try:
        anchor_raw = datetime.strptime(date_arg, "%Y-%m-%d").date() if date_arg else date(year, month, 1)
    except ValueError:
        anchor_raw = date(year, month, 1)

    effective_period = raw_period if raw_period in {"day","week","month","year"} else "month"
    anchor_norm = _normalize_anchor(effective_period, anchor_raw)
    start_dt, end_dt = _period_range_safe(effective_period, anchor_norm)

    year = start_dt.year
    month = start_dt.month

    _MONATE = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]
    month_name = _MONATE[month-1]

    start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end   = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn2 = get_db()
    row_user = conn2.execute(
        "SELECT username, COALESCE(weekly_minutes, 2400) AS wm FROM users WHERE id = ?",
        (uid,)
    ).fetchone()
    username        = row_user["username"]
    weekly_minutes  = int(row_user["wm"])
    conn2.close()

    # Buchungen (Blanko-Tickets ausschließen!)
    conn = get_db()
    rows = conn.execute("""
        SELECT
          action,
          datetime(created_at,'localtime') AS ts_local,
          date(datetime(created_at,'localtime')) AS d_local
        FROM bookings
        WHERE user_id = ?
          AND datetime(created_at) >= ?
          AND datetime(created_at) <  ?
          AND NOT (needs_review=1 AND IFNULL(ticket_action,'')='blank')
        ORDER BY created_at ASC, id ASC
    """, (uid, start, end)).fetchall()
    conn.close()

    by_day = {}
    for r in rows:
        d = r["d_local"]
        by_day.setdefault(d, []).append((r["ts_local"], r["action"]))

    result = []
    sums = {"work":0,"breaks":0,"afk":0,"net":0}
    for d in _iter_days_in_range(start_dt, end_dt):
        dstr = d.strftime("%Y-%m-%d")
        evts = by_day.get(dstr, [])
        metrics = compute_day_minutes(evts)
        for k in ("work","breaks","afk","net"):
            sums[k] += metrics[k]
        result.append({
            "date": dstr,
            "work": metrics["work"],
            "breaks": metrics["breaks"],
            "afk": metrics["afk"],
            "net": metrics["net"],
            "flags": metrics["flags"],
        })

    workdays = sum(1 for d in _iter_days_in_range(start_dt, end_dt) if d.weekday() < 5)
    daily_target = weekly_minutes / 5.0
    soll_minutes = int(round(daily_target * workdays))
    delta_minutes = sums["net"] - soll_minutes

    return render_template(
        "reports.html",
        title="Reports",
        users=users, uid=uid,
        year=year, month=month, month_name=month_name,
        rows=result, sums=sums,
        weekly_minutes=weekly_minutes,
        soll_minutes=soll_minutes, delta_minutes=delta_minutes,
        username=username,
        back_ep=back_ep,
        anchor_norm_iso=anchor_norm.isoformat()
    )

# ---------- CSV-Export ----------
def _fmt_hhmm(mins: int) -> str:
    s = "-" if mins < 0 else ""
    m = abs(int(mins))
    h = m // 60
    mm = m % 60
    return f"{s}{h}:{mm:02d}"

@app.get("/admin/reports/export")
def admin_reports_export():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    conn = get_db()
    users = conn.execute("SELECT id, username FROM users ORDER BY username").fetchall()

    today = date.today()
    raw_period = (request.args.get("period") or "month").lower()
    effective_period = raw_period if raw_period in {"day","week","month","year"} else "month"

    try:
        year = int(request.args.get("y", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.args.get("m", today.month))
    except (TypeError, ValueError):
        month = today.month
    month = min(12, max(1, month))
    try:
        uid = int(request.args.get("uid", users[0]["id"] if users else 0))
    except (TypeError, ValueError, IndexError):
        uid = 0

    if not users or uid == 0:
        conn.close()
        return ("Kein Benutzer gewählt", 400)

    chronik = (request.args.get("chronik") or "all").lower()
    if chronik not in {"all", "over", "under", "afk"}:
        chronik = "all"

    date_arg = request.args.get("date")
    try:
        anchor_raw = datetime.strptime(date_arg, "%Y-%m-%d").date() if date_arg else date(year, month, 1)
    except ValueError:
        anchor_raw = date(year, month, 1)

    anchor_norm = _normalize_anchor(effective_period, anchor_raw)
    start_dt, end_dt = _period_range_safe(effective_period, anchor_norm)

    row_user = conn.execute(
        "SELECT username, COALESCE(weekly_minutes, 2400) AS wm FROM users WHERE id = ?",
        (uid,)
    ).fetchone()
    username        = row_user["username"]
    weekly_minutes  = int(row_user["wm"])

    start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end   = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("""
        SELECT
          action,
          datetime(created_at,'localtime') AS ts_local,
          date(datetime(created_at,'localtime')) AS d_local
        FROM bookings
        WHERE user_id = ?
          AND datetime(created_at) >= ?
          AND datetime(created_at) <  ?
          AND NOT (needs_review=1 AND IFNULL(ticket_action,'')='blank')
        ORDER BY created_at ASC, id ASC
    """, (uid, start, end)).fetchall()
    conn.close()

    by_day = {}
    for r in rows:
        d = r["d_local"]
        by_day.setdefault(d, []).append((r["ts_local"], r["action"]))

    daily_target = weekly_minutes / 5.0

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow(["User", username])
    writer.writerow(["Zeitraum", f"{start_dt.date()} bis {end_dt.date()}"])
    if chronik == "over":
        writer.writerow(["Filter", "Überstunden (Δ > 0)"])
    elif chronik == "under":
        writer.writerow(["Filter", "Fehlstunden (Δ < 0)"])
    elif chronik == "afk":
        writer.writerow(["Filter", "AFK-Tage (AFK > 0)"])
    else:
        writer.writerow(["Filter", "Alle Tage"])
    writer.writerow([])

    writer.writerow([
        "Datum",
        "Arbeit_min","Arbeit_hhmm",
        "Pausen_min","Pausen_hhmm",
        "AFK_min","AFK_hhmm",
        "Netto_min","Netto_hhmm",
        "Soll_min","Soll_hhmm",
        "Delta_min","Delta_hhmm"
    ])
    sum_work = sum_breaks = sum_afk = sum_net = sum_soll = sum_delta = 0

    for d in _iter_days_in_range(start_dt, end_dt):
        dstr = d.strftime("%Y-%m-%d")
        evts = by_day.get(dstr, [])
        m = compute_day_minutes(evts)

        is_weekday = d.weekday() < 5
        soll = int(round(daily_target)) if is_weekday else 0
        delta = m["net"] - soll

        include_row = True
        if chronik == "over" and delta <= 0:
            include_row = False
        elif chronik == "under" and delta >= 0:
            include_row = False
        elif chronik == "afk" and m["afk"] <= 0:
            include_row = False
        if not include_row:
            continue

        sum_work   += m["work"]
        sum_breaks += m["breaks"]
        sum_afk    += m["afk"]
        sum_net    += m["net"]
        sum_soll   += soll
        sum_delta  += delta

        writer.writerow([
            dstr,
            m["work"], _fmt_hhmm(m["work"]),
            m["breaks"], _fmt_hhmm(m["breaks"]),
            m["afk"], _fmt_hhmm(m["afk"]),
            m["net"], _fmt_hhmm(m["net"]),
            soll, _fmt_hhmm(soll),
            delta, ("+" if delta>=0 else "") + _fmt_hhmm(delta)
        ])

    writer.writerow([
        "SUMME",
        sum_work,   _fmt_hhmm(sum_work),
        sum_breaks, _fmt_hhmm(sum_breaks),
        sum_afk,    _fmt_hhmm(sum_afk),
        sum_net,    _fmt_hhmm(sum_net),
        sum_soll,   _fmt_hhmm(sum_soll),
        sum_delta,  ("+" if sum_delta>=0 else "") + _fmt_hhmm(sum_delta)
    ])

    csv_data = output.getvalue().encode("utf-8-sig")
    suffix = "" if chronik == "all" else f"_{chronik}"
    filename = f"report_{username}_{effective_period}_{start_dt.date()}_{end_dt.date()}{suffix}.csv"
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename=\"{filename}\"'
    return resp
# ---------- Ende: CSV-Export ----------

# ========== Präsenz-Seite ==========
@app.route("/presence")
def presence():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    rows = conn.execute("""
        SELECT
          u.id AS user_id,
          u.username,
          (SELECT created_at FROM bookings WHERE user_id=u.id AND action IN ('bin da','kommt') ORDER BY created_at DESC, id DESC LIMIT 1) AS last_start,
          (SELECT created_at FROM bookings WHERE user_id=u.id AND action IN ('gehe','geht') ORDER BY created_at DESC, id DESC LIMIT 1) AS last_end,
          (SELECT action FROM bookings
             WHERE user_id=u.id
               AND NOT (needs_review=1 AND IFNULL(ticket_action,'')='blank')
             ORDER BY created_at DESC, id DESC LIMIT 1) AS last_action,
          (SELECT datetime(created_at,'localtime') FROM bookings
             WHERE user_id=u.id
               AND NOT (needs_review=1 AND IFNULL(ticket_action,'')='blank')
             ORDER BY created_at DESC, id DESC LIMIT 1) AS last_action_local,
          (SELECT datetime(created_at,'localtime') FROM bookings WHERE user_id=u.id AND action IN ('bin da','kommt') ORDER BY created_at DESC, id DESC LIMIT 1) AS last_start_local
        FROM users u
        ORDER BY u.username
    """).fetchall()
    conn.close()

    present = []
    for r in rows:
        last_start = r["last_start"]
        last_end   = r["last_end"]
        is_present = bool(last_start) and (not last_end or last_start > last_end)
        if not is_present:
            continue
        action_key = r["last_action"] or ""
        status_label = ACTION_LABELS.get(action_key, action_key or "—")
        present.append({
            "username": r["username"],
            "since_local": r["last_start_local"],
            "last_action_label": status_label,
            "last_action_local": r["last_action_local"],
        })

    present.sort(key=lambda x: x["since_local"] or "", reverse=True)

    return render_template(
        "presence.html",
        title="Wer ist da?",
        present=present,
        count=len(present),
    )

# --- JSON für sanften Auto-Refresh ---
@app.get("/presence.json")
def presence_json():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    rows = conn.execute("""
        SELECT
          u.id AS user_id,
          u.username,
          (SELECT created_at FROM bookings WHERE user_id=u.id AND action IN ('bin da','kommt') ORDER BY created_at DESC, id DESC LIMIT 1) AS last_start,
          (SELECT created_at FROM bookings WHERE user_id=u.id AND action IN ('gehe','geht') ORDER BY created_at DESC, id DESC LIMIT 1) AS last_end,
          (SELECT action FROM bookings
             WHERE user_id=u.id
               AND NOT (needs_review=1 AND IFNULL(ticket_action,'')='blank')
             ORDER BY created_at DESC, id DESC LIMIT 1) AS last_action,
          (SELECT datetime(created_at,'localtime') FROM bookings
             WHERE user_id=u.id
               AND NOT (needs_review=1 AND IFNULL(ticket_action,'')='blank')
             ORDER BY created_at DESC, id DESC LIMIT 1) AS last_action_local,
          (SELECT datetime(created_at,'localtime') FROM bookings WHERE user_id=u.id AND action IN ('bin da','kommt') ORDER BY created_at DESC, id DESC LIMIT 1) AS last_start_local
        FROM users u
        ORDER BY u.username
    """).fetchall()
    conn.close()

    present = []
    for r in rows:
        last_start = r["last_start"]
        last_end   = r["last_end"]
        is_present = bool(last_start) and (not last_end or last_start > last_end)
        if not is_present:
            continue
        action_key = r["last_action"] or ""
        status_label = ACTION_LABELS.get(action_key, action_key or "—")
        present.append({
            "username": r["username"],
            "since_local": r["last_start_local"],
            "last_action_label": status_label,
            "last_action_local": r["last_action_local"],
        })

    present.sort(key=lambda x: x["since_local"] or "", reverse=True)
    return jsonify({
        "count": len(present),
        "present": present,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
# ========== Ende Präsenz-Seite ==========

# ----------------- ADMIN: Ticket lösen/ändern -----------------
@app.post("/admin/resolve/<int:booking_id>", endpoint="admin_resolve")
def admin_resolve(booking_id: int):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    resolution     = (request.form.get("resolution") or "").strip()
    new_action     = (request.form.get("new_action") or "").strip()
    admin_comment  = (request.form.get("admin_comment") or "").strip()

    new_date_raw   = (request.form.get("new_date") or "").strip()
    new_time_raw   = (request.form.get("new_time") or "").strip()
    new_note_raw   = (request.form.get("new_note") or "").strip()

    conn = get_db()
    cur  = conn.cursor()

    if resolution == "loeschen":
        cur.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_only"))

    elif resolution == "aendern":
        row = cur.execute(
            "SELECT action, note, created_at FROM bookings WHERE id = ?",
            (booking_id,)
        ).fetchone()
        if not row:
            conn.close()
            return ("Buchung nicht gefunden", 404)

        action_final = row["action"]
        if new_action:
            valid_actions = {k for k, _ in ACTIONS}
            if new_action not in valid_actions:
                conn.close()
                return ("Ungültige Aktion", 400)
            action_final = new_action

        note_final = new_note_raw if new_note_raw else (row["note"] or "")

        created_at_final = row["created_at"]
        if new_date_raw or new_time_raw:
            try:
                res = cur.execute(
                    "SELECT strftime('%Y-%m-%d %H:%M:%S', created_at, 'localtime') FROM bookings WHERE id = ?",
                    (booking_id,)
                ).fetchone()
                old_local_str = res[0] if res else None
                if not old_local_str:
                    raise ValueError("no old local ts")

                y, M, d = map(int, old_local_str[0:10].split("-"))
                hh, mm, ss = map(int, old_local_str[11:19].split(":"))

                if new_date_raw:
                    try:
                        y2, M2, d2 = [int(x) for x in new_date_raw.split("-")]
                        y, M, d = y2, M2, d2
                    except Exception:
                        conn.close()
                        return ("Ungültiges Datum (erwarte YYYY-MM-DD)", 400)

                if new_time_raw:
                    try:
                        tparts = [int(x) for x in new_time_raw.split(":")]
                        if   len(tparts) == 2: hh, mm = tparts; ss = 0
                        elif len(tparts) == 3: hh, mm, ss = tparts
                        else: raise ValueError
                        if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                            raise ValueError
                    except Exception:
                        conn.close()
                        return ("Ungültige Uhrzeit (erwarte HH:MM oder HH:MM:SS)", 400)

                new_local_str = f"{y:04d}-{M:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}"
                utc_row = cur.execute("SELECT datetime(?,'utc')", (new_local_str,)).fetchone()
                if not utc_row or not utc_row[0]:
                    conn.close()
                    return ("Ungültiges Datum/Uhrzeit-Format", 400)
                created_at_final = utc_row[0]
            except Exception:
                conn.close()
                return ("Ungültiges Datum/Uhrzeit-Format", 400)

        if admin_comment:
            note_final = (note_final + " " if note_final else "") + f"[Admin: {admin_comment}]"

        cur.execute("""
            UPDATE bookings
               SET action = ?,
                   created_at = ?,
                   note = ?,
                   needs_review = 0,
                   ticket_action = NULL,
                   ticket_message = NULL
             WHERE id = ?
        """, (action_final, created_at_final, note_final, booking_id))

        conn.commit()
        conn.close()
        return redirect(url_for("admin_only"))

    else:
        row = cur.execute("SELECT ticket_action FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if row and (row["ticket_action"] or "") == "blank":
            cur.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        else:
            cur.execute("""
                UPDATE bookings
                   SET needs_review = 0,
                       ticket_action = NULL,
                       ticket_message = NULL
                 WHERE id = ?
            """, (booking_id,))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_only"))

# ----------------- USER: Startseite -----------------
@app.route("/user")
def user_only():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") not in ("user", "admin"):
        return redirect(url_for("unauthorized"))

    uid = session["user_id"]
    today = date.today()

    try:
        year = int(request.args.get("y", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.args.get("m", today.month))
    except (TypeError, ValueError):
        month = today.month
    month = min(12, max(1, month))

    if month == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, month - 1
    if month == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, month + 1

    d_arg = request.args.get("d")
    if d_arg and len(d_arg) == 2 and d_arg.isdigit():
        sel_day = int(d_arg)
    else:
        sel_day = today.day if (today.year == year and today.month == month) else 1
    if sel_day < 1: sel_day = 1
    if sel_day > 31: sel_day = 31

    month_start = f"{year:04d}-{month:02d}-01 00:00:00"
    month_end   = f"{next_y:04d}-{next_m:02d}-01 00:00:00"

    selected_iso = f"{year:04d}-{month:02d}-{sel_day:02d}"

    conn = get_db()

    # Letzte 5 — Blanko-Tickets als "ticket" labeln
    last5 = conn.execute("""
        SELECT
          id,
          CASE
            WHEN needs_review=1 AND IFNULL(ticket_action,'')='blank' THEN 'ticket'
            ELSE action
          END AS action,
          note, needs_review, ticket_action, ticket_message,
          datetime(created_at, 'localtime') AS local_created_at
        FROM bookings
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 5
    """, (uid,)).fetchall()

    rows = conn.execute("""
        SELECT date(datetime(created_at,'localtime')) AS d, COUNT(*) AS c
        FROM bookings
        WHERE user_id = ?
          AND datetime(created_at) >= ?
          AND datetime(created_at) <  ?
        GROUP BY d
    """, (uid, month_start, month_end)).fetchall()
    counts_by_day = {r["d"]: r["c"] for r in rows}

    # Tagesliste — Blanko-Tickets als "ticket" labeln
    day_rows = conn.execute("""
        SELECT
          id,
          CASE
            WHEN needs_review=1 AND IFNULL(ticket_action,'')='blank' THEN 'ticket'
            ELSE action
          END AS action,
          note, needs_review, ticket_action, ticket_message,
          datetime(created_at,'localtime') AS local_created_at
        FROM bookings
        WHERE user_id = ?
          AND date(datetime(created_at,'localtime')) = date(?, 'localtime')
        ORDER BY created_at ASC, id ASC
    """, (uid, selected_iso)).fetchall()

    conn.close()

    weeks = []
    cal = calendar.Calendar(firstweekday=0)
    today_iso = today.isoformat()
    for w in cal.monthdatescalendar(year, month):
        row = []
        for day in w:
            iso_local = day.isoformat()
            row.append({
                "iso": iso_local,
                "day": day.day,
                "in_month": (day.month == month),
                "count": counts_by_day.get(iso_local, 0),
                "is_selected": (iso_local == selected_iso),
                "is_today": (iso_local == today_iso),
            })
        weeks.append(row)

    MONATSNAMEN = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]
    month_name = MONATSNAMEN[month-1]

    return render_template(
        "user.html",
        title="User-Start",
        actions=ACTIONS,
        bookings=last5,
        weeks=weeks,
        month_name=month_name,
        year=year,
        selected_date=selected_iso,
        day_bookings=day_rows,
        prev_y=prev_y, prev_m=prev_m,
        next_y=next_y, next_m=next_m,
        hide_title=True,
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

# --- Minimal-Route: /book ---
@app.post("/book", endpoint="book")
def book():
    if "user_id" not in session:
        return redirect(url_for("login"))

    action = (request.form.get("action") or "").strip()
    note   = (request.form.get("note") or "").strip()

    if not action:
        return ("Aktion fehlt", 400)

    MAP_NEW_TO_OLD = {
        "bin da": "kommt",
        "gehe": "geht",
        "afk": "abwesend",
        "wieder da": "wieder_da",
        "päuschen": "pause",
        "mache weiter": "pausenende",
    }

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO bookings (user_id, action, created_at, note) VALUES (?,?,datetime('now'),?)",
            (session["user_id"], action, note),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        mapped = MAP_NEW_TO_OLD.get(action)
        if not mapped:
            conn.close()
            return ("Ungültige Aktion", 400)
        conn.execute(
            "INSERT INTO bookings (user_id, action, created_at, note) VALUES (?,?,datetime('now'),?)",
            (session["user_id"], mapped, note),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("user_only"))
    conn.close()
    return redirect(url_for("user_only"))
# -----------------------------------------------------------------

# Ticket eröffnen (bestehende Buchung)
@app.post("/ticket/open", endpoint="ticket_open_simple")
def ticket_open_simple():
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        booking_id = int((request.form.get("booking_id") or "").strip())
    except (TypeError, ValueError):
        return ("Ungültige Buchungs-ID", 400)

    ticket_action = (request.form.get("ticket_action") or "").strip()
    if ticket_action not in ("", "aendern", "loeschen"):
        return ("Ungültige Ticket-Aktion", 400)

    ticket_message = (request.form.get("ticket_message") or "").strip()

    conn = get_db()
    conn.execute(
        """
        UPDATE bookings
           SET needs_review  = 1,
               ticket_action  = NULLIF(?, '' ),
               ticket_message = NULLIF(?, '' )
         WHERE id = ? AND user_id = ?
        """,
        (ticket_action, ticket_message, booking_id, session["user_id"]),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("user_only"))

# Ticket schließen (vom User)
@app.post("/ticket/close/<int:booking_id>", endpoint="ticket_close")
def ticket_close(booking_id: int):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    row = conn.execute(
        "SELECT ticket_action FROM bookings WHERE id = ? AND user_id = ?",
        (booking_id, session["user_id"])
    ).fetchone()
    if row and (row["ticket_action"] or "") == "blank":
        conn.execute("DELETE FROM bookings WHERE id = ? AND user_id = ?", (booking_id, session["user_id"]))
    else:
        conn.execute(
            "UPDATE bookings SET needs_review = 0, ticket_action=NULL, ticket_message=NULL WHERE id = ? AND user_id = ?",
            (booking_id, session["user_id"]),
        )
    conn.commit()
    conn.close()
    return redirect(url_for("user_only"))

# --- ZENTRALER BLANKO-TICKET-BUTTON ---
@app.post("/ticket/open_blank", endpoint="ticket_open_blank")
def ticket_open_blank():
    if "user_id" not in session:
        return redirect(url_for("login"))

    message = (request.form.get("message") or "").strip()
    if not message:
        return ("Bitte eine kurze Beschreibung angeben.", 400)

    wish_action = (request.form.get("wish_action") or "").strip()
    wish_date   = (request.form.get("wish_date") or "").strip()
    wish_time   = (request.form.get("wish_time") or "").strip()

    parts = []
    if wish_action: parts.append(f"Wunsch-Aktion: {wish_action}")
    if wish_date:   parts.append(f"Datum: {wish_date}")
    if wish_time:   parts.append(f"Uhrzeit: {wish_time}")
    ticket_msg = message + ((" | " + " · ".join(parts)) if parts else "")

    conn = get_db()
    conn.execute("""
        INSERT INTO bookings (user_id, action, created_at, note, needs_review, ticket_action, ticket_message)
        VALUES (?, 'mache weiter', datetime('now'), '', 1, 'blank', ?)
    """, (session["user_id"], ticket_msg))
    conn.commit()
    conn.close()
    return redirect(url_for("user_only"))
# ---------------------------------------------------------------------------

# ---------------------- TAGEBUCH: Wochenansicht (User) -----------------------
@app.get("/journal")
@session_required
def journal():
    """Wochenansicht Mo–Fr mit Einträgen des eingeloggten Users."""
    uid = session["user_id"]
    date_arg = request.args.get("date")
    try:
        anchor = datetime.strptime(date_arg, "%Y-%m-%d").date() if date_arg else date.today()
    except ValueError:
        anchor = date.today()

    days = _week_days_mon_fri(anchor)
    mon = days[0]
    fri = days[-1]

    conn = get_db()
    rows = conn.execute("""
        SELECT id, entry_date, content,
               datetime(created_at,'localtime') AS created_local
        FROM journal_entries
        WHERE user_id = ?
          AND entry_date >= ?
          AND entry_date <= ?
        ORDER BY entry_date ASC, created_at ASC, id ASC
    """, (uid, mon.isoformat(), fri.isoformat())).fetchall()
    conn.close()

    by_date = {}
    for r in rows:
        by_date.setdefault(r["entry_date"], []).append(r)

    prev_week = (mon - timedelta(days=7)).isoformat()
    next_week = (mon + timedelta(days=7)).isoformat()

    kw = mon.isocalendar()[1]
    title_range = f"{mon.strftime('%d.%m.%Y')} – {fri.strftime('%d.%m.%Y')} · KW {kw}"

    return render_template(
        "journal.html",
        title="Tagebuch",
        week_days=days,
        entries_by_date=by_date,
        monday_iso=mon.isoformat(),
        prev_week_date=prev_week,
        next_week_date=next_week,
        title_range=title_range,
    )

@app.post("/journal/add")
@session_required
def journal_add():
    """Einen Stichpunkt für einen Tag (YYYY-MM-DD) hinzufügen."""
    uid = session["user_id"]
    entry_date = (request.form.get("entry_date") or "").strip()
    content = (request.form.get("content") or "").strip()
    if not entry_date:
        return ("Datum fehlt", 400)
    try:
        d = datetime.strptime(entry_date, "%Y-%m-%d").date()
    except ValueError:
        return ("Ungültiges Datum (YYYY-MM-DD)", 400)
    if not content:
        return ("Bitte einen Stichpunkt eingeben.", 400)
    if len(content) > 500:
        content = content[:500]

    conn = get_db()
    conn.execute("""
        INSERT INTO journal_entries (user_id, entry_date, content)
        VALUES (?, ?, ?)
    """, (uid, d.isoformat(), content))
    conn.commit()
    conn.close()

    return redirect(url_for("journal", date=_monday_of(d).isoformat()))

@app.post("/journal/delete/<int:entry_id>")
@session_required
def journal_delete(entry_id: int):
    """Eigenen Tagebuch-Eintrag löschen."""
    uid = session["user_id"]
    conn = get_db()
    row = conn.execute("SELECT entry_date FROM journal_entries WHERE id=? AND user_id=?", (entry_id, uid)).fetchone()
    if not row:
        conn.close()
        return redirect(url_for("journal"))
    entry_date = row["entry_date"]
    conn.execute("DELETE FROM journal_entries WHERE id=? AND user_id=?", (entry_id, uid))
    conn.commit()
    conn.close()
    try:
        d = datetime.strptime(entry_date, "%Y-%m-%d").date()
        monday = _monday_of(d).isoformat()
    except Exception:
        monday = None
    return redirect(url_for("journal", date=monday) if monday else url_for("journal"))
# ------------------- Ende Tagebuch (User) ------------------------------------

# ------------------- ADMIN: Tagebuch-Ansicht + Export ------------------------
@app.get("/admin/journal")
def admin_journal():
    """Admin-Ansicht: Wochenraster Mo–Fr für einen gewählten User."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    conn = get_db()
    users = conn.execute("SELECT id, username FROM users ORDER BY username").fetchall()
    if not users:
        conn.close()
        return render_template("admin_journal.html", title="Tagebuch (Admin)", users=[], uid=0, entries_by_date={}, week_days=[])

    try:
        uid = int(request.args.get("uid", users[0]["id"]))
    except (TypeError, ValueError):
        uid = users[0]["id"]

    date_arg = request.args.get("date")
    try:
        anchor = datetime.strptime(date_arg, "%Y-%m-%d").date() if date_arg else date.today()
    except ValueError:
        anchor = date.today()

    days = _week_days_mon_fri(anchor)
    mon = days[0]
    fri = days[-1]

    rows = conn.execute("""
        SELECT id, entry_date, content,
               datetime(created_at,'localtime') AS created_local
        FROM journal_entries
        WHERE user_id = ?
          AND entry_date >= ?
          AND entry_date <= ?
        ORDER BY entry_date ASC, created_at ASC, id ASC
    """, (uid, mon.isoformat(), fri.isoformat())).fetchall()

    username = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()["username"]
    conn.close()

    by_date = {}
    for r in rows:
        by_date.setdefault(r["entry_date"], []).append(r)

    prev_week = (mon - timedelta(days=7)).isoformat()
    next_week = (mon + timedelta(days=7)).isoformat()
    kw = mon.isocalendar()[1]
    title_range = f"{mon.strftime('%d.%m.%Y')} – {fri.strftime('%d.%m.%Y')} · KW {kw}"

    return render_template(
        "admin_journal.html",
        title="Tagebuch (Admin)",
        users=users,
        uid=uid,
        username=username,
        week_days=days,
        entries_by_date=by_date,
        monday_iso=mon.isoformat(),
        prev_week_date=prev_week,
        next_week_date=next_week,
        title_range=title_range,
    )

@app.get("/admin/journal/export")
def admin_journal_export():
    """CSV-Export der sichtbaren Woche für einen User."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    conn = get_db()
    try:
        uid = int(request.args.get("uid", "0"))
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        conn.close()
        return ("uid fehlt/ungültig", 400)

    date_arg = request.args.get("date")
    try:
        anchor = datetime.strptime(date_arg, "%Y-%m-%d").date() if date_arg else date.today()
    except ValueError:
        anchor = date.today()

    days = _week_days_mon_fri(anchor)
    mon = days[0]
    fri = days[-1]

    username_row = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    if not username_row:
        conn.close()
        return ("User nicht gefunden", 404)
    username = username_row["username"]

    rows = conn.execute("""
        SELECT entry_date,
               datetime(created_at,'localtime') AS created_local,
               content
        FROM journal_entries
        WHERE user_id = ?
          AND entry_date >= ?
          AND entry_date <= ?
        ORDER BY entry_date ASC, created_at ASC, id ASC
    """, (uid, mon.isoformat(), fri.isoformat())).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["User", username])
    writer.writerow(["Zeitraum", f"{mon.isoformat()} bis {fri.isoformat()}"])
    writer.writerow([])

    writer.writerow(["Datum", "Erstellt (lokal)", "Inhalt"])
    for r in rows:
        writer.writerow([r["entry_date"], r["created_local"], r["content"]])

    csv_data = output.getvalue().encode("utf-8-sig")
    filename = f"journal_{username}_{mon.isoformat()}_{fri.isoformat()}.csv"
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename=\"{filename}\"'
    return resp
# ------------------- Ende Admin: Tagebuch ------------------------------------

# ------------------- ADMIN: Benutzerverwaltung -------------------------------
def _parse_minutes(v, default=2400):
    try:
        iv = int(str(v).strip())
        if iv < 0: iv = 0
        return iv
    except Exception:
        return default

@app.get("/admin/users")
def admin_users():
    """Liste & Pflege der Benutzer (Admin)."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    conn = get_db()
    users = conn.execute("""
        SELECT id, username, role, COALESCE(weekly_minutes,2400) AS weekly_minutes
        FROM users
        ORDER BY username
    """).fetchall()
    conn.close()

    return render_template(
        "admin_users.html",
        title="Benutzerverwaltung",
        users=users,
        back_ep=_resolve_back_ep()
    )

@app.post("/admin/users/create", endpoint="admin_users_create")
def admin_users_create():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    role     = (request.form.get("role") or "user").strip()
    wm_raw   = request.form.get("weekly_minutes", "2400")

    if not username or not password:
        return ("Benutzername und Passwort sind Pflicht.", 400)
    if role not in ("admin","user"):
        role = "user"

    weekly_minutes = _parse_minutes(wm_raw, 2400)

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, weekly_minutes) VALUES (?,?,?,?)",
            (username, generate_password_hash(password), role, weekly_minutes)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return ("Benutzername bereits vergeben.", 400)
    conn.close()
    return redirect(url_for("admin_users"))

@app.post("/admin/users/update/<int:user_id>", endpoint="admin_users_update")
def admin_users_update(user_id: int):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    new_role = (request.form.get("role") or "").strip()
    new_wm   = _parse_minutes(request.form.get("weekly_minutes", "2400"), 2400)
    new_pw   = (request.form.get("password") or "").strip()

    fields = []
    params = []

    if new_role in ("admin","user"):
        fields.append("role = ?")
        params.append(new_role)

    fields.append("weekly_minutes = ?")
    params.append(new_wm)

    if new_pw:
        fields.append("password_hash = ?")
        params.append(generate_password_hash(new_pw))

    if not fields:
        return redirect(url_for("admin_users"))

    params.append(user_id)

    conn = get_db()
    conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return redirect(url_for("admin_users"))
# ------------------- Ende Admin: Benutzer -----------------------------------

# --- Report-Helfer ---
WORK_START = {"bin da", "kommt"}
WORK_END   = {"gehe", "geht"}
BRK_START  = {"päuschen", "pause"}
BRK_END    = {"mache weiter", "pausenende"}
AFK_START  = {"afk", "abwesend"}
AFK_END    = {"wieder da", "wieder_da"}

def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

def compute_day_minutes(events):
    work = breaks = afk = 0
    on = brk_on = afk_on = None
    flags = []

    for ts_str, action in events:
        t = _parse_ts(ts_str)

        if action in WORK_START:
            if on is None:
                on = t
            else:
                work += max(0, int((t - on).total_seconds() // 60))
                if brk_on:
                    breaks += max(0, int((t - brk_on).total_seconds() // 60))
                    brk_on = None
                if afk_on:
                    afk += max(0, int((t - afk_on).total_seconds() // 60))
                    afk_on = None
                on = t

        elif action in WORK_END:
            if on is not None:
                work += max(0, int((t - on).total_seconds() // 60))
                on = None
                if brk_on:
                    breaks += max(0, int((t - brk_on).total_seconds() // 60))
                    brk_on = None
                if afk_on:
                    afk += max(0, int((t - afk_on).total_seconds() // 60))
                    afk_on = None
            else:
                flags.append("Ende ohne Start")

        elif action in BRK_START:
            if on is not None and brk_on is None:
                brk_on = t

        elif action in BRK_END:
            if brk_on is not None:
                breaks += max(0, int((t - brk_on).total_seconds() // 60))
                brk_on = None

        elif action in AFK_START:
            if on is not None and afk_on is None:
                afk_on = t

        elif action in AFK_END:
            if afk_on is not None:
                afk += max(0, int((t - afk_on).total_seconds() // 60))
                afk_on = None

    if on is not None:
        flags.append("Offener Arbeitstag (kein Ende)")
    if brk_on is not None:
        flags.append("Offene Pause (kein Ende)")
    if afk_on is not None:
        flags.append("Offenes AFK (kein Ende)")

    net = max(0, work - breaks - afk)
    return {"work": work, "breaks": breaks, "afk": afk, "net": net, "flags": flags}

# --- Admin-Diagnose (read-only, ohne flask_login) ---
@app.route("/admin/diag")
@session_required
def admin_diag():
    if session.get("role") != "admin":
        return redirect(url_for("unauthorized"))

    db_path = DB_PATH
    if not db_path or not os.path.exists(db_path):
        return jsonify({"db_path": db_path or "(keine gefunden)", "tables": [], "note": "Keine DB gefunden"}), 200

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [r[0] for r in cur.fetchall()]
    sample_counts = {}
    for t in tables[:10]:
        try:
            cur.execute(f"SELECT COUNT(1) FROM {t};")
            sample_counts[t] = cur.fetchone()[0]
        except Exception:
            sample_counts[t] = "n/a"
    conn.close()
    return jsonify({"db_path": db_path, "tables": tables, "counts": sample_counts}), 200

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
