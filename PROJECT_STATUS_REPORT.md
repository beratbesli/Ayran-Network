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

## Current Verification Status

The last recorded full validation before the safe stop reported:

```text
121 tests passed
ruff check passed
ruff format check passed
strict mypy passed
compileall passed
git diff check passed
```

The safe-stop verification confirmed:

```text
main == origin/main
working tree clean
ahead/behind: 0 / 0
no Beer-Network Python process running
```

This report itself should be treated as a new documentation update after that
safe checkpoint.

## Known Limitations

### Per-Process Traffic Is Estimated

psutil provides host-wide byte counters and process-owned socket visibility, but
not true per-process network byte counters. Beer-Network currently estimates
per-process traffic based on visible connection activity. This is useful for
orientation, but it is not equivalent to measured per-process bandwidth.

Future true per-process accounting would likely require one of:

- eBPF-based measurement.
- cgroup-based accounting.
- A nethogs-like privileged backend.
- Kernel-level packet attribution.

### Textual Minimum Version Compatibility

`pyproject.toml` currently declares:

```toml
textual>=0.85.0
```

The application has been validated primarily against Textual 8.2.8. A reviewer
found that the committed CSS selector `.metric-panel:first-child` may fail on
Textual 0.85.0, while `.metric-panel:first-of-type` appears to be a compatible
alternative. This compatibility issue was not committed because the project was
stopped at the last safe pushed checkpoint.

Recommended next action:

- Either test and fix the CSS for Textual 0.85.0 through the latest supported
  version.
- Or raise the minimum Textual dependency to the version range that the current
  UI is actually tested against.

### Real-System Validation Is Still Needed

Most validation so far has been automated and headless. The project still needs
longer live testing on Kubuntu with real processes, real network traffic, and
different permission levels.

Important cases to test:

- Running as a normal user.
- Running with elevated privileges.
- Running when some process details are hidden.
- Running with VPN, loopback, bridge, and container interfaces.
- Running while processes start and exit rapidly.
- Running in a small terminal such as 40x12.
- Running in a standard terminal such as 80x24.

### Optional Service End-to-End Testing

Geo-IP and AI paths have automated tests, but production end-to-end validation
is still pending.

Remaining checks:

- Real ipwho.is lookup behavior over time.
- Geo-IP timeout and rate-limit behavior during extended sessions.
- Real Groq API request and response behavior with a valid key.
- Real local LLM endpoint behavior against a local OpenAI-compatible server.

### Packaging Smoke Tests Are Pending

The package metadata exists, and the `beer-network` entry point is configured.
However, a clean build/install smoke test should still be performed.

Recommended checks:

```bash
python -m build
python -m pip install dist/*.whl
beer-network
python -m beer_network
```

## Recommended Future Work

### High Priority

- Resolve the Textual minimum-version compatibility issue.
- Add a CI matrix for Python 3.10 through the newest supported Python version.
- Add dependency matrix coverage for psutil 5.9.8 and latest psutil.
- Add Textual compatibility testing for the declared minimum and current latest
  version.
- Perform package build and clean-install smoke tests.
- Run a manual Kubuntu live-session test as both a normal user and with elevated
  permissions.

### Medium Priority

- Improve table rendering performance by updating rows incrementally instead of
  clearing and rebuilding whole tables every refresh.
- Preserve scroll position more accurately across refreshes.
- Add interface filtering so users can include or exclude loopback, VPN, bridge,
  Docker, or virtual interfaces.
- Add configuration file support in addition to environment variables.
- Add a persistent settings screen inside the TUI.
- Add export options for snapshots, such as JSON Lines or CSV.
- Add a read-only process details modal showing all sockets for the selected
  process.
- Add a safer resume action for suspended processes if the UI will support
  suspend as a first-class workflow.

### Advanced Future Work

- Add an eBPF or cgroup backend for true per-process traffic measurement.
- Add DNS reverse lookup with strict caching and privacy controls.
- Add ASN and organization enrichment for public remote IPs.
- Add traffic anomaly detection without requiring an external AI provider.
- Add optional local-only AI prompt templates for security review workflows.
- Add profiles for gaming, streaming, development, and privacy-first operation.
- Add snapshot replay mode for debugging and demos.

## Operational Notes

### Running the App

Development setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Run from source:

```bash
python -m beer_network
```

Run after installation:

```bash
beer-network
```

### Useful Environment Variables

Focus mode:

```bash
export BEER_NETWORK_FOCUS_APPS="+MyGame,AnotherGame.exe"
```

Disable Geo-IP:

```bash
export BEER_NETWORK_GEOIP_ENABLED=0
```

Enable Groq AI analysis:

```bash
export GROQ_API_KEY="your-key"
export BEER_NETWORK_GROQ_MODEL="llama-3.3-70b-versatile"
```

Enable local OpenAI-compatible AI analysis:

```bash
export BEER_NETWORK_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export BEER_NETWORK_LLM_MODEL="your-local-model"
```

### Keyboard Controls

```text
R  refresh immediately
G  toggle Geo-IP enrichment
K  terminate the selected process after confirmation
S  suspend the selected process after confirmation
A  analyze the selected process with the configured AI provider
Q  quit
```

## Suggested Next Safe Development Step

The next safest development step is to fix or explicitly re-scope Textual
version support. This should be done before adding new features, because a
declared dependency incompatibility can prevent the application from launching
for users who install the minimum allowed version.

Recommended implementation path:

1. Create a compatibility test environment with Textual 0.85.0.
2. Replace incompatible CSS selectors or APIs with public alternatives.
3. Re-run the app tests on Textual 0.85.0 and latest Textual.
4. If compatibility requires too many compromises, raise the minimum Textual
   dependency and document the tested version range.
5. Commit and push the compatibility decision as its own small milestone.

## Current Repository Checkpoints

```text
b3ab930 feat: add optional AI process analysis
27780ad feat: add focus controls and Geo-IP
4630db3 feat: build live Textual dashboard
cd787c4 feat: add asynchronous traffic backend
6d71d3f chore: scaffold Beer-Network project
```

## Final Status

Beer-Network is a functional, tested prototype with a coherent architecture and
several advanced features already implemented. The largest remaining technical
risk is not the core architecture; it is compatibility and real-system
validation. Before building more features, the project should lock down its
supported Textual version range, run clean install tests, and perform live
Kubuntu validation under realistic permissions and traffic.
