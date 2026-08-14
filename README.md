# 🍺 Beer-Network

<div align="center">

[![CI](https://github.com/beratbesli/Beer-Network/actions/workflows/ci.yml/badge.svg)](https://github.com/beratbesli/Beer-Network/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![UI: Textual](https://img.shields.io/badge/TUI-Textual-00d2ff.svg)](https://textual.textualize.io/)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](https://www.kernel.org/)

**A modern, responsive Linux terminal dashboard for real-time network traffic observation, per-process connection tracking, and bandwidth diagnostics.**

[Key Features](#-key-features) •
[Installation](#-installation) •
[Keyboard Shortcuts](#-keyboard-shortcuts) •
[Configuration](#%EF%B8%8F-configuration) •
[Development](#-development)

</div>

---

## ⚡ Overview

**Beer-Network** brings an intuitive, asynchronous Terminal User Interface (TUI) to your shell for monitoring live network metrics on Linux. Built with **Python 3.10+**, **Textual**, **psutil**, and **asyncio**, it delivers responsive, low-overhead network diagnostics right inside your terminal.

```text
┌─ Beer-Network ────────────────────────────────────────────────────────┐
│  ▼ Download: 14.2 MB/s  [ ▂▃▅▆▇█]   ▲ Upload: 1.8 MB/s   [   ▂▃▅]     │
├───────────────────────────────────────────────────────────────────────┤
│  Focused Applications                                                 │
│  PID    PROCESS       TRAFFIC LEVEL  CONNECTIONS   DESTINATIONS       │
│  4210   firefox       ● HIGH         18            🇩🇪 DE, 🇺🇸 US, 🇳🇱 NL │
│  8832   steam         ● MEDIUM        6            🇺🇸 US              │
│                                                                       │
│  Background Services & Daemons                                        │
│  PID    PROCESS       TRAFFIC LEVEL  CONNECTIONS   DESTINATIONS       │
│  1042   tailscaled    ● LOW           4            🇬🇧 GB              │
│  1190   systemd-resolved ● IDLE       2            -                  │
└───────────────────────────────────────────────────────────────────────┘
```

---

## ✨ Key Features

- 📊 **Real-Time Bandwidth Sparklines**: Live host-wide upload and download rate meters featuring animated Unicode sparklines and cumulative transfer counters.
- 🎯 **Intelligent Focus Mode**: Automatically separates interactive foreground applications (browsers, games, media apps) from background system daemons for cleaner monitoring.
- 🌍 **Privacy-Conscious Geo-IP Resolution**: Enriches public remote endpoints with ISO country codes and flag emojis. Local cached lookups with strict timeout guards and zero leakage of private/local IP subnets.
- 🔍 **Live Search & Filter**: Instant dynamic filtering across process names and PIDs by pressing <kbd>F</kbd>.
- 🛡️ **Guarded Process Management**: Safely Suspend (<kbd>S</kbd>), Resume (<kbd>U</kbd>), or Terminate (<kbd>K</kbd>) misbehaving network consumers directly from the TUI with built-in confirmation dialogs.
- 🔬 **Deep Process Inspection Modal**: Press <kbd>D</kbd> to inspect complete socket connection trees, local/remote IP endpoints, port numbers, connection states (`ESTABLISHED`, `LISTEN`, `TIME_WAIT`), and traffic score histories.
- 📁 **Snapshot Data Export**: One-touch export (<kbd>E</kbd>) of complete system network state snapshots to structured **JSON Lines** and **CSV** files (`~/.beer-network/exports/`).
- 🎛️ **Interface Filtering**: Support for excluding virtual bridges, container interfaces (`docker`, `veth`, `virbr`), and loopback devices.
- ⚡ **Asynchronous & Non-Blocking**: High-frequency sampling runs on a dedicated background loop ensuring butter-smooth UI responsiveness without terminal stutter.

---

## 🚀 Installation

### Prerequisites

- **OS**: Linux (Ubuntu, Debian, Fedora, Arch, Kubuntu, etc.)
- **Python**: 3.10 or newer

### Setup via Virtual Environment

```bash
# 1. Clone repository
git clone https://github.com/beratbesli/Beer-Network.git
cd Beer-Network

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install in editable mode with dependencies
pip install -e .
```

### Running Beer-Network

Run directly as a module or through the console entry point:

```bash
# Run with console command
beer-network

# Or run via Python module
python3 -m beer_network
```

---

## ⌨️ Keyboard Shortcuts

| Key | Action | Description |
| :--- | :--- | :--- |
| <kbd>F</kbd> | **Search** | Filter process list in real time by name or PID |
| <kbd>Esc</kbd> | **Clear** | Clear active search filter and restore full list |
| <kbd>D</kbd> | **Details** | Open comprehensive process inspection modal |
| <kbd>G</kbd> | **Geo-IP** | Toggle Geo-IP remote address enrichment on / off |
| <kbd>K</kbd> | **Kill** | Terminate selected process (with confirmation) |
| <kbd>S</kbd> | **Suspend** | Pause / freeze selected process execution |
| <kbd>U</kbd> | **Resume** | Unfreeze selected process |
| <kbd>E</kbd> | **Export** | Save current network snapshot to CSV and JSONL |
| <kbd>R</kbd> | **Refresh** | Request immediate data sample refresh |
| <kbd>Q</kbd> | **Quit** | Exit Beer-Network |

---

## ⚙️ Configuration

Beer-Network supports configuration via TOML files and environment variables.

### Configuration Files

Beer-Network checks configuration candidates in the following order:
1. File specified by `BEER_NETWORK_CONFIG` environment variable
2. `~/.config/beer-network/config.toml`
3. `~/.beer-network/config.toml`
4. `./beer-network.toml`

### Example `config.toml`

Copy `beer-network.example.toml` to `~/.config/beer-network/config.toml`:

```toml
[general]
poll_interval = 1.0     # Refresh interval in seconds
history_size = 60       # Historical data points for rate history

[focus]
# Highlight specific games, browsers, or streaming software
apps = ["BeamNG.drive", "Elden Ring", "Fortnite", "steam", "discord", "firefox", "chrome"]
extend_defaults = true  # Merge with built-in app defaults

[geoip]
enabled = true          # Enable remote IP country lookup & flags

[interface]
filter = "no-virtual"   # Options: "no-virtual", "exclude:docker,veth", "include:eth0"

[export]
directory = "~/.beer-network/exports"
```

### Environment Variables

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `BEER_NETWORK_CONFIG` | `string` | `""` | Explicit path to TOML configuration file |
| `BEER_NETWORK_GEOIP_ENABLED` | `bool` | `true` | Toggle Geo-IP enrichment (`1`/`0`, `true`/`false`) |
| `BEER_NETWORK_FOCUS_APPS` | `string` | `""` | Comma-separated app names (prefix with `+` to extend) |
| `BEER_NETWORK_INTERFACE_FILTER` | `string` | `""` | Filter mode (`no-virtual`, `exclude:...`, `include:...`) |
| `BEER_NETWORK_EXPORT_DIR` | `string` | `~/.beer-network/exports` | Output directory for snapshot exports |

---

## 🔒 Permissions & Visibility

Under Linux security models, standard unprivileged users can only inspect network sockets belonging to processes owned by their user.

To observe system-wide sockets from all system processes:
```bash
# Option A: Run with sudo
sudo beer-network

# Option B: Or set cap_net_admin / cap_sys_ptrace on your python binary if preferred
```

---

## 🧪 Development

### Install Development Dependencies

```bash
pip install -e ".[dev]"
```

### Code Quality & Testing Suite

```bash
# Run pytest test suite (100+ unit & integration tests)
pytest

# Run fast linter checks
ruff check .

# Run strict static type analysis
mypy beer_network tests
```

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).
