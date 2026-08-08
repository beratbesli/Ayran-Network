# Beer-Network

Beer-Network is a responsive Linux terminal dashboard for observing global and
per-process network activity. It is built with Python, Textual, psutil, and
asyncio.

## Current capabilities

- Live upload and download rates with compact sparklines
- Per-process connection and estimated traffic views
- Non-blocking background sampling with a responsive Textual interface
- Clear warning states when Linux permissions limit process visibility

## Roadmap

- Automatic focus mode for games and other configured applications
- Process terminate and suspend actions with confirmation
- Cached Geo-IP country flags for public remote addresses
- Optional Groq or OpenAI-compatible local LLM safety analysis

## Requirements

- Python 3.10 or newer
- Linux (Kubuntu and similar distributions are the primary target)

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

## Run

```bash
python -m beer_network
```

Installing the package also provides the `beer-network` command. Press `r` to
request an immediate refresh and `q` to quit.

Per-process rates are estimates based on each visible connection's activity
state. psutil provides measured host-wide byte counters, but it does not expose
per-process network byte counters. The interface labels estimated values
accordingly.

## Privacy

Network data remains local unless the user explicitly requests Geo-IP or AI
analysis. Optional integrations use short timeouts and can be disabled entirely.

## License

MIT
