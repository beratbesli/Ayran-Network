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
- `U`: resume the selected suspended process after confirmation
- `D`: view read-only process details modal (sockets, ports, states)
- `E`: export snapshot to JSON Lines and CSV (`~/.beer-network/exports/`)

- `Q`: quit

## Configuration file

Beer-Network supports a TOML configuration file at `~/.config/beer-network/config.toml` or `~/.beer-network/config.toml`. See `beer-network.example.toml` for options. You can also specify a custom path with `BEER_NETWORK_CONFIG`:

```bash
export BEER_NETWORK_CONFIG="/path/to/config.toml"
```

## Interface filtering

Exclude or include specific network interfaces by setting `BEER_NETWORK_INTERFACE_FILTER`:

```bash
# Exclude common virtual/container/VPN interfaces (docker, veth, lo, tun, etc.)
export BEER_NETWORK_INTERFACE_FILTER="no-virtual"

# Exclude custom interfaces by regex
export BEER_NETWORK_INTERFACE_FILTER="exclude:docker,veth,virbr"
```

## Focus mode configuration

Browsers and system tools are recognized by
default (chrome, firefox, apt, pacman, etc.). Set `BEER_NETWORK_FOCUS_APPS` to a comma-separated list to replace the
defaults, or prefix the value with `+` to extend them:

```bash
export BEER_NETWORK_FOCUS_APPS="+MyGame,AnotherGame.exe"
```

Matching is case-insensitive and ignores executable suffixes and punctuation.

Per-process network utilization is tracked via an "Activity Score" based on each visible connection's activity
state. psutil provides measured host-wide byte counters, but it does not expose
per-process network byte counters. The interface labels processes with this activity score instead of bytes per second.

## Privacy

Geo-IP enrichment is enabled by default and sends only public remote IP addresses
to the HTTPS [ipwho.is API](https://ipwhois.io/documentation). Private, local,
reserved, and invalid addresses are never sent. Press `G` to disable enrichment
at runtime or start with `BEER_NETWORK_GEOIP_ENABLED=0`. Results are cached and
lookups use bounded concurrency and short timeouts.


## License

MIT
