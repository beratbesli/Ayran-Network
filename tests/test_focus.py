from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from beer_network.backend import PROCESS_RATE_ESTIMATE_BASIS, ProcessSnapshot
from beer_network.focus import (
    DEFAULT_FOCUS_APPS,
    FOCUS_APPS_ENV,
    FocusClassifier,
    normalize_app_name,
    parse_focus_apps,
    split_processes,
)


def process(pid: int, name: str) -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=pid,
        name=name,
        username="player",
        status="running",
        connections=(),
        connection_count=0,
        established_connection_count=0,
        listening_connection_count=0,
        estimated_upload_bytes_per_second=0.0,
        estimated_download_bytes_per_second=0.0,
        rate_estimate_basis=PROCESS_RATE_ESTIMATE_BASIS,
        create_time=100.0 + pid,
    )


@pytest.mark.parametrize(
    "name",
    [
        "beamng-drive.EXE",
        "BEAMNG DRIVE X64.exe",
        "BeamNG.drive.x86.exe",
        "ELDEN_RING.EXE",
        "Fortnite Client Win64 Shipping.exe",
        "FORTNITECLIENT-WIN64-SHIPPING_EAC.EXE",
        "FortniteLauncher.exe",
    ],
)
def test_default_focus_apps_recognize_game_process_variants(name: str) -> None:
    classifier = FocusClassifier()

    assert classifier.matches(name)


def test_matching_ignores_case_punctuation_executable_suffix_and_path() -> None:
    classifier = FocusClassifier(("My.Game",))

    assert normalize_app_name(r"C:\Games\MY game.exe") == "mygame"
    assert classifier.matches(r"C:\Games\MY game.exe")
    assert not classifier.matches("my-game-helper.exe")


def test_split_is_ordered_immutable_and_exposes_active_state() -> None:
    browser = process(10, "browser")
    first_game = process(20, "ELDEN-RING.exe")
    second_game = process(30, "elden ring")

    selection = split_processes(
        (browser, first_game, second_game),
        classifier=FocusClassifier(),
    )

    assert selection.focused == (first_game, second_game)
    assert selection.background == (browser,)
    assert selection.matched_names == ("ELDEN-RING.exe",)
    assert selection.active is True
    assert selection.is_active is True
    with pytest.raises(FrozenInstanceError):
        selection.focused = ()  # type: ignore[misc]


def test_empty_split_is_inactive() -> None:
    selection = FocusClassifier().split(())

    assert selection.focused == ()
    assert selection.background == ()
    assert selection.matched_names == ()
    assert selection.active is False


def test_environment_value_overrides_defaults() -> None:
    classifier = FocusClassifier.from_environment({FOCUS_APPS_ENV: "Custom.App; helper.exe"})

    assert classifier.matches("custom-app.exe")
    assert classifier.matches("HELPER")
    assert not classifier.matches("eldenring.exe")


def test_environment_plus_prefix_extends_defaults_and_deduplicates() -> None:
    classifier = FocusClassifier.from_environment({FOCUS_APPS_ENV: "+ Custom.App, custom app.exe"})

    assert classifier.matches("Custom App.exe")
    assert classifier.matches("BeamNG.drive.exe")
    assert classifier.app_names[-1] == "Custom.App"
    assert sum(normalize_app_name(name) == "customapp" for name in classifier.app_names) == 1


def test_unset_and_empty_environment_values_have_distinct_meanings() -> None:
    assert parse_focus_apps(None) == DEFAULT_FOCUS_APPS
    assert parse_focus_apps("") == ()
    assert FocusClassifier.from_environment({}).matches("Fortnite.exe")
    assert not FocusClassifier.from_environment({FOCUS_APPS_ENV: ""}).matches("Fortnite.exe")
