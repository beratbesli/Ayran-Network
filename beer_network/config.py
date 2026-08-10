"""Configuration file support for Beer-Network."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]


__all__ = ["BeerNetworkConfig", "load_config"]

_CONFIG_PATHS: Final[tuple[Path, ...]] = (
    Path.home() / ".config" / "beer-network" / "config.toml",
    Path.home() / ".beer-network" / "config.toml",
    Path("beer-network.toml"),
)

_CONFIG_PATH_ENV: Final = "BEER_NETWORK_CONFIG"


@dataclass(frozen=True, slots=True)
class BeerNetworkConfig:
    """Parsed application configuration."""

    # General
    poll_interval: float = 1.0
    history_size: int = 60

    # Focus mode
    focus_apps: tuple[str, ...] = ()
    focus_extend_defaults: bool = True

    # Geo-IP
    geoip_enabled: bool = True

    # Interface filter
    interface_filter: str = ""

    # AI Analysis
    groq_api_key: str = ""
    groq_model: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""

    # Export
    export_dir: str = ""

    # Source file path (for informational purposes)
    source_path: str | None = None


def load_config(
    config_path: str | Path | None = None,
) -> BeerNetworkConfig:
    """Load configuration from a TOML file.

    Search order:
    1. Explicit config_path argument
    2. BEER_NETWORK_CONFIG environment variable
    3. Default paths: ~/.config/beer-network/config.toml,
       ~/.beer-network/config.toml, ./beer-network.toml
    """
    path = _find_config_file(config_path)
    if path is None or tomllib is None:
        return BeerNetworkConfig()

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, getattr(tomllib, "TOMLDecodeError", Exception)):
        return BeerNetworkConfig(source_path=str(path))

    return _parse_config(data, source_path=str(path))


def _find_config_file(
    explicit_path: str | Path | None,
) -> Path | None:
    if explicit_path is not None:
        path = Path(explicit_path)
        return path if path.is_file() else None

    env_path = os.environ.get(_CONFIG_PATH_ENV, "").strip()
    if env_path:
        path = Path(env_path)
        return path if path.is_file() else None

    for candidate in _CONFIG_PATHS:
        if candidate.is_file():
            return candidate

    return None


def _parse_config(
    data: dict[str, Any],
    source_path: str | None = None,
) -> BeerNetworkConfig:
    general = data.get("general", {})
    focus = data.get("focus", {})
    geoip = data.get("geoip", {})
    interface = data.get("interface", {})
    ai = data.get("ai", {})
    export = data.get("export", {})

    focus_apps_raw = focus.get("apps", [])
    if isinstance(focus_apps_raw, str):
        focus_apps = tuple(app.strip() for app in focus_apps_raw.split(",") if app.strip())
    elif isinstance(focus_apps_raw, list):
        focus_apps = tuple(str(app) for app in focus_apps_raw if app)
    else:
        focus_apps = ()

    return BeerNetworkConfig(
        poll_interval=_float_value(general, "poll_interval", 1.0),
        history_size=_int_value(general, "history_size", 60),
        focus_apps=focus_apps,
        focus_extend_defaults=_bool_value(focus, "extend_defaults", True),
        geoip_enabled=_bool_value(geoip, "enabled", True),
        interface_filter=_str_value(interface, "filter", ""),
        groq_api_key=_str_value(ai, "groq_api_key", ""),
        groq_model=_str_value(ai, "groq_model", ""),
        llm_base_url=_str_value(ai, "llm_base_url", ""),
        llm_model=_str_value(ai, "llm_model", ""),
        llm_api_key=_str_value(ai, "llm_api_key", ""),
        export_dir=_str_value(export, "directory", ""),
        source_path=source_path,
    )


def _str_value(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key)
    return str(value).strip() if value is not None else default


def _int_value(section: dict[str, Any], key: str, default: int) -> int:
    value = section.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _float_value(section: dict[str, Any], key: str, default: float) -> float:
    value = section.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _bool_value(section: dict[str, Any], key: str, default: bool) -> bool:
    value = section.get(key)
    if isinstance(value, bool):
        return value
    return default
