"""Guarded asynchronous process-control actions backed by :mod:`psutil`."""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass
from typing import Literal, TypeAlias

import psutil

__all__ = [
    "ProcessAction",
    "ProcessActionResult",
    "ProcessController",
    "resume_process",
    "suspend_process",
    "terminate_process",
]

ProcessAction: TypeAlias = Literal["terminate", "suspend", "resume"]


@dataclass(frozen=True, slots=True)
class ProcessActionResult:
    """The immutable outcome of one requested process-control action."""

    action: ProcessAction
    pid: int
    success: bool
    message: str
    error: str | None = None
    observed_name: str | None = None
    observed_create_time: float | None = None

    @property
    def ok(self) -> bool:
        """Return the outcome using a conventional result-style name."""

        return self.success


class ProcessController:
    """Small injectable facade for the process-control functions."""

    async def terminate(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        """Terminate a process after verifying any supplied identity fields."""

        return await terminate_process(
            pid,
            expected_name=expected_name,
            expected_create_time=expected_create_time,
        )

    async def suspend(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        """Suspend a process after verifying any supplied identity fields."""

        return await suspend_process(
            pid,
            expected_name=expected_name,
            expected_create_time=expected_create_time,
        )

    async def resume(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        """Resume a process after verifying any supplied identity fields."""

        return await resume_process(
            pid,
            expected_name=expected_name,
            expected_create_time=expected_create_time,
        )


async def terminate_process(
    pid: int,
    *,
    expected_name: str | None = None,
    expected_create_time: float | None = None,
) -> ProcessActionResult:
    """Terminate a process after optional identity checks in a worker thread."""

    return await _run_process_action(
        "terminate",
        pid,
        expected_name=expected_name,
        expected_create_time=expected_create_time,
    )


async def suspend_process(
    pid: int,
    *,
    expected_name: str | None = None,
    expected_create_time: float | None = None,
) -> ProcessActionResult:
    """Suspend a process after optional identity checks in a worker thread."""

    return await _run_process_action(
        "suspend",
        pid,
        expected_name=expected_name,
        expected_create_time=expected_create_time,
    )


async def resume_process(
    pid: int,
    *,
    expected_name: str | None = None,
    expected_create_time: float | None = None,
) -> ProcessActionResult:
    """Resume a process after optional identity checks in a worker thread."""

    return await _run_process_action(
        "resume",
        pid,
        expected_name=expected_name,
        expected_create_time=expected_create_time,
    )


async def _run_process_action(
    action: ProcessAction,
    pid: int,
    *,
    expected_name: str | None,
    expected_create_time: float | None,
) -> ProcessActionResult:
    return await asyncio.to_thread(
        _perform_process_action,
        action,
        pid,
        expected_name,
        expected_create_time,
    )


def _perform_process_action(
    action: ProcessAction,
    pid: int,
    expected_name: str | None,
    expected_create_time: float | None,
) -> ProcessActionResult:
    rejection = _validate_target(action, pid, expected_create_time)
    if rejection is not None:
        return rejection

    observed_name: str | None = None
    observed_create_time: float | None = None
    try:
        process = psutil.Process(pid)

        if expected_create_time is not None:
            observed_create_time = process.create_time()
            if not math.isclose(
                observed_create_time,
                expected_create_time,
                rel_tol=0.0,
                abs_tol=1e-6,
            ):
                return _identity_mismatch(
                    action,
                    pid,
                    "creation time changed",
                    observed_name=observed_name,
                    observed_create_time=observed_create_time,
                )

        if expected_name is not None:
            observed_name = process.name()
            if observed_name.strip().casefold() != expected_name.strip().casefold():
                return _identity_mismatch(
                    action,
                    pid,
                    f"name changed from {expected_name!r} to {observed_name!r}",
                    observed_name=observed_name,
                    observed_create_time=observed_create_time,
                )

        if action == "terminate":
            process.terminate()
        elif action == "suspend":
            process.suspend()
        elif action == "resume":
            process.resume()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as error:
        error_name = type(error).__name__
        detail = str(error).strip()
        suffix = f": {detail}" if detail else ""
        return ProcessActionResult(
            action=action,
            pid=pid,
            success=False,
            error=error_name,
            message=f"Could not {action} PID {pid} ({error_name}){suffix}",
            observed_name=observed_name,
            observed_create_time=observed_create_time,
        )

    return ProcessActionResult(
        action=action,
        pid=pid,
        success=True,
        message=f"Requested {action} for PID {pid}.",
        observed_name=observed_name,
        observed_create_time=observed_create_time,
    )


def _validate_target(
    action: ProcessAction,
    pid: int,
    expected_create_time: float | None,
) -> ProcessActionResult | None:
    if pid <= 1:
        return ProcessActionResult(
            action=action,
            pid=pid,
            success=False,
            error="UnsafePid",
            message=f"Refusing to {action} protected PID {pid}.",
        )
    if pid == os.getpid():
        return ProcessActionResult(
            action=action,
            pid=pid,
            success=False,
            error="CurrentProcess",
            message=f"Refusing to {action} the current process (PID {pid}).",
        )
    if expected_create_time is not None and not math.isfinite(expected_create_time):
        return ProcessActionResult(
            action=action,
            pid=pid,
            success=False,
            error="InvalidIdentity",
            message="Expected process creation time must be finite.",
        )
    return None


def _identity_mismatch(
    action: ProcessAction,
    pid: int,
    detail: str,
    *,
    observed_name: str | None,
    observed_create_time: float | None,
) -> ProcessActionResult:
    return ProcessActionResult(
        action=action,
        pid=pid,
        success=False,
        error="IdentityMismatch",
        message=f"Refusing to {action} PID {pid}: {detail}.",
        observed_name=observed_name,
        observed_create_time=observed_create_time,
    )
