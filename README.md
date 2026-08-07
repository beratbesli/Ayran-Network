# Beer-Network

Beer-Network is a responsive Linux terminal dashboard for observing global and
per-process network activity. It is built with Python, Textual, psutil, and
asyncio.

## Planned capabilities

- Live upload and download rates with compact sparklines
- Per-process connection and estimated traffic views
- Automatic focus mode for games and other configured applications
- Process terminate and suspend actions with confirmation
- Cached Geo-IP country flags for public remote addresses
- Optional Groq or OpenAI-compatible local LLM safety analysis
- Graceful degradation when process details require elevated permissions

## Requirements

- Python 3.10 or newer
- Linux (Kubuntu and similar distributions are the primary target)

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Runtime and usage instructions will be completed as the application modules are
implemented.

## Privacy

Network data remains local unless the user explicitly requests Geo-IP or AI
analysis. Optional integrations use short timeouts and can be disabled entirely.

## License

MIT

