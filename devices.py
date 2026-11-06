"""Live health of the two ESP32 controllers, kept in memory.

Fed from the MQTT callback, read by the /devices route. Nothing is stored: after a
restart the state refills from the next heartbeat (at most 30 s).
"""
import threading
import time

from config import (
    DEVICE_HEARTBEAT_DOWN_SEC,
    DEVICE_HEARTBEAT_WARN_SEC,
    DEVICE_READING_DOWN_SEC,
    DEVICE_READING_WARN_SEC,
    MQTT_RESP_TOPIC,
    MQTT_STATUS_TOPIC,
    SENSOR_STATUS_TOPIC,
)

SENSOR_IDS = ("ph", "ec", "water_temp", "air")

_lock = threading.Lock()
_started_at = time.time()

_state = {
    "pump": {
        "controller_id": None, "ip": None, "state": None,
        "seen_at": None, "heartbeat": {}, "last_batch": None,
    },
    "sensor": {
        "controller_id": None, "ip": None, "state": None,
        "seen_at": None, "heartbeat": {}, "reading_at": None,
        "sensors": {sid: {"ok": None, "last_ok_at": None} for sid in SENSOR_IDS},
    },
}

_NAMES = {"pump": "Pump controller", "sensor": "Sensor controller"}
_LEVEL = {"ok": 0, "unknown": 1, "warn": 2, "down": 3}


def _record_heartbeat(dev: dict, msg: dict, now: float, retained: bool) -> None:
    state = msg.get("state")
    if msg.get("controller_id"):
        dev["controller_id"] = msg["controller_id"]
    if msg.get("ip"):
        dev["ip"] = msg["ip"]
    if retained:
        # A retained message says what the broker last heard, not that the device is
        # alive now, so it must not refresh the liveness clock. A retained "offline"
        # is still worth showing until a live message replaces it.
        if state == "offline":
            dev["state"] = state
        return
    dev["state"] = state
    dev["seen_at"] = now
    dev["heartbeat"] = {
        **{k: msg[k] for k in ("uptime_sec", "free_heap", "heap_total", "heap_min_free",
                               "chip_temp", "wifi_rssi")
           if isinstance(msg.get(k), (int, float)) and not isinstance(msg.get(k), bool)},
        **{k: msg[k] for k in ("active_batch", "motor_busy") if isinstance(msg.get(k), bool)},
    }


def _record_sensor_reading(dev: dict, msg: dict, now: float) -> None:
    dev["reading_at"] = now
    # The firmware publishes -1 for a field whose read failed. Only the pass/fail is
    # kept here; this page does not display sensor values.
    fields = {
        "ph": (msg.get("ph"),),
        "ec": (msg.get("ec"),),
        "water_temp": (msg.get("water_temp"),),
        "air": (msg.get("air_temp"), msg.get("air_hum")),
    }
    for sid, values in fields.items():
        if any(not isinstance(v, (int, float)) for v in values):
            continue
        ok = all(v >= 0 for v in values)
        entry = dev["sensors"][sid]
        entry["ok"] = ok
        if ok:
            entry["last_ok_at"] = now


def record(topic: str, msg: dict, retained: bool = False, now: float | None = None) -> None:
    """Update device state from one decoded MQTT message."""
    now = time.time() if now is None else now
    mtype = msg.get("type")
    with _lock:
        if topic == MQTT_STATUS_TOPIC and mtype == "controller_status":
            _record_heartbeat(_state["pump"], msg, now, retained)
        elif topic == SENSOR_STATUS_TOPIC and mtype == "controller_status":
            _record_heartbeat(_state["sensor"], msg, now, retained)
        elif topic == SENSOR_STATUS_TOPIC and mtype == "sensor_reading":
            _record_sensor_reading(_state["sensor"], msg, now)
        elif topic == MQTT_RESP_TOPIC and mtype == "batch_event":
            pump = _state["pump"]
            pump["last_batch"] = {
                "request_id": msg.get("request_id"),
                "state": msg.get("state"),
                "reason": msg.get("reason"),
                "at": now,
            }
            # Reflect batch start/finish at once instead of waiting for the next heartbeat.
            if msg.get("state") == "accepted":
                pump["heartbeat"]["active_batch"] = True
            elif msg.get("state") in ("completed", "aborted"):
                pump["heartbeat"]["active_batch"] = False


def _fmt_age(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return "{}s".format(sec)
    if sec < 3600:
        return "{}m {}s".format(sec // 60, sec % 60)
    return "{}h {}m".format(sec // 3600, (sec % 3600) // 60)


def _clock(seen_at, now: float, warn: int, down: int, what: str) -> tuple:
    """Level and reason for a message stream, judged by the age of its last message."""
    ref = _started_at if seen_at is None else seen_at
    age = now - ref
    if age > down:
        if seen_at is None:
            return "down", "No {} since grow-ui started".format(what)
        return "down", "No {} for {}".format(what, _fmt_age(age))
    if age > warn:
        if seen_at is None:
            return "warn", "No {} since grow-ui started".format(what)
        return "warn", "No {} for {}".format(what, _fmt_age(age))
    if seen_at is None:
        return "unknown", "Waiting for first {}".format(what)
    return "ok", ""


def _device_view(kind: str, dev: dict, mqtt_connected: bool, now: float) -> dict:
    checks = []
    if dev["state"] == "offline":
        checks.append(("down", "Controller reported offline"))
    else:
        checks.append(_clock(dev["seen_at"], now, DEVICE_HEARTBEAT_WARN_SEC,
                             DEVICE_HEARTBEAT_DOWN_SEC, "heartbeat"))

    view = {
        "id": kind,
        "name": _NAMES[kind],
        "controller_id": dev["controller_id"],
        "ip": dev["ip"],
        "state": dev["state"],
        "heartbeat_age_sec": None if dev["seen_at"] is None else now - dev["seen_at"],
    }
    hb = dev["heartbeat"]
    view["wifi_rssi"] = hb.get("wifi_rssi")
    view["free_heap"] = hb.get("free_heap")
    view["heap_total"] = hb.get("heap_total")
    view["heap_min_free"] = hb.get("heap_min_free")
    view["chip_temp"] = hb.get("chip_temp")
    uptime = hb.get("uptime_sec")
    view["uptime_sec"] = uptime
    view["booted_at"] = None if uptime is None or dev["seen_at"] is None else dev["seen_at"] - uptime

    if kind == "pump":
        view["active_batch"] = hb.get("active_batch")
        view["motor_busy"] = hb.get("motor_busy")
        batch = dev["last_batch"]
        view["last_batch"] = None if batch is None else dict(batch, age_sec=now - batch["at"])
    else:
        view["reading_age_sec"] = None if dev["reading_at"] is None else now - dev["reading_at"]
        checks.append(_clock(dev["reading_at"], now, DEVICE_READING_WARN_SEC,
                             DEVICE_READING_DOWN_SEC, "sensor reading"))
        sensors = []
        failing = []
        for sid in SENSOR_IDS:
            entry = dev["sensors"][sid]
            sensors.append({
                "id": sid, "ok": entry["ok"],
                "last_ok_age_sec": None if entry["last_ok_at"] is None else now - entry["last_ok_at"],
            })
            if entry["ok"] is False:
                failing.append(sid)
        view["sensors"] = sensors
        if failing:
            checks.append(("warn", "Sensor read failing: {}".format(", ".join(failing))))

    if not mqtt_connected:
        view["status"] = "unknown"
        view["reasons"] = ["grow-ui is not connected to the broker"]
        return view

    worst = max((lvl for lvl, _ in checks), key=lambda lvl: _LEVEL[lvl])
    view["status"] = worst
    view["reasons"] = [reason for lvl, reason in checks if lvl != "ok" and reason]
    return view


def snapshot(mqtt_connected: bool, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with _lock:
        devices = [_device_view(kind, _state[kind], mqtt_connected, now) for kind in ("pump", "sensor")]
    return {"now": now, "mqtt_connected": mqtt_connected, "devices": devices}
