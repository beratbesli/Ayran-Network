"""Network interface filtering for Ayran-Network."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, TypeVar

__all__ = ["INTERFACE_FILTER_ENV", "InterfaceFilter"]

INTERFACE_FILTER_ENV: Final = "AYRAN_NETWORK_INTERFACE_FILTER"

T = TypeVar("T")

# Common patterns for virtual/container/VPN interfaces
_VIRTUAL_PATTERNS: Final[tuple[str, ...]] = (
    r"^lo$",  # loopback
    r"^docker\d*$",  # Docker bridge
    r"^br-",  # Docker custom bridges
    r"^veth",  # Docker/container veth pairs
    r"^virbr",  # libvirt bridges
    r"^vnet",  # libvirt virtual NICs
    r"^tun\d*$",  # VPN tunnels
    r"^tap\d*$",  # VPN taps
    r"^wg\d*$",  # WireGuard
    r"^tailscale\d*$",  # Tailscale
    r"^nordlynx$",  # NordVPN
    r"^cni\d*$",  # Kubernetes CNI
    r"^flannel",  # Flannel overlay
    r"^calico",  # Calico
    r"^dummy\d*$",  # Dummy interfaces
)


@dataclass(frozen=True, slots=True)
class InterfaceFilter:
    """Filter network interfaces by name pattern."""

    excluded_patterns: tuple[re.Pattern[str], ...] = ()
    included_patterns: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> InterfaceFilter:
        """Build an interface filter from AYRAN_NETWORK_INTERFACE_FILTER.

        Format: 'exclude:pattern1,pattern2' or 'include:pattern1,pattern2'
        Default (unset): no filtering (all interfaces included)
        Special value 'no-virtual': exclude common virtual/container interfaces
        """
        source = os.environ if environ is None else environ
        raw = source.get(INTERFACE_FILTER_ENV, "").strip()

        if not raw:
            return cls()

        if raw.lower() == "no-virtual":
            compiled = tuple(re.compile(p, re.IGNORECASE) for p in _VIRTUAL_PATTERNS)
            return cls(excluded_patterns=compiled)

        if raw.lower().startswith("exclude:"):
            patterns_str = raw[8:]
            compiled = _compile_patterns(patterns_str)
            return cls(excluded_patterns=compiled)

        if raw.lower().startswith("include:"):
            patterns_str = raw[8:]
            compiled = _compile_patterns(patterns_str)
            return cls(included_patterns=compiled)

        # Treat as exclude patterns by default
        compiled = _compile_patterns(raw)
        return cls(excluded_patterns=compiled)

    def should_include(self, interface_name: str) -> bool:
        """Return True if the interface should be included."""
        name = interface_name.strip()
        if not name:
            return False

        if self.included_patterns:
            return any(p.search(name) for p in self.included_patterns)

        if self.excluded_patterns:
            return not any(p.search(name) for p in self.excluded_patterns)

        return True

    def filter_interfaces(self, interfaces: Mapping[str, T]) -> dict[str, T]:
        """Filter a dictionary of interfaces by name."""
        return {name: data for name, data in interfaces.items() if self.should_include(name)}


def _compile_patterns(patterns_str: str) -> tuple[re.Pattern[str], ...]:
    """Compile comma-separated patterns."""
    result: list[re.Pattern[str]] = []
    for part in patterns_str.split(","):
        part = part.strip()
        if part:
            try:
                result.append(re.compile(part, re.IGNORECASE))
            except re.error:
                continue
    return tuple(result)
