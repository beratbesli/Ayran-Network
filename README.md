# Beer-Network

Beer-Network is a responsive Linux terminal dashboard for observing global and
per-process network activity. It is built with Python, Textual, psutil, and
asyncio.

## Current capabilities

- Live upload and download rates with compact sparklines
- Per-process connection and estimated traffic views
- Non-blocking background sampling with a responsive Textual interface
- Clear warning states when Linux permissions limit process visibility
- Automatic focus mode for configured games and heavy applications
- Guarded process terminate and suspend actions with confirmation
- Cached Geo-IP country flags for public remote addresses

## Roadmap

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

Installing the package also provides the `beer-network` command.

## Controls

- `R`: request an immediate refresh
- `G`: toggle Geo-IP enrichment
- `K`: terminate the selected process after confirmation
- `S`: suspend the selected process after confirmation
- `Q`: quit

## Focus mode configuration

BeamNG.drive, Elden Ring, and Fortnite process variants are recognized by
default. Set `BEER_NETWORK_FOCUS_APPS` to a comma-separated list to replace the
defaults, or prefix the value with `+` to extend them:

```bash
export BEER_NETWORK_FOCUS_APPS="+MyGame,AnotherGame.exe"
```

Matching is case-insensitive and ignores executable suffixes and punctuation.

Per-process rates are estimates based on each visible connection's activity
state. psutil provides measured host-wide byte counters, but it does not expose
per-process network byte counters. The interface labels estimated values
accordingly.

## Privacy

Geo-IP enrichment is enabled by default and sends only public remote IP addresses
to the HTTPS [ipwho.is API](https://ipwhois.io/documentation). Private, local,
reserved, and invalid addresses are never sent. Press `G` to disable enrichment
at runtime or start with `BEER_NETWORK_GEOIP_ENABLED=0`. Results are cached and
lookups use bounded concurrency and short timeouts.

AI analysis remains local unless the user explicitly requests it with the AI
shortcut. Optional integrations can be disabled entirely.

## License

MIT
