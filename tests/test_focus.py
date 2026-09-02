from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ayran_network.backend import PROCESS_ACTIVITY_BASIS, ProcessSnapshot
from ayran_network.focus import (
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
        activity_score=0.0,
        activity_basis=PROCESS_ACTIVITY_BASIS,
        create_time=100.0 + pid,
    )


@pytest.mark.parametrize(
    "name",
    [
        "CHROME.EXE",
        "firefox.bin",
        "apt",
        "Discord.exe",
        "slack",
        "wget",
        "CURL",
    ],
)
def test_default_focus_apps_recognize_browser_process_variants(name: str) -> None:
    classifier = FocusClassifier()
    assert classifier.matches(name)


def test_matching_ignores_case_punctuation_executable_suffix_and_path() -> None:
    classifier = FocusClassifier(("My.Game",))

    assert normalize_app_name(r"C:\Games\MY game.exe") == "mygame"
    assert classifier.matches(r"C:\Games\MY game.exe")
    assert not classifier.matches("my-game-helper.exe")


def test_split_is_ordered_immutable_and_exposes_active_state() -> None:
    background_task = process(10, "unknown_task")
    first_app = process(20, "chrome.exe")
    second_app = process(30, "slack")

    selection = split_processes(
        (background_task, first_app, second_app),
        classifier=FocusClassifier(),
    )

    assert selection.focused == (first_app, second_app)
    assert selection.background == (background_task,)
    assert selection.matched_names == ("chrome.exe", "slack")
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
    assert not classifier.matches("chrome.exe")


def test_environment_plus_prefix_extends_defaults_and_deduplicates() -> None:
    classifier = FocusClassifier.from_environment({FOCUS_APPS_ENV: "+ Custom.App, custom app.exe"})

    assert classifier.matches("Custom App.exe")
    assert classifier.matches("chrome.exe")
    assert classifier.app_names[-1] == "Custom.App"
    assert sum(normalize_app_name(name) == "customapp" for name in classifier.app_names) == 1


def test_unset_and_empty_environment_values_have_distinct_meanings() -> None:
    assert parse_focus_apps(None) == DEFAULT_FOCUS_APPS
    assert parse_focus_apps("") == ()
    assert FocusClassifier.from_environment({}).matches("firefox.exe")
    assert not FocusClassifier.from_environment({FOCUS_APPS_ENV: ""}).matches("firefox.exe")
