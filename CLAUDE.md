# CLAUDE.md

## Purpose

grow-ui is a lightweight Flask control panel for a hydroponic dosing system. It runs on
the Raspberry Pi and is the day-to-day operator interface: mix nutrients, run pumps by
hand, read sensors, review history. It is a dedicated panel for one grow, not a general
automation platform.

It does four things:

- computes nutrient doses from gallons, growth stage and strength, and publishes them as
  one `pump_batch` to the pump ESP32 over MQTT
- sends manual pump commands and sensor read/calibration commands
- streams every ESP32 message to the browser over SSE
- logs doses to a shared SQLite database and shows that history

## Where it sits

- **grow-ui (this repo):** operator UI, nutrient math, command dispatch
- **ESP32 pump controller:** executes batches, reports events. Owns I2C addresses, PROFET
  channels and motor scheduling.
- **ESP32 sensor controller:** reads pH, EC, water temp, air temp/humidity
- **Mycodo (on the same Pi):** collects sensor readings for its dashboard and writes them
  to the shared database. Event-driven dosing from Mycodo is planned.

grow-ui never knows an I2C address or GPIO pin; it speaks only in `pump_id` and
`sensor_id` names. Mycodo can also publish to `esp32/pump/cmd`. The two do not
coordinate, so never run a batch from both at once.

## Stack

Python 3.10+, Flask 3, `paho-mqtt` 2.x (callback API `VERSION2`), `python-dotenv`, a
vanilla-JS single page (no framework), SQLite.

## Files

- `app.py` — routes, MQTT client, SSE broadcast, dosing logic, auth
- `config.py` — loads `.env`; topics, recipes, systems, delays, defaults
- `history.py` — SQLite writes for `dosing_events`, reads for the History tab
- `templates/index.html` — the whole UI (Dose, Manual Pumps, Sensors, History tabs)
- `templates/login.html` — login form
- `.env` — secrets and overrides (gitignored)

### Routes (all except `/login` require login)

| Route | Purpose |
|---|---|
| `POST /preview` | Compute doses and delays for display |
| `POST /dose` | Build and publish the `pump_batch`, log doses |
| `POST /abort` | Publish `{"v":1,"type":"batch_abort"}` |
| `POST /command` | Manual pump command (single or multi-pump) |
| `POST /sensor_read` | `read` / `read_all` to the sensor ESP32 |
| `POST /sensor_cmd` | Raw Atlas command for `ph` or `ec` (calibration) |
| `GET /history` | Last 50 dosing events and sensor readings |
| `GET /stream` | SSE stream of all MQTT traffic |

## MQTT

JSON only, every message has `"v": 1`, commands carry a `request_id` the firmware echoes.

| Topic | Direction |
|---|---|
| `esp32/pump/cmd` | publish |
| `esp32/pump/resp`, `esp32/pump/status` | subscribe |
| `esp32/sensors/cmd` | publish |
| `esp32/sensors/resp`, `esp32/sensors/status` | subscribe |

Every received message is forwarded to the browser unchanged, plus `_topic` and `_ts`.

Pump names: `calmag`, `micro`, `gro`, `bloom`, `phup`, `phdown`. Sensor names: `ph`, `ec`,
`water_temp`, `air`. These come from the firmware; keep `SYSTEMS` in `config.py` aligned.

## Dosing

1. The form sends gallons, stage, strength and optional per-nutrient delays.
2. `/preview` returns the doses; `/dose` validates, builds the batch, publishes, logs.
3. The ESP32 runs the batch step by step, emitting `batch_item_event` and `batch_event`
   on `esp32/pump/resp`, which reach the browser over SSE.

Math: Cal-Mag is `gallons × CALMAG_ML_PER_GAL` (flat, **not** scaled by strength). Micro,
Gro and Bloom are `gallons × STAGE_RECIPES[stage][nutrient] × strength`. Recipes are GH
Flora ml/gal at full strength; the default strength is 0.33.

## Authentication

`UI_USER` / `UI_PASS` from `.env`. Login issues a random token kept in an in-memory set
and sent as an HTTP-only cookie (`grow_session`, 30 days). Restarting the app logs
everyone out. The cookie is not marked `secure` because the panel is served over plain
HTTP on the LAN and Tailscale.

## History database

`/opt/Mycodo/mycodo/databases/grow_history.db`, shared with Mycodo.

- `dosing_events` — written by grow-ui (and, later, Mycodo)
- `sensor_readings` — written **only by Mycodo**; grow-ui just displays it

Both codebases define the schema independently, so a new column must be added in both,
plus a manual `ALTER TABLE` on the Pi (existing databases are not migrated). Logging
never raises: a failed write is silent.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `MQTT_HOST` / `MQTT_PORT` | `10.0.0.232` / `1883` | Broker |
| `MQTT_USER` / `MQTT_PASS` | — | Broker credentials |
| `UI_USER` / `UI_PASS` | `admin` / — | Web UI login |
| `CALMAG_ML_PER_GAL` | `3.8` | Cal-Mag rate |
| `DEFAULT_STRENGTH` | `0.33` | Default strength factor |
| `DELAY_CALMAG_SEC` | `600` | Mixing delay after Cal-Mag |
| `DELAY_MICRO_SEC` | `150` | After Micro |
| `DELAY_GRO_SEC` | `90` | After Gro |
| `DELAY_BLOOM_SEC` | `90` | After Bloom |

Topic names can also be overridden (`MQTT_CMD_TOPIC`, `SENSOR_STATUS_TOPIC`, ...), but the
defaults match the firmware.

## Deployment

Systemd service `grow-ui` on the Pi, working directory `/home/pi/code/grow-ui`, venv
`.venv`, port 5001.

```bash
cd /home/pi/code/grow-ui && git pull && sudo systemctl restart grow-ui
```

## Gotchas

1. **Dose order is fixed: Cal-Mag, Micro, Gro, Bloom.** GH labels require FloraMicro to
   go into fresh water first, and concentrates must never be mixed together.
2. **Delays come from the request.** `build_batch()` uses the submitted delays and falls
   back to the `.env` defaults; do not hardcode them. Timing belongs to the ESP32 — a batch
   can span 20+ minutes and must survive an app restart, so never sequence steps from here.
3. **Logged doses are the requested amounts.** `/dose` writes its rows when the batch is
   published, before anything runs. An aborted or failed batch still appears as dosed.
   Completion on the firmware is estimated from `ml / mlPerSec`, never measured.
4. **One motor runs at a time** (the 12 V supply cannot run two). Do not design anything
   that assumes parallel dosing.
5. **Manual commands** use `queue_if_busy` for single pumps so they are not rejected during
   a batch. Multi-pump motor commands go out as a `pump_batch` with `stop_on_error: false`.
6. **pH Up / pH Down** doses over 20 ml prompt for confirmation. The threshold is hardcoded
   in `sendCommand()` in `index.html`. pH is manual here; it is a feedback loop, not part
   of the nutrient batch.
7. **SSE buffer:** the last 100 events are replayed to new connections so a refresh
   mid-batch keeps context. The buffer is in memory only and is lost on restart.
8. **paho-mqtt 2.x:** callbacks use the v2 signatures. v1-style callbacks fail silently.
9. **Multiple systems:** `SYSTEMS` in `config.py` maps nutrient roles to pump IDs. The
   selector appears only when more than one system is defined.
10. **Secrets stay out of git.** `.env` holds broker and UI credentials.
