"""Focus-mode classification for network-active processes."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from beer_network.backend import ProcessSnapshot

__all__ = [
    "DEFAULT_FOCUS_APPS",
    "FOCUS_APPS_ENV",
    "FocusClassifier",
    "FocusSelection",
    "normalize_app_name",
    "parse_focus_apps",
    "split_processes",
]

FOCUS_APPS_ENV: Final = "BEER_NETWORK_FOCUS_APPS"

# Each entry is a process name used by a supported game. Name normalization
# handles executable suffixes, capitalization, and punctuation differences.
DEFAULT_FOCUS_APPS: Final[tuple[str, ...]] = (
    "BeamNG.drive",
    "BeamNG.drive.x64.exe",
    "BeamNG.drive.x86.exe",
    "Elden Ring",
    "eldenring.exe",
    "Fortnite",
    "FortniteClient-Win64-Shipping.exe",
    "FortniteClient-Win64-Shipping_BE.exe",
    "FortniteClient-Win64-Shipping_EAC.exe",
    "FortniteLauncher.exe",
)

_APP_SEPARATOR = re.compile(r"[,;\n]+")
_EXECUTABLE_SUFFIXES: Final[tuple[str, ...]] = (".exe", ".com", ".bin", ".appimage")


@dataclass(frozen=True, slots=True)
class FocusSelection:
    """An immutable partition of processes according to the focus policy."""

    focused: tuple[ProcessSnapshot, ...]
    background: tuple[ProcessSnapshot, ...]
    matched_names: tuple[str, ...]

    @property
    def active(self) -> bool:
        """Return whether at least one configured focus process is present."""

        return bool(self.focused)

    @property
    def is_active(self) -> bool:
        """Return the focus state using a predicate-style name."""

        return self.active


@dataclass(frozen=True, slots=True)
class FocusClassifier:
    """Match process names against an immutable set of focus applications."""

    app_names: tuple[str, ...] = DEFAULT_FOCUS_APPS
    _normalized_names: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        app_names = _unique_app_names(self.app_names)
        object.__setattr__(self, "app_names", app_names)
        object.__setattr__(
            self,
            "_normalized_names",
            frozenset(normalize_app_name(name) for name in app_names),
        )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> FocusClassifier:
        """Build a classifier from ``BEER_NETWORK_FOCUS_APPS``.

        A comma-, semicolon-, or newline-separated value replaces the defaults.
        Prefixing the complete value with ``+`` extends the defaults instead.
        An unset variable uses the defaults, while an explicitly empty value
        disables focus matching.
        """

        source = os.environ if environ is None else environ
        raw_value = source.get(FOCUS_APPS_ENV)
        return cls(parse_focus_apps(raw_value))

    def matches(self, process_name: str) -> bool:
        """Return whether a process name belongs to a configured focus app."""

        normalized_name = normalize_app_name(process_name)
        return bool(normalized_name) and normalized_name in self._normalized_names

    def split(self, processes: Sequence[ProcessSnapshot]) -> FocusSelection:
        """Split a process sequence while preserving its original order."""

        focused: list[ProcessSnapshot] = []
        background: list[ProcessSnapshot] = []
        matched_names: list[str] = []
        seen_names: set[str] = set()

        for process in processes:
            if not self.matches(process.name):
                background.append(process)
                continue

            focused.append(process)
            normalized_name = normalize_app_name(process.name)
            if normalized_name not in seen_names:
                seen_names.add(normalized_name)
                matched_names.append(process.name)

        return FocusSelection(
            focused=tuple(focused),
            background=tuple(background),
            matched_names=tuple(matched_names),
        )


def parse_focus_apps(value: str | None) -> tuple[str, ...]:
    """Parse a focus-app environment value into immutable matcher names."""

    if value is None:
        return DEFAULT_FOCUS_APPS

    stripped_value = value.strip()
    extend_defaults = stripped_value.startswith("+")
    if extend_defaults:
        stripped_value = stripped_value[1:]

    configured_names = tuple(
        part.strip() for part in _APP_SEPARATOR.split(stripped_value) if part.strip()
    )
    base_names = DEFAULT_FOCUS_APPS if extend_defaults else ()
    return _unique_app_names((*base_names, *configured_names))


def normalize_app_name(value: str) -> str:
    """Normalize a process name for case- and punctuation-insensitive matching."""

    basename = value.strip().replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    folded_name = basename.casefold()
    for suffix in _EXECUTABLE_SUFFIXES:
        if folded_name.endswith(suffix):
            folded_name = folded_name[: -len(suffix)]
            break
    return "".join(character for character in folded_name if character.isalnum())


def split_processes(
    processes: Sequence[ProcessSnapshot],
    *,
    classifier: FocusClassifier | None = None,
) -> FocusSelection:
    """Split processes with the supplied classifier or the environment policy."""

    effective_classifier = classifier or FocusClassifier.from_environment()
    return effective_classifier.split(processes)


def _unique_app_names(names: Sequence[str]) -> tuple[str, ...]:
    unique_names: list[str] = []
    normalized_names: set[str] = set()
    for name in names:
        normalized_name = normalize_app_name(name)
        if not normalized_name or normalized_name in normalized_names:
            continue
        normalized_names.add(normalized_name)
        unique_names.append(name.strip())
    return tuple(unique_names)
