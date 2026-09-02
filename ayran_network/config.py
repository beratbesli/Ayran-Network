"""Configuration file support for Ayran-Network."""

from __future__ import annotations

import logging
import math
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


__all__ = ["AyranNetworkConfig", "load_config"]

_CONFIG_PATHS: Final[tuple[Path, ...]] = (
    Path.home() / ".config" / "ayran-network" / "config.toml",
    Path.home() / ".ayran-network" / "config.toml",
    Path("ayran-network.toml"),
)

_CONFIG_PATH_ENV: Final = "AYRAN_NETWORK_CONFIG"
_LOGGER = logging.getLogger(__name__)
_DEFAULT_GEOIP_ENDPOINT: Final = "https://ipwho.is/{encoded_ip}"


@dataclass(frozen=True, slots=True)
class AyranNetworkConfig:
    """Parsed application configuration."""

    # General
    poll_interval: float = 1.0
    history_size: int = 60

    # Focus mode
    focus_apps: tuple[str, ...] = ()
    focus_extend_defaults: bool = True

    # Geo-IP
    geoip_enabled: bool = False
    geoip_endpoint: str = _DEFAULT_GEOIP_ENDPOINT

    # Interface filter
    interface_filter: str = ""



    # Export
    export_dir: str = ""

    # Source file path (for informational purposes)
    source_path: str | None = None


def load_config(
    config_path: str | Path | None = None,
) -> AyranNetworkConfig:
    """Load configuration from a TOML file.

    Search order:
    1. Explicit config_path argument
    2. AYRAN_NETWORK_CONFIG environment variable
    3. Default paths: ~/.config/ayran-network/config.toml,
       ~/.ayran-network/config.toml, ./ayran-network.toml
    """
    path = _find_config_file(config_path)
    if path is None or tomllib is None:
        return _apply_environment(AyranNetworkConfig())

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, getattr(tomllib, "TOMLDecodeError", Exception)):
        _LOGGER.warning("Could not read TOML configuration %s; using defaults.", path)
        return _apply_environment(AyranNetworkConfig(source_path=str(path)))

    config = _parse_config(data, source_path=str(path))
    return _apply_environment(config)


def _find_config_file(
    explicit_path: str | Path | None,
) -> Path | None:
    if explicit_path is not None:
        path = Path(explicit_path).expanduser()
        return path if path.is_file() else None

    env_path = os.environ.get(_CONFIG_PATH_ENV, "").strip()
    if env_path:
        path = Path(env_path).expanduser()
        return path if path.is_file() else None

    for candidate in _CONFIG_PATHS:
        if candidate.is_file():
            return candidate

    return None


def _parse_config(
    data: dict[str, Any],
    source_path: str | None = None,
) -> AyranNetworkConfig:
    general = _section(data, "general", source_path)
    focus = _section(data, "focus", source_path)
    geoip = _section(data, "geoip", source_path)
    interface = _section(data, "interface", source_path)
    export = _section(data, "export", source_path)

    focus_apps_raw = focus.get("apps", [])
    if isinstance(focus_apps_raw, str):
        focus_apps = tuple(app.strip() for app in focus_apps_raw.split(",") if app.strip())
    elif isinstance(focus_apps_raw, list):
        focus_apps = tuple(str(app) for app in focus_apps_raw if app)
    else:
        focus_apps = ()

    return AyranNetworkConfig(
        poll_interval=_positive_float_value(general, "poll_interval", 1.0),
        history_size=_history_size_value(general, "history_size", 60),
        focus_apps=focus_apps,
        focus_extend_defaults=_bool_value(focus, "extend_defaults", True),
        geoip_enabled=_bool_value(geoip, "enabled", False),
        geoip_endpoint=_https_endpoint_value(geoip, "endpoint", _DEFAULT_GEOIP_ENDPOINT),
        interface_filter=_str_value(interface, "filter", ""),
        export_dir=_str_value(export, "directory", ""),
        source_path=source_path,
    )


def _str_value(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key)
    return str(value).strip() if value is not None else default


def _https_endpoint_value(section: dict[str, Any], key: str, default: str) -> str:
    value = _str_value(section, key, default)
    if value.startswith("https://") and "{encoded_ip}" in value:
        return value
    if value != default:
        _LOGGER.warning("Invalid Geo-IP endpoint; using the default HTTPS endpoint.")
    return default


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


def _positive_float_value(section: dict[str, Any], key: str, default: float) -> float:
    value = _float_value(section, key, default)
    if not math.isfinite(value) or value <= 0.0:
        _LOGGER.warning("Invalid %s value; using %.1f.", key, default)
        return default
    return value


def _history_size_value(section: dict[str, Any], key: str, default: int) -> int:
    value = _int_value(section, key, default)
    if value < 2:
        _LOGGER.warning("Invalid %s value; using %d.", key, default)
        return default
    return value


def _bool_value(section: dict[str, Any], key: str, default: bool) -> bool:
    value = section.get(key)
    if isinstance(value, bool):
        return value
    return default


def _section(data: dict[str, Any], name: str, source_path: str | None) -> dict[str, Any]:
    value = data.get(name, {})
    if isinstance(value, dict):
        return value
    _LOGGER.warning(
        "Configuration section [%s] in %s must be a table; using defaults.",
        name,
        source_path or "file",
    )
    return {}


def _apply_environment(config: AyranNetworkConfig) -> AyranNetworkConfig:
    values: dict[str, object] = {}
    if (raw := os.environ.get("AYRAN_NETWORK_POLL_INTERVAL")) is not None:
        values["poll_interval"] = _env_positive_float(raw, config.poll_interval)
    if (raw := os.environ.get("AYRAN_NETWORK_HISTORY_SIZE")) is not None:
        values["history_size"] = _env_history_size(raw, config.history_size)
    if (raw := os.environ.get("AYRAN_NETWORK_GEOIP_ENABLED")) is not None:
        values["geoip_enabled"] = _env_bool(raw, config.geoip_enabled)
    if (raw := os.environ.get("AYRAN_NETWORK_GEOIP_ENDPOINT")):
        candidate = raw.strip()
        values["geoip_endpoint"] = (
            candidate
            if candidate.startswith("https://") and "{encoded_ip}" in candidate
            else config.geoip_endpoint
        )
    if (raw := os.environ.get("AYRAN_NETWORK_INTERFACE_FILTER")) is not None:
        values["interface_filter"] = raw.strip()
    if (raw := os.environ.get("AYRAN_NETWORK_EXPORT_DIR")) is not None:
        values["export_dir"] = raw.strip()
    if (raw := os.environ.get("AYRAN_NETWORK_FOCUS_APPS")) is not None:
        normalized = raw.strip()
        extends = normalized.startswith("+")
        if extends:
            normalized = normalized[1:]
        values["focus_apps"] = tuple(part.strip() for part in normalized.split(",") if part.strip())
        values["focus_extend_defaults"] = extends
    return replace(
        config,
        poll_interval=values.get("poll_interval", config.poll_interval),  # type: ignore[arg-type]
        history_size=values.get("history_size", config.history_size),  # type: ignore[arg-type]
        focus_apps=values.get("focus_apps", config.focus_apps),  # type: ignore[arg-type]
        focus_extend_defaults=values.get(
            "focus_extend_defaults", config.focus_extend_defaults
        ),  # type: ignore[arg-type]
        geoip_enabled=values.get("geoip_enabled", config.geoip_enabled),  # type: ignore[arg-type]
        geoip_endpoint=values.get("geoip_endpoint", config.geoip_endpoint),  # type: ignore[arg-type]
        interface_filter=values.get("interface_filter", config.interface_filter),  # type: ignore[arg-type]
        export_dir=values.get("export_dir", config.export_dir),  # type: ignore[arg-type]
    )


def _env_bool(raw: str, default: bool) -> bool:
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    _LOGGER.warning("Invalid boolean environment value %r; using %s.", raw, default)
    return default


def _env_positive_float(raw: str, default: float) -> float:
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0.0 else default


def _env_history_size(raw: str, default: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 2 else default
