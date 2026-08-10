from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any

import psutil
import pytest

from beer_network import process_control
from beer_network.process_control import (
    ProcessActionResult,
    ProcessController,
    resume_process,
    suspend_process,
    terminate_process,
)


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        name: str = "game.exe",
        create_time: float = 42.0,
        action_error: BaseException | None = None,
    ) -> None:
        self.pid = pid
        self._name = name
        self._create_time = create_time
        self._action_error = action_error
        self.actions: list[str] = []

    def name(self) -> str:
        return self._name

    def create_time(self) -> float:
        return self._create_time

    def terminate(self) -> None:
        self._record_action("terminate")

    def suspend(self) -> None:
        self._record_action("suspend")

    def resume(self) -> None:
        self._record_action("resume")

    def _record_action(self, action: str) -> None:
        if self._action_error is not None:
            raise self._action_error
        self.actions.append(action)


@pytest.fixture(autouse=True)
def run_worker_calls_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[Callable[..., Any], tuple[object, ...]]]:
    calls: list[tuple[Callable[..., Any], tuple[object, ...]]] = []

    async def inline_to_thread(function: Callable[..., Any], *args: object) -> Any:
        calls.append((function, args))
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline_to_thread)
    return calls


@pytest.mark.asyncio
async def test_terminate_checks_name_and_create_time_before_signaling(
    monkeypatch: pytest.MonkeyPatch,
    run_worker_calls_inline: list[tuple[Callable[..., Any], tuple[object, ...]]],
) -> None:
    fake_process = FakeProcess(40, name="Game.EXE", create_time=123.5)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_process)

    result = await terminate_process(
        40,
        expected_name="game.exe",
        expected_create_time=123.5,
    )

    assert result.success is True
    assert result.ok is True
    assert result.action == "terminate"
    assert result.observed_name == "Game.EXE"
    assert result.observed_create_time == 123.5
    assert fake_process.actions == ["terminate"]
    assert len(run_worker_calls_inline) == 1
    assert run_worker_calls_inline[0][0] is process_control._perform_process_action


@pytest.mark.asyncio
async def test_suspend_uses_psutil_without_signaling_a_real_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = FakeProcess(41)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_process)

    result = await suspend_process(41, expected_name="game.exe")

    assert result.success is True
    assert result.action == "suspend"
    assert fake_process.actions == ["suspend"]


@pytest.mark.asyncio
async def test_resume_uses_psutil_without_signaling_a_real_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = FakeProcess(41)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_process)

    result = await resume_process(41, expected_name="game.exe")

    assert result.success is True
    assert result.action == "resume"
    assert fake_process.actions == ["resume"]


@pytest.mark.asyncio
async def test_controller_facade_delegates_to_public_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = FakeProcess(45, name="controller.exe", create_time=88.0)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_process)

    result = await ProcessController().terminate(
        45,
        expected_name="controller.exe",
        expected_create_time=88.0,
    )

    assert result.success is True
    assert fake_process.actions == ["terminate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", [-10, 0, 1])
async def test_protected_pids_are_rejected_before_psutil_lookup(
    monkeypatch: pytest.MonkeyPatch,
    pid: int,
) -> None:
    def unexpected_process_lookup(_pid: int) -> FakeProcess:
        raise AssertionError("psutil.Process must not be called")

    monkeypatch.setattr(psutil, "Process", unexpected_process_lookup)

    result = await terminate_process(pid)

    assert result.success is False
    assert result.error == "UnsafePid"


@pytest.mark.asyncio
async def test_current_process_is_rejected_before_psutil_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "getpid", lambda: 99)

    def unexpected_process_lookup(_pid: int) -> FakeProcess:
        raise AssertionError("psutil.Process must not be called")

    monkeypatch.setattr(psutil, "Process", unexpected_process_lookup)

    result = await suspend_process(99)

    assert result.success is False
    assert result.error == "CurrentProcess"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_name", "expected_create_time", "error_detail"),
    [
        ("different.exe", 42.0, "name changed"),
        ("game.exe", 99.0, "creation time changed"),
    ],
)
async def test_identity_mismatch_never_signals_process(
    monkeypatch: pytest.MonkeyPatch,
    expected_name: str,
    expected_create_time: float,
    error_detail: str,
) -> None:
    fake_process = FakeProcess(50)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_process)

    result = await terminate_process(
        50,
        expected_name=expected_name,
        expected_create_time=expected_create_time,
    )

    assert result.success is False
    assert result.error == "IdentityMismatch"
    assert error_detail in result.message
    assert fake_process.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_name"),
    [
        (psutil.AccessDenied(pid=60), "AccessDenied"),
        (psutil.NoSuchProcess(pid=60), "NoSuchProcess"),
        (psutil.ZombieProcess(pid=60), "ZombieProcess"),
        (OSError("operation unavailable"), "OSError"),
    ],
)
async def test_expected_psutil_failures_are_returned_as_results(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    error_name: str,
) -> None:
    def failed_process_lookup(_pid: int) -> FakeProcess:
        raise failure

    monkeypatch.setattr(psutil, "Process", failed_process_lookup)

    result = await terminate_process(60, expected_name="game.exe")

    assert result.success is False
    assert result.error == error_name
    assert error_name in result.message


@pytest.mark.asyncio
async def test_action_os_error_is_returned_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = FakeProcess(70, action_error=OSError("not supported"))
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_process)

    result = await suspend_process(70)

    assert result.success is False
    assert result.error == "OSError"
    assert fake_process.actions == []


@pytest.mark.asyncio
async def test_non_finite_creation_time_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_process_lookup(_pid: int) -> FakeProcess:
        raise AssertionError("psutil.Process must not be called")

    monkeypatch.setattr(psutil, "Process", unexpected_process_lookup)

    result = await terminate_process(80, expected_create_time=float("nan"))

    assert result.success is False
    assert result.error == "InvalidIdentity"


def test_action_results_are_immutable() -> None:
    result = ProcessActionResult(
        action="terminate",
        pid=90,
        success=False,
        message="Refused",
        error="UnsafePid",
    )

    with pytest.raises(FrozenInstanceError):
        result.success = True  # type: ignore[misc]
