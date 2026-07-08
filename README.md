# needledrop

Drop the needle and your Sonos speakers play the record. Lift it, and after the side finishes they stop. That is the whole idea.

[![CI](https://github.com/justintevya/needledrop/actions/workflows/ci.yml/badge.svg)](https://github.com/justintevya/needledrop/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

needledrop is one program that runs on any Linux box with a cheap USB audio adapter. It listens to your turntable, notices when a record is actually playing, and streams it to whichever Sonos rooms you pick. When the side ends it hands the speakers back so the rest of the house can use them again.

There is a small web dashboard too, for watching what it is doing and changing settings. You can preview it with fake data at `/?demo=1` on any running copy.

## Why this exists

Sonos used to let you add a custom radio station. That was how everyone got a turntable onto their speakers: run darkice and icecast, aim a TuneIn custom station at the stream, and hope. Then Sonos dropped custom stations, the TuneIn registration path went stale, and the whole trick quietly stopped working. Mine broke one day and I got tired of it.

needledrop does the same job with a single program and nothing installed on the speakers. It uses the radio player already built into every Sonos unit, over plain UPnP, on your own network. Nothing is added to the speakers, nothing phones home, and it keeps working with the internet unplugged. No TuneIn, no cloud account, no plugins.

## What you need

| Piece | What works |
|---|---|
| Adapter | Any PCM2902-class USB phono adapter, roughly 20 to 40 dollars. The Behringer UFO202 has a phono preamp built in. The Behringer UCA202 is line level. The ART USB Phono Plus also works, as do the many identical-chipset clones. You can also run any phono preamp into any USB line-in codec. |
| Turntable | Anything. If it already has a line output, you can skip the phono preamp. |
| Computer | Any Linux box. A Raspberry Pi 3, 4, or 5 is plenty. On a Pi, put the adapter on a powered hub, because the onboard ports brown out under load. |
| Speakers | Any Sonos players, current S2 or the old S1 units, down to the oldest Play:1 firmware. |

## Install

You need Python 3.11 or newer.

The short version:

```bash
uv tool install needledrop        # or: pipx install needledrop
needledrop setup                  # asks which adapter and which rooms, writes the config
```

Plain pip works too, in a venv if you prefer:

```bash
python -m venv .venv && . .venv/bin/activate
pip install needledrop
needledrop setup
```

Either way, `needledrop setup` writes the config and prints the exact commands to install the systemd service and the udev rule. Run those, then:

```bash
sudo systemctl enable --now needledrop
```

Open `http://<your-box>:8341/` and put a record on.

The setup wizard also writes a udev rule (`90-needledrop.rules`) that stops Linux from suspending your USB adapter. Install it with the two commands the wizard prints. If you skip it, the cheaper adapters tend to vanish partway through a side.

## First record

Drop the needle and give it a couple of seconds. The dashboard should move from IDLE to PLAYING and your chosen rooms should group up and start playing. Lift the needle at the end and it waits a few minutes (long enough to flip the record) before it stops and gives the speakers back.

If it does not start when it should, or does not stop when it should, that is almost always the detection thresholds, and the fix is the calibration wizard behind the gear icon on the dashboard. It records ten seconds of your needle-up noise floor and ten seconds of a silent groove, then suggests the two numbers that tell music from silence. Cartridges, preamps, and adapter gain vary a lot, so the defaults are only a starting point.

## How it works

```
ALSA capture ─► ring buffer ─┬─► detector (high-pass, RMS, hysteresis) ─► state machine ─► SoCo
(arecord or     (PCM, 4s)    │                                            │                (group, volume,
 sounddevice,                └─► MP3 encoder (lameenc, 320 CBR)           │                 play, stop, watchdog)
 44.1k, 16-bit,                      │                                    ▼
 stereo)                             └─► HTTP fan-out (/stream.mp3)   websocket for state and VU
                aiohttp app: dashboard, REST API, websocket, /healthz  (audio stream on port + 1)
```

It moves through four states:

- IDLE. Silence on the input. Sonos is left alone and the stream stays warm.
- SENSING. Signal detected. It waits a short debounce (2 seconds by default) to be sure it is music and not a bumped tonearm.
- PLAYING. The rooms are grouped under one coordinator and playing the stream. A watchdog re-issues play every 10 seconds if a player wanders off.
- GRACE. The side went quiet. It keeps streaming silence for a grace window (4 minutes by default, enough to flip the record) before it stops Sonos, ungroups, and puts the volumes back.

## Configuration

The config lives at `~/.config/needledrop/config.yaml`, or `/etc/needledrop/config.yaml` for a system install. The setup wizard writes it, and the settings panel on the dashboard edits it live. Every key, with its default:

| Key | Default | What it does |
|---|---|---|
| `audio.device.usb_id` | `""` | The USB vendor:product id of your adapter (from `lsusb`), for example `08bb:2902`. Matched through sysfs, so it survives reboots and moving to a different port. |
| `audio.device.card_name` | `""` | ALSA card name to fall back on (like `CODEC`) if the usb_id does not match. If both miss and there is exactly one USB capture card, it gets used with a warning. |
| `audio.sample_rate` | `44100` | Fixed at 44100, the rate old and new Sonos both accept as radio. Anything else is rejected. |
| `detect.highpass_hz` | `40.0` | High-pass corner. Cuts turntable rumble and cheap-adapter DC offset so they do not register as music. |
| `detect.music_on_db` | `-45.0` | Smoothed level above this counts as music. Has to be higher than `music_off_db`. |
| `detect.music_off_db` | `-55.0` | Smoothed level below this counts as silence. The gap between the two is hysteresis, so a quiet passage does not flip the state back and forth. |
| `detect.start_debounce_s` | `2.0` | Music has to hold this long before playback starts. |
| `detect.end_of_side_s` | `240.0` | Silence this long during GRACE means the side is over and Sonos stops. Four minutes gives you time to flip the record without the house going quiet. |
| `stream.port` | `8341` | Port for the dashboard, API, and websocket. The audio stream itself is served on `port + 1`, with raw framing that the oldest firmware accepts. |
| `stream.bitrate` | `320` | MP3 bitrate, one of 128, 192, 256, 320. Constant bitrate only, because old players have buffer bugs with variable bitrate radio. |
| `sonos.vinyl_zones` | `[]` | The room names to pull into vinyl playback. Has to be non-empty for it to run. |
| `sonos.preferred_coordinator` | `""` | The room that pulls the stream and passes it to the rest of the group. Pick a wired or newer player. Empty means the first available vinyl room. |
| `sonos.dont_interrupt_busy` | `true` | Rooms already playing something else get skipped (and marked in the dashboard) instead of hijacked. |
| `sonos.master_volume` | `null` | Set every vinyl room to this volume on start. `null` leaves the volumes as they are. Either way, the previous volumes come back when it stops. |

There are no secrets in this file. There is nowhere to put one.

## About the delay

The gap between the needle and the sound is 2 to 6 seconds, and it cannot be tuned away. Sonos treats the stream as internet radio and buffers it the way it buffers any station, which is also the reason it never skips. Capture and encoding add well under half a second. The rest is the players' own buffer. You are not listening along with the stylus, you are listening a few seconds behind it. For playing a record across the house that is fine. For monitoring while you cut one, it is not.

## Security

needledrop is open on the local network on purpose. Anyone on your LAN can open the dashboard and start or stop playback. That is the same deal Sonos already gives you: Sonos control is unauthenticated UPnP, so anyone on the network can already drive your speakers from any Sonos app. needledrop adds no new exposure and stores no secrets. The config is room names and threshold numbers, nothing more.

If your network has people you do not trust on it, put the dashboard behind a reverse proxy with a login (Caddy `basic_auth`, nginx `auth_request`, Authelia, and so on) and firewall the two ports to the proxy. The speakers only need to reach the stream port (`stream.port + 1`).

## Troubleshooting

Playback never starts or never stops. This is the thresholds nine times out of ten. Use the calibration wizard on the dashboard. It measures your needle-up noise floor and a silent groove and sets `music_on_db` and `music_off_db` for your gear.

The `xruns` counter keeps climbing (in the footer and at `/healthz`). Those are capture overruns, meaning samples arrived faster than they were read. A few now and then are harmless. Steady growth points at a starved USB bus (on a Pi, use a powered hub), CPU contention, or a desktop sound server fighting for the device.

Which room should be the coordinator. A wired or newer player. The coordinator is the single player that pulls the stream from needledrop and then feeds it to the rest of the group, so make it the strongest link, not a battery speaker on the far side of the house.

Notes for old firmware. Quick connect and disconnect probes in the log before playback are normal. Players probe the stream (sometimes from a different address in the fleet) before they tune in. needledrop serves the audio with raw HTTP/1.0 framing, no chunked encoding, and frame-aligned starts, because the oldest Play:1 firmware rejects anything else. Confirmed working on firmware 86.7.

PipeWire and PortAudio. On desktop-style systems the PipeWire PortAudio shim can deliver a fraction of real time with a flood of xruns. That is why the `arecord` subprocess backend is the default: it talks to ALSA directly and does not care which sound server is running.

## Development

```bash
git clone https://github.com/justintevya/needledrop && cd needledrop
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
pytest -q          # full suite, no hardware or network needed
ruff check .
```

Where things live (`src/needledrop/`):

- `cli.py`, the `run`, `setup`, and `version` commands
- `config.py`, dataclasses and YAML load, validate, save
- `devices.py`, USB vendor:product to ALSA card resolution through sysfs
- `capture.py`, the capture supervisor with unplug and backoff recovery
- `ringbuf.py`, a thread-safe PCM ring buffer
- `detect.py`, a hand-rolled biquad high-pass with RMS and hysteresis
- `state.py`, the IDLE, SENSING, PLAYING, GRACE machine with an injectable clock
- `encode.py`, the lameenc MP3 wrapper
- `stream.py`, the listener hub and raw-framing stream server
- `sonos.py`, SoCo grouping, busy-skip, watchdog, and volume restore
- `web.py`, REST, websocket, healthz, and the static dashboard
- `app.py`, the composition root
- `units.py`, systemd unit and udev rule text

Pull requests are welcome. Good starting points: an Opus or AAC encoder backend (the encoder already sits behind an interface), MQTT or Home Assistant state publishing, and support for a split S1 and S2 fleet. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
