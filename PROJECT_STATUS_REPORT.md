# Beer-Network Project Status Report

Date: 2026-08-09

## Executive Summary

Beer-Network is a Linux-first terminal user interface for real-time network
traffic monitoring. It is built with Python 3, Textual, psutil, httpx, and
asyncio. The project currently provides a responsive TUI dashboard, global
network throughput metrics, process-level connection visibility, focus-mode
partitioning for games and heavy applications, guarded process control actions,
Geo-IP country flag enrichment, and optional AI-assisted process analysis.

The repository is version-controlled with Git and synchronized with the GitHub
remote repository:

```text
https://github.com/beratbesli/Beer-Network.git
```

The latest known safe project checkpoint is:

```text
b3ab930 feat: add optional AI process analysis
```

At the last safety stop, the local `main` branch matched `origin/main`, the
working tree was clean, and no Beer-Network Python process was left running.

## Project Purpose

Beer-Network is intended to help Linux users understand what is happening on
their network in real time without leaving the terminal. Its main goals are:

- Show global upload and download rates in a compact live dashboard.
- Display which visible processes own active internet sockets.
- Keep the interface responsive while psutil scans run in the background.
- Highlight heavy or game-like applications separately from background traffic.
- Allow carefully confirmed terminate and suspend actions for selected
  processes.
- Add remote-country context for public IP addresses.
- Offer optional advisory AI analysis when explicitly requested by the user.

The project is intentionally defensive about Linux permissions. If psutil cannot
see every process or socket, the application should degrade gracefully, show
warnings, and keep running.

## Current Architecture

The project is organized as a small Python package:

```text
beer_network/
  __init__.py
  __main__.py
  app.py
  ai_analysis.py
  backend.py
  focus.py
  geoip.py
  process_control.py
tests/
  test_ai_analysis.py
  test_app.py
  test_backend.py
  test_focus.py
  test_geoip.py
  test_process_control.py
```

### Backend Layer

`beer_network/backend.py` contains the asynchronous psutil-backed sampling
engine. It collects:

- Host-wide network byte counters.
- Upload and download rates based on elapsed monotonic time.
- Process metadata such as PID, name, user, status, and create time.
- Internet socket information such as local endpoint, remote endpoint, socket
  family, socket type, and connection state.
- Permission warnings when data is partial or unavailable.

The backend exposes immutable snapshot dataclasses so the UI can safely render a
coherent point-in-time view.

Important limitation: psutil does not expose true per-process network byte
counters. Beer-Network therefore labels per-process rates as estimated values.
The current estimate is based on visible connection activity share, not measured
per-PID byte accounting.

### Textual TUI Layer

`beer_network/app.py` contains the Textual application. It provides:

- Header and footer controls.
- Upload and download dashboard panels.
- ASCII-style sparklines for live speed history.
- Process tables with responsive column schemas.
- Warning and error status messages.
- Manual refresh through the `R` key.
- Geo-IP toggle through the `G` key.
- Process terminate and suspend actions through `K` and `S`.
- Optional AI analysis through `A`.

The UI uses background workers for sampling, Geo-IP lookup, process actions, and
AI analysis so the event loop remains responsive.

### Focus Mode

`beer_network/focus.py` classifies processes into focused and background groups.
It recognizes BeamNG.drive, Elden Ring, and Fortnite variants by default. The
default list can be replaced or extended with `BEER_NETWORK_FOCUS_APPS`.

When focus mode is active, the TUI splits visible processes into:

- Focused application traffic.
- Background traffic.

### Process Control

`beer_network/process_control.py` provides guarded terminate and suspend
operations. The UI confirms each action before execution and passes the expected
process name and create time where available. This reduces the chance of acting
on a reused PID.

### Geo-IP Enrichment

`beer_network/geoip.py` resolves public remote IP addresses to countries and
flag emoji. It avoids local, private, reserved, and invalid addresses. Results
are cached and lookups are bounded with timeouts and limited concurrency.

Geo-IP can be disabled at startup with:

```bash
export BEER_NETWORK_GEOIP_ENABLED=0
```

It can also be toggled at runtime with `G`.

### Optional AI Analysis

`beer_network/ai_analysis.py` provides optional advisory analysis using either:

- Groq, configured with `GROQ_API_KEY`.
- An OpenAI-compatible local endpoint, configured with
  `BEER_NETWORK_LLM_BASE_URL` and `BEER_NETWORK_LLM_MODEL`.

AI analysis is never automatic. The user must select a process and press `A`.
The request intentionally excludes PID, username, and remote IP addresses. It
sends bounded process behavior fields such as process name, status, ports,
socket states, connection counts, and estimated rates.

The implementation includes terminal-text sanitization, response-size limits,
restricted local HTTP handling, and an inert disabled state when configuration
is missing or invalid.

## Completed Milestones

### Step 1: Project Scaffold

Commit:

```text
6d71d3f chore: scaffold Beer-Network project
```

Completed work:

- Initialized the Git repository.
- Added Python `.gitignore`.
- Added `requirements.txt` and `requirements-dev.txt`.
- Added `pyproject.toml` with package metadata, dependencies, script entry
  point, pytest settings, Ruff settings, and strict MyPy settings.
- Added `.env.example`.
- Added initial README documentation.
- Added base package files.
- Connected the repository to the GitHub remote.
- Pushed the initial commit.

### Step 2: Async Network Backend

Commit:

```text
cd787c4 feat: add asynchronous traffic backend
```

Completed work:

- Added immutable snapshot models.
- Added global upload and download rate calculation.
- Added psutil process and socket collection.
- Added connection normalization for IPv4, IPv6, UDP, listening sockets, and
  missing remote endpoints.
- Added graceful handling for `AccessDenied`, `NoSuchProcess`, `ZombieProcess`,
  and OS-level psutil failures.
- Added process identity support with PID and create time.
- Added explicit estimated per-process traffic fields.
- Added deterministic backend tests.

### Step 3: Live Textual Dashboard

Commit:

```text
4630db3 feat: build live Textual dashboard
```

Completed work:

- Added the Textual application.
- Added live global upload and download panels.
- Added speed formatting.
- Added sparklines.
- Added process table rendering.
- Added immediate refresh binding.
- Added CLI module and package script entry point.
- Added fake-backend UI tests.

### Step 4: Focus Mode, Process Control, and Geo-IP

Commit:

```text
27780ad feat: add focus controls and Geo-IP
```

Completed work:

- Added focus-mode process classification.
- Added configurable focus application matching.
- Added focused and background process table rendering.
- Added guarded terminate and suspend actions.
- Added confirmation modal flow.
- Added Geo-IP resolver with caching and safe address filtering.
- Added Geo-IP TUI integration and runtime toggle.
- Added responsive table schemas for narrower terminals.
- Added tests for focus classification, process control, Geo-IP, and UI
  integration.

### Step 5: Optional AI Process Analysis

Commit:

```text
b3ab930 feat: add optional AI process analysis
```

Completed work:

- Added optional AI analysis service.
- Added Groq provider support.
- Added OpenAI-compatible local endpoint support.
- Added disabled and invalid-configuration behavior that does not break app
  startup.
- Added strict request shaping so sensitive fields are not sent.
- Added terminal-text sanitization for AI input and output.
- Added bounded response handling.
- Added AI analysis modal to the TUI.
- Added UI worker cancellation and cleanup handling.
- Added tests for service behavior, safety cases, and UI integration.

## Completed Milestones

### Step 1: Project Scaffold

Commit:
```text
6d71d3f chore: scaffold Beer-Network project
```

### Step 2: Async Network Backend

Commit:
```text
cd787c4 feat: add asynchronous traffic backend
```

### Step 3: Live Textual Dashboard

Commit:
```text
4630db3 feat: build live Textual dashboard
```

### Step 4: Focus Mode, Process Control, and Geo-IP

Commit:
```text
27780ad feat: add focus controls and Geo-IP
```

### Step 5: Optional AI Process Analysis

Commit:
```text
b3ab930 feat: add optional AI process analysis
```

### Step 6: Textual Compatibility & Process Resume

Commits:
```text
1424100 fix: resolve Textual CSS compatibility and raise minimum version to 1.0.0
cd7c8a8 feat: add resume action for suspended processes
```

Completed work:
- Fixed `.metric-panel:first-child` CSS selector to `.metric-panel:first-of-type` for Textual compatibility.
- Raised minimum Textual dependency in `pyproject.toml` to `textual>=1.0.0`.
- Added process `resume` action to `ProcessController` and `process_control.py`.
- Integrated `U` key binding for resuming suspended processes.
- Added tests for process resume action.

### Step 7: Process Details Modal & Snapshot Export

Commit:
```text
2c2c8e1 feat: add process details modal and snapshot export (JSON/CSV)
```

Completed work:
- Added read-only `ProcessDetailsScreen` modal accessible via `D` key showing detailed process sockets, ports, and connection counts.
- Added `export_json` and `export_csv` functions in `beer_network/export.py`.
- Integrated `E` key binding to export snapshots to `~/.beer-network/exports/`.
- Added unit tests for JSON and CSV snapshot export.

### Step 8: Network Interface Filtering

Commit:
```text
51b42d6 feat: add network interface filtering
```

Completed work:
- Created `beer_network/interface_filter.py` with `InterfaceFilter` class.
- Added support for `BEER_NETWORK_INTERFACE_FILTER` (`no-virtual`, `exclude:...`, `include:...`).
- Added unit tests for interface filtering logic.

### Step 9: TOML Configuration File Support

Commit:
```text
587871f feat: add TOML configuration file support
```

Completed work:
- Created `beer_network/config.py` with `BeerNetworkConfig` and `load_config()`.
- Added search order: `BEER_NETWORK_CONFIG` env, `~/.config/beer-network/config.toml`, `~/.beer-network/config.toml`, `./beer-network.toml`.
- Created sample configuration template `beer-network.example.toml`.
- Added unit tests for TOML configuration parsing.

### Step 10: GitHub Actions CI Workflow & Type Fixes

Commit:
```text
e71e7a3 ci: add GitHub Actions workflow and fix strict mypy types
```

Completed work:
- Added `.github/workflows/ci.yml` matrix testing Python 3.10, 3.11, and 3.12.
- Verified strict MyPy and Ruff linting compliance across all 11 package and test modules.

## Current Verification Status

```text
137 tests passed in 7.48s
ruff check passed
strict mypy passed
git diff clean
```

The safe-stop verification confirmed:

```text
main == origin/main
working tree clean
ahead/behind: 0 / 0
no Beer-Network Python process running
```

## Keyboard Controls

```text
R  refresh immediately
G  toggle Geo-IP enrichment
K  terminate the selected process after confirmation
S  suspend the selected process after confirmation
U  resume the selected suspended process after confirmation
D  view read-only process details modal
E  export snapshot to JSON Lines and CSV (~/.beer-network/exports/)
A  analyze the selected process with the configured AI provider
Q  quit
```

## Current Repository Checkpoints

```text
e71e7a3 ci: add GitHub Actions workflow and fix strict mypy types
587871f feat: add TOML configuration file support
51b42d6 feat: add network interface filtering
2c2c8e1 feat: add process details modal and snapshot export (JSON/CSV)
cd7c8a8 feat: add resume action for suspended processes
1424100 fix: resolve Textual CSS compatibility and raise minimum version to 1.0.0
422bbda docs: add project status report
b3ab930 feat: add optional AI process analysis
27780ad feat: add focus controls and Geo-IP
4630db3 feat: build live Textual dashboard
cd787c4 feat: add asynchronous traffic backend
6d71d3f chore: scaffold Beer-Network project
```

## Final Status

Beer-Network is a feature-complete, fully tested Linux terminal network monitoring dashboard with comprehensive unit test coverage (137 tests passing), strict MyPy typing, clean Ruff formatting, GitHub Actions CI automation, process control actions (kill, suspend, resume), Geo-IP flags, optional AI analysis, snapshot export (JSON/CSV), network interface filtering, and TOML configuration file support. All code changes have been pushed to GitHub.

