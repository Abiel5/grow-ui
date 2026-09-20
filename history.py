# coding=utf-8
#
# history.py - grow-ui side of grow_history SQLite logging.
#
# Writes to the same DB as the Mycodo-side grow_history.py utility.
# Tables are created automatically on first use.
#
import sqlite3
import threading
import time

DB_PATH = '/opt/Mycodo/mycodo/databases/grow_history.db'

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           REAL    NOT NULL,
            ph           REAL,
            ec           REAL,
            water_temp_f REAL,
            air_temp_f   REAL,
            air_humidity REAL
        );
        CREATE TABLE IF NOT EXISTS dosing_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              REAL    NOT NULL,
            trigger         TEXT    NOT NULL,
            pump_id         TEXT    NOT NULL,
            volume_ml       REAL    NOT NULL,
            grow_stage      TEXT,
            strength_factor REAL,
            ph_before       REAL,
            batch_id        TEXT
        );
    """)
    conn.commit()


def log_dosing_event(trigger, pump_id, volume_ml,
                     grow_stage=None, strength_factor=None,
                     ph_before=None, batch_id=None):
    with _lock:
        conn = _conn()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO dosing_events "
                "(ts, trigger, pump_id, volume_ml, grow_stage, strength_factor, ph_before, batch_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), trigger, pump_id, volume_ml,
                 grow_stage, strength_factor, ph_before, batch_id)
            )
            conn.commit()
        except Exception:
            pass  # never let logging break the UI
        finally:
            conn.close()


def parse_dose_ml(cmd):
    """Extract ml from 'D,X.XX' ESP32 dose command. Returns None for non-dose commands."""
    c = cmd.strip().upper()
    if c.startswith('D,'):
        try:
            return float(c[2:])
        except ValueError:
            pass
    return None


def query_history(sensor_limit=50, dosing_limit=50):
    with _lock:
        conn = _conn()
        try:
            _ensure_schema(conn)
            sensors = conn.execute(
                "SELECT * FROM sensor_readings ORDER BY ts DESC LIMIT ?",
                (sensor_limit,)
            ).fetchall()
            dosing = conn.execute(
                "SELECT * FROM dosing_events ORDER BY ts DESC LIMIT ?",
                (dosing_limit,)
            ).fetchall()
            return {
                'sensor_readings': [dict(r) for r in sensors],
                'dosing_events':   [dict(r) for r in dosing],
            }
        except Exception:
            return {'sensor_readings': [], 'dosing_events': []}
        finally:
            conn.close()
