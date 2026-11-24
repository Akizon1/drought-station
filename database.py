import secrets
import sqlite3
import time
from contextlib import closing

from config import DB_PATH, DEFAULT_SETTINGS, HISTORY_KEEP_DAYS

ROLES = ("admin", "expert", "farmer")
ROLE_NAMES = {"admin": "Администратор", "expert": "Эксперт", "farmer": "Фермер"}
PROFILE_FIELDS = ("first_name", "last_name", "company", "position", "phone", "region")

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    invited_by INTEGER,
    created_at REAL NOT NULL DEFAULT 0
);
"""

BASE_SCHEMA = USERS_SCHEMA + """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    temperature REAL,
    humidity REAL,
    water INTEGER,
    status TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sensors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    lat REAL,
    lon REAL,
    profile TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    last_seen REAL NOT NULL DEFAULT 0,
    port TEXT NOT NULL DEFAULT '',
    owner_id INTEGER
);
CREATE TABLE IF NOT EXISTS sensor_access (
    sensor_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (sensor_id, user_id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tg_subscribers (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    subscribed INTEGER NOT NULL DEFAULT 1,
    added_at REAL NOT NULL
);
"""

READING_COLUMNS = {
    "sensor_id": "TEXT NOT NULL DEFAULT 'arduino-1'",
    "is_test": "INTEGER NOT NULL DEFAULT 0",
    "salinity": "REAL",
    "ph": "REAL",
    "n": "REAL",
    "p": "REAL",
    "k": "REAL",
}
SENSOR_COLUMNS = {"port": "TEXT NOT NULL DEFAULT ''", "owner_id": "INTEGER"}
# Подписка: plan = free | trial | pro; plan_until — до какого времени (0 у pro — бессрочно, выдал админ)
USER_PLAN_COLUMNS = {"plan": "TEXT NOT NULL DEFAULT 'free'", "plan_until": "REAL NOT NULL DEFAULT 0",
                     "trial_used": "INTEGER NOT NULL DEFAULT 0"}
# История уведомлений: kind = drought | link | system; sensor_id — чтобы фермер видел только свои датчики
EVENT_COLUMNS = {"kind": "TEXT NOT NULL DEFAULT 'system'", "sensor_id": "TEXT", "is_read": "INTEGER NOT NULL DEFAULT 0"}

BASE_SENSORS = [
    ("arduino-1", "Arduino UNO Number 1", "real", 1, None, None, "", None),
    ("test-kyzylorda", "Тест: Кызылорда, орошаемое поле", "test", 1, 44.85, 65.48, "kyzylorda", None),
    ("test-almaty", "Тест: Алматы, пригород", "test", 1, 43.22, 76.85, "almaty", None),
]


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _query(sql, params=()):
    with closing(_conn()) as conn, conn:
        return conn.execute(sql, params).fetchall()


def _exec(sql, params=()):
    with closing(_conn()) as conn, conn:
        conn.execute(sql, params)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db():
    with closing(_conn()) as conn, conn:
        migrate_users = False
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if exists and "first_name" not in _columns(conn, "users"):
            conn.execute("ALTER TABLE users RENAME TO users_old")
            migrate_users = True
        conn.executescript(BASE_SCHEMA)
        if migrate_users:
            conn.execute("INSERT INTO users (id, username, salt, password_hash, role, created_at) "
                         "SELECT id, username, salt, password_hash, "
                         "CASE WHEN role = 'admin' THEN 'admin' ELSE 'farmer' END, 0 FROM users_old")
            conn.execute("DROP TABLE users_old")

        cols = _columns(conn, "readings")
        for column, definition in READING_COLUMNS.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE readings ADD COLUMN {column} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts ON readings(sensor_id, ts)")
        cols = _columns(conn, "users")
        for column, definition in {"consent_version": "TEXT NOT NULL DEFAULT ''", "consent_at": "REAL NOT NULL DEFAULT 0",
                                   **USER_PLAN_COLUMNS}.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        cols = _columns(conn, "app_events")
        for column, definition in EVENT_COLUMNS.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE app_events ADD COLUMN {column} {definition}")
        cols = _columns(conn, "sensors")
        for column, definition in SENSOR_COLUMNS.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE sensors ADD COLUMN {column} {definition}")
        old = conn.execute("SELECT value FROM settings WHERE key = 'port'").fetchone()
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        for row in BASE_SENSORS:
            conn.execute("INSERT OR IGNORE INTO sensors (id, name, kind, enabled, lat, lon, profile, owner_id) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
        conn.execute("UPDATE sensors SET name = 'Arduino UNO Number 1' WHERE id = 'arduino-1' "
                     "AND name = 'Arduino UNO (COM)'")
        if old and old["value"]:
            conn.execute("UPDATE sensors SET port = ? WHERE id = 'arduino-1' AND port = ''", (old["value"],))


# ---------- пользователи ----------
def has_users():
    return _query("SELECT COUNT(*) AS n FROM users")[0]["n"] > 0


def has_role(role):
    return _query("SELECT 1 FROM users WHERE role = ? LIMIT 1", (role,)) != []


def create_user(username, salt, password_hash, role, profile):
    values = {field: profile.get(field, "") for field in PROFILE_FIELDS}
    with closing(_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO users (username, salt, password_hash, role, first_name, last_name, company, position, "
            "phone, region, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (username, salt, password_hash, role, values["first_name"], values["last_name"], values["company"],
             values["position"], values["phone"], values["region"], time.time()))
        user_id = cur.lastrowid
        if role == "expert":
            conn.execute("UPDATE sensors SET owner_id = ? WHERE kind = 'real' AND owner_id IS NULL", (user_id,))
        return user_id


def get_user(username):
    rows = _query("SELECT * FROM users WHERE username = ?", (username,))
    return rows[0] if rows else None


def get_user_by_id(user_id):
    rows = _query("SELECT * FROM users WHERE id = ?", (user_id,))
    return rows[0] if rows else None


def list_users():
    return _query("SELECT * FROM users ORDER BY role, username")


def delete_user(user_id):
    _exec("DELETE FROM users WHERE id = ?", (user_id,))
    _exec("DELETE FROM sensor_access WHERE user_id = ?", (user_id,))


def update_password(user_id, salt, password_hash):
    _exec("UPDATE users SET salt = ?, password_hash = ? WHERE id = ?", (salt, password_hash, user_id))


def invite_farmer(login, expert, limit=None):
    """Эксперт приглашает фермера. Фермер получает название фирмы эксперта (и его подписку).
    limit — сколько фермеров можно на тарифе эксперта (None — без ограничений)."""
    if limit is not None and len(farmers_of(expert["id"])) >= limit:
        return False, (f"На бесплатном тарифе можно пригласить до {limit} фермеров. "
                       "Больше — в подписке (раздел «Подписка»).")
    farmer = get_user(login)
    if not farmer:
        return False, "Пользователь с таким логином не найден"
    if farmer["role"] != "farmer":
        return False, "Приглашать можно только фермеров"
    if farmer["invited_by"]:
        return False, "Этот фермер уже приглашён другим экспертом"
    _exec("UPDATE users SET company = ?, invited_by = ? WHERE id = ?",
          (expert["company"], expert["id"], farmer["id"]))
    return True, f"Фермер {farmer['first_name']} {farmer['last_name']} приглашён"


def remove_farmer(farmer_id, expert_id):
    farmer = get_user_by_id(farmer_id)
    if not farmer or farmer["invited_by"] != expert_id:
        return
    _exec("UPDATE users SET company = '', invited_by = NULL WHERE id = ?", (farmer_id,))
    _exec("DELETE FROM sensor_access WHERE user_id = ? AND sensor_id IN "
          "(SELECT id FROM sensors WHERE owner_id = ?)", (farmer_id, expert_id))


def farmers_of(expert_id):
    return _query("SELECT * FROM users WHERE invited_by = ? ORDER BY last_name, first_name", (expert_id,))


def granted_sensor_ids(user_id):
    return {r["sensor_id"] for r in _query("SELECT sensor_id FROM sensor_access WHERE user_id = ?", (user_id,))}


def set_access(sensor_id, user_id, allowed):
    if allowed:
        _exec("INSERT OR IGNORE INTO sensor_access (sensor_id, user_id) VALUES (?, ?)", (sensor_id, user_id))
    else:
        _exec("DELETE FROM sensor_access WHERE sensor_id = ? AND user_id = ?", (sensor_id, user_id))


# ---------- датчики ----------
def visible_sensors(user, include_test=False):
    """Какие датчики видит пользователь: эксперт — свои, фермер — выданные ему, админ — все физические.
    Тестовые датчики видны всем, но только когда включён тестовый режим (его включает админ)."""
    uid = user["id"]
    if user["role"] == "admin":
        real, params = "kind = 'real'", []
    elif user["role"] == "expert":
        real, params = "kind = 'real' AND owner_id = ?", [uid]
    else:
        real, params = ("kind = 'real' AND id IN (SELECT sensor_id FROM sensor_access WHERE user_id = ?)"), [uid]
    where = f"enabled = 1 AND (({real}) OR kind = 'test')" if include_test else f"enabled = 1 AND ({real})"
    return _query(f"SELECT * FROM sensors WHERE {where} ORDER BY kind, name", params)


def manageable_sensors(user):
    """Физические датчики, которыми пользователь может управлять."""
    if user["role"] == "admin":
        return _query("SELECT * FROM sensors WHERE kind = 'real' ORDER BY name")
    return _query("SELECT * FROM sensors WHERE kind = 'real' AND owner_id = ? ORDER BY name", (user["id"],))


def collect_sensors(include_test):
    """Все датчики, с которых программа собирает данные (независимо от пользователя)."""
    sql = "SELECT * FROM sensors WHERE enabled = 1 AND (kind = 'real' OR ?)"
    return _query(sql + " ORDER BY kind, name", (1 if include_test else 0,))


def all_sensors():
    return _query("SELECT * FROM sensors ORDER BY kind, name")


def test_sensors():
    return _query("SELECT * FROM sensors WHERE kind = 'test' ORDER BY name")


def add_real_sensor(owner_id, name, port, lat, lon):
    sid = "arduino-" + secrets.token_hex(3)
    _exec("INSERT INTO sensors (id, name, kind, enabled, lat, lon, profile, port, owner_id) "
          "VALUES (?, ?, 'real', 1, ?, ?, '', ?, ?)", (sid, name, lat, lon, port, owner_id))
    return sid


def add_test_sensor(name, profile, lat, lon):
    sid = "test-" + secrets.token_hex(3)
    _exec("INSERT INTO sensors (id, name, kind, enabled, lat, lon, profile) VALUES (?, ?, 'test', 1, ?, ?, ?)",
          (sid, name, lat, lon, profile))
    return sid


def update_sensor(sid, name, port, lat, lon, enabled, profile):
    _exec("UPDATE sensors SET name = ?, port = ?, lat = ?, lon = ?, enabled = ?, profile = ? WHERE id = ?",
          (name, port, lat, lon, 1 if enabled else 0, profile, sid))


def delete_sensor(sid):
    _exec("DELETE FROM sensors WHERE id = ?", (sid,))
    _exec("DELETE FROM sensor_access WHERE sensor_id = ?", (sid,))


def touch_sensor(sid, state, ts):
    _exec("UPDATE sensors SET state = ?, last_seen = ? WHERE id = ?", (state, ts, sid))


# ---------- настройки ----------
def get_settings():
    return {row["key"]: row["value"] for row in _query("SELECT key, value FROM settings")}


def set_settings(values):
    with closing(_conn()) as conn, conn:
        for key, value in values.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


# ---------- показания ----------
def add_reading(ts, sid, is_test, v, status):
    _exec("INSERT INTO readings (ts, temperature, humidity, water, status, sensor_id, is_test, "
          "salinity, ph, n, p, k) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          (ts, v["temperature"], v["humidity"], v["water"], status, sid, is_test,
           v.get("salinity"), v.get("ph"), v.get("n"), v.get("p"), v.get("k")))


def get_readings(since, sid=None):
    if sid:
        rows = _query("SELECT * FROM readings WHERE ts >= ? AND sensor_id = ? ORDER BY ts", (since, sid))
    else:
        rows = _query("SELECT * FROM readings WHERE ts >= ? ORDER BY ts", (since,))
    return [dict(r) for r in rows]


def latest_by_sensor():
    return _query("SELECT r.* FROM readings r JOIN (SELECT sensor_id, MAX(id) AS mid FROM readings "
                  "GROUP BY sensor_id) m ON r.id = m.mid")


def prune_readings():
    _exec("DELETE FROM readings WHERE ts < ?", (time.time() - HISTORY_KEEP_DAYS * 86400,))


# ---------- Telegram: подписчики и журнал ----------
def set_subscriber(chat_id, title, subscribed):
    _exec("INSERT INTO tg_subscribers (chat_id, title, subscribed, added_at) VALUES (?, ?, ?, ?) "
          "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title, subscribed = excluded.subscribed",
          (chat_id, title, subscribed, time.time()))


def subscriber_flag(chat_id):
    rows = _query("SELECT subscribed FROM tg_subscribers WHERE chat_id = ?", (chat_id,))
    return rows[0]["subscribed"] if rows else None


def list_subscribers():
    return _query("SELECT * FROM tg_subscribers WHERE subscribed = 1")


def log_notification(ts, text, ok):
    _exec("INSERT INTO notifications (ts, text, ok) VALUES (?, ?, ?)", (ts, text, 1 if ok else 0))


def list_notifications(limit=30):
    return _query("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))


# ---------- подписка ----------
def start_trial(user_id, days, now=None):
    """Пробный период эксперта: один раз на аккаунт. Возвращает True, если запущен."""
    now = now or time.time()
    user = get_user_by_id(user_id)
    if not user or user["role"] != "expert" or user["trial_used"]:
        return False
    _exec("UPDATE users SET plan = 'trial', plan_until = ?, trial_used = 1 WHERE id = ?",
          (now + days * 86400, user_id))
    return True


def set_plan(user_id, plan, until=0):
    _exec("UPDATE users SET plan = ?, plan_until = ? WHERE id = ?", (plan, until, user_id))


def record_consent(user_id, version):
    """Фиксирует, что пользователь принял условия и политику указанной версии."""
    _exec("UPDATE users SET consent_version = ?, consent_at = ? WHERE id = ?", (version, time.time(), user_id))


# ---------- история уведомлений программы ----------
def log_event(ts, level, text, kind="system", sensor_id=None):
    _exec("INSERT INTO app_events (ts, level, text, kind, sensor_id) VALUES (?, ?, ?, ?, ?)",
          (ts, level, text, kind, sensor_id))


def _event_filter(sensor_ids, kind=None):
    """Уведомления без датчика видят все; с датчиком — только те, кому датчик виден."""
    where, params = [], []
    if sensor_ids is not None:
        marks = ",".join("?" * len(sensor_ids))
        where.append(f"(sensor_id IS NULL OR sensor_id IN ({marks}))" if sensor_ids else "sensor_id IS NULL")
        params += list(sensor_ids)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def list_events(limit=200, sensor_ids=None, kind=None):
    where, params = _event_filter(sensor_ids, kind)
    return _query(f"SELECT * FROM app_events{where} ORDER BY id DESC LIMIT ?", (*params, limit))


def unread_events(sensor_ids=None):
    where, params = _event_filter(sensor_ids)
    where = (where + " AND " if where else " WHERE ") + "is_read = 0"
    return _query(f"SELECT COUNT(*) AS n FROM app_events{where}", params)[0]["n"]


def mark_events_read(sensor_ids=None):
    where, params = _event_filter(sensor_ids)
    _exec(f"UPDATE app_events SET is_read = 1{where}", params)


def clear_events(sensor_ids=None):
    where, params = _event_filter(sensor_ids)
    _exec(f"DELETE FROM app_events{where}", params)


# ---------- выборка для графиков и экспорта ----------
SERIES_KEYS = ("temperature", "humidity", "water", "salinity", "ph", "n", "p", "k")


def get_series(since, sensor_ids, bucket=0):
    """Показания датчиков с since. bucket > 0 — средние значения по интервалам bucket секунд
    (для длинных периодов, чтобы графики и Excel не тормозили)."""
    if not sensor_ids:
        return []
    marks = ",".join("?" * len(sensor_ids))
    if bucket <= 0:
        rows = _query(f"SELECT * FROM readings WHERE ts >= ? AND sensor_id IN ({marks}) ORDER BY ts",
                      (since, *sensor_ids))
        return [dict(r) for r in rows]
    cols = ", ".join(f"AVG({k}) AS {k}" for k in SERIES_KEYS)
    rows = _query(f"SELECT MIN(ts) AS ts, {cols} FROM readings WHERE ts >= ? AND sensor_id IN ({marks}) "
                  "GROUP BY CAST(ts / ? AS INTEGER) ORDER BY ts", (since, *sensor_ids, bucket))
    return [dict(r) for r in rows]
