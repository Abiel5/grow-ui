# grow-ui

A lightweight web control panel for a hydroponic nutrient dosing system. It runs as a
Flask app on a Raspberry Pi and is the day-to-day operator interface: mix nutrients,
run pumps by hand, watch sensors, and review history.

It talks to two ESP32 controllers over MQTT and streams their events live to the browser.

## Features

- **Dose** — enter gallons added, growth stage and strength, preview the doses, then send
  the whole mix as one batch
- **Manual pumps** — run any pump directly, with a confirmation prompt for pH doses over 20 ml
- **Sensors** — live pH, EC, water temperature, air temperature and humidity, with
  on-demand reads and raw Atlas calibration commands
- **History** — dosing events and sensor readings from a shared SQLite database
- **Live events** — ESP32 events stream to the browser over SSE, with the last 100 events
  replayed on reconnect so a refresh mid-batch does not lose context

## Requirements

- Python 3.10+
- An MQTT broker (Mosquitto) reachable on the network
- The two ESP32 controllers online and subscribed

## Setup

```bash
git clone https://github.com/Abiel5/grow-ui.git
cd grow-ui
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```ini
MQTT_HOST=10.0.0.232
MQTT_PORT=1883
MQTT_USER=your-mqtt-username
MQTT_PASS=your-mqtt-password

UI_USER=admin
UI_PASS=your-ui-password
SECRET_KEY=a-long-random-string
```

`.env` is gitignored and holds every secret the app needs. Dosing tunables
(`CALMAG_ML_PER_GAL`, `DEFAULT_STRENGTH`, the per-nutrient mixing delays) can also be set
there; `config.py` holds the defaults.

## Running

```bash
python app.py
```

Serves on port 5001, all interfaces. Log in with `UI_USER` / `UI_PASS`.

Sessions are held in memory, so everyone is logged out when the app restarts.

## Deployment

Runs as a systemd service on the Pi at `/home/pi/code/grow-ui`, listening on port 5001 and
reachable at `http://10.0.0.232:5001` on the LAN or over Tailscale.

```bash
cd /home/pi/code/grow-ui && git pull && sudo systemctl restart grow-ui
```

## How it works

```
browser ──HTTP──► Flask (app.py) ──MQTT──► ESP32 pump controller
   ▲                                       ESP32 sensor controller
   └──────SSE──── MQTT subscriptions ◄──────────────┘
```

- `app.py` — routes, MQTT client, SSE broadcast, dosing logic, auth
- `config.py` — `.env` loading, topics, stage recipes, pump maps, delays
- `history.py` — SQLite reads and writes
- `templates/index.html` — the whole single-page UI

Dosing computes Cal-Mag as a flat rate per gallon (not scaled by strength) and Micro, Gro
and Bloom from the stage recipe times strength. The four doses go out as a single
`pump_batch` in the order Cal-Mag, Micro, Gro, Bloom, with a mixing delay after each step.
That order follows the GH Flora label instructions and should not be changed casually.

Sequencing and timing belong to the ESP32, not to this app — a batch can span 20+ minutes
of mixing delays and must survive a restart here.

### MQTT topics

| Topic | Direction |
|---|---|
| `esp32/pump/cmd` | publish |
| `esp32/pump/resp` | subscribe |
| `esp32/pump/status` | subscribe |
| `esp32/sensors/cmd` | publish |
| `esp32/sensors/resp` | subscribe |
| `esp32/sensors/status` | subscribe |

### History database

SQLite at `/opt/Mycodo/mycodo/databases/grow_history.db`, shared with the Mycodo side.
Tables `dosing_events` and `sensor_readings` are created on first write. Both projects
define the schema separately, so a column added here must be added there too.

## Related projects

Part of a four-repo hydroponics system:

- [esp32-pump-controller](https://github.com/Abiel5/esp32-pump-controller) — dosing pump firmware
- [esp32-sensor-controller](https://github.com/Abiel5/esp32-sensor-controller) — sensor firmware
- [Mycodo fork](https://github.com/Abiel5/Mycodo) — automation, scheduling and long-term logging

Mycodo can publish to the same pump topic as this app. They do not coordinate, so do not
run a batch from both at once.

See `CLAUDE.md` for architecture notes and conventions.
