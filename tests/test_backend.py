from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from beer_network.backend import (
    PROCESS_RATE_ESTIMATE_BASIS,
    GlobalRates,
    PsutilNetworkBackend,
)


@pytest.fixture(autouse=True)
def run_worker_calls_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid a Python 3.14 executor-shutdown issue in the sandboxed test runner."""

    async def inline_to_thread(function: Callable[[], Any]) -> Any:
        return function()

    monkeypatch.setattr(asyncio, "to_thread", inline_to_thread)


class SequenceClock:
    def __init__(self, *values: float) -> None:
        self._values: Iterator[float] = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        name: str = "worker",
        username: str = "alice",
        status: str = psutil.STATUS_RUNNING,
        create_time: float = 42.0,
        connections: list[SimpleNamespace] | BaseException | None = None,
    ) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "username": username,
            "status": status,
            "create_time": create_time,
        }
        self._connections = connections if connections is not None else []

    def net_connections(self, *, kind: str) -> list[SimpleNamespace]:
        assert kind == "inet"
        if isinstance(self._connections, BaseException):
            raise self._connections
        return self._connections


class LegacyFakeProcess:
    def __init__(self, pid: int, process_connections: list[SimpleNamespace]) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": "legacy",
            "username": "alice",
            "status": psutil.STATUS_RUNNING,
        }
        self._connections = process_connections

    def connections(self, *, kind: str) -> list[SimpleNamespace]:
        assert kind == "inet"
        return self._connections


def connection(
    *,
    local: tuple[str, int],
    remote: tuple[str, int] | tuple[()],
    status: str,
    family: socket.AddressFamily = socket.AF_INET,
    socket_type: socket.SocketKind = socket.SOCK_STREAM,
) -> SimpleNamespace:
    return SimpleNamespace(
        laddr=local,
        raddr=remote,
        status=status,
        family=family,
        type=socket_type,
    )


def install_psutil_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    counters: list[tuple[int, int] | BaseException | None],
    processes: Callable[..., list[Any]],
) -> None:
    counter_values = iter(counters)

    def net_io_counters() -> SimpleNamespace | None:
        value = next(counter_values)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            return None
        sent, received = value
        return SimpleNamespace(bytes_sent=sent, bytes_recv=received)

    monkeypatch.setattr(psutil, "net_io_counters", net_io_counters)
    monkeypatch.setattr(psutil, "process_iter", processes)


@pytest.mark.asyncio
async def test_global_rates_first_sample_delta_and_counter_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_psutil_fakes(
        monkeypatch,
        counters=[(1_000, 2_000), (1_400, 2_600), (100, 100)],
        processes=lambda **_kwargs: [],
    )
    sampler = PsutilNetworkBackend(clock=SequenceClock(10.0, 12.0, 14.0))

    first = await sampler.sample()
    second = await sampler.sample()
    reset = await sampler.sample()

    assert first.global_rates.upload_bytes_per_second == 0.0
    assert first.global_rates.download_bytes_per_second == 0.0
    assert first.global_rates.interval_seconds == 0.0
    assert second.global_rates.upload_bytes_per_second == 200.0
    assert second.global_rates.download_bytes_per_second == 300.0
    assert second.global_rates.interval_seconds == 2.0
    assert reset.global_rates.upload_bytes_per_second == 0.0
    assert reset.global_rates.download_bytes_per_second == 0.0
    assert reset.global_rates.interval_seconds == 2.0
    assert sampler.latest_snapshot is reset


@pytest.mark.asyncio
async def test_process_connections_counts_and_connection_weighted_estimates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_process = FakeProcess(
        20,
        name="browser",
        connections=[
            connection(
                local=("127.0.0.1", 50_000),
                remote=("203.0.113.4", 443),
                status=psutil.CONN_ESTABLISHED,
            ),
            connection(
                local=("0.0.0.0", 8_080),
                remote=(),
                status=psutil.CONN_LISTEN,
            ),
        ],
    )
    second_process = FakeProcess(
        10,
        name="resolver",
        connections=[
            connection(
                local=("192.0.2.3", 40_000),
                remote=("198.51.100.53", 53),
                status=psutil.CONN_NONE,
                socket_type=socket.SOCK_DGRAM,
            )
        ],
    )
    install_psutil_fakes(
        monkeypatch,
        counters=[(1_000, 2_000), (1_300, 2_600)],
        processes=lambda **_kwargs: [first_process, second_process],
    )
    sampler = PsutilNetworkBackend(clock=SequenceClock(1.0, 2.0))

    await sampler.sample()
    snapshot = await sampler.sample()

    resolver, browser = snapshot.processes
    assert (resolver.pid, browser.pid) == (10, 20)
    assert resolver.connection_count == 1
    assert resolver.established_connection_count == 0
    assert browser.connection_count == 2
    assert browser.create_time == 42.0
    assert browser.established_connection_count == 1
    assert browser.listening_connection_count == 1
    remote_connection = next(
        connection for connection in browser.connections if connection.remote_host is not None
    )
    assert remote_connection.remote_host == "203.0.113.4"
    assert remote_connection.remote_port == 443
    assert browser.rate_estimate_basis == PROCESS_RATE_ESTIMATE_BASIS
    assert browser.estimated_upload_bytes_per_second == pytest.approx(200.0)
    assert browser.estimated_download_bytes_per_second == pytest.approx(400.0)
    assert resolver.estimated_upload_bytes_per_second == pytest.approx(100.0)
    assert resolver.estimated_download_bytes_per_second == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_process_errors_are_skipped_or_exposed_as_limited_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = FakeProcess(11, connections=psutil.AccessDenied(pid=11))
    gone = FakeProcess(12, connections=psutil.NoSuchProcess(pid=12))
    zombie = FakeProcess(13, connections=psutil.ZombieProcess(pid=13))
    install_psutil_fakes(
        monkeypatch,
        counters=[(100, 200)],
        processes=lambda **_kwargs: [denied, gone, zombie],
    )
    sampler = PsutilNetworkBackend(clock=SequenceClock(1.0))

    snapshot = await sampler.sample()

    assert snapshot.limited_access is True
    assert len(snapshot.warnings) == 1
    assert "AccessDenied" in snapshot.warnings[0]
    assert [process.pid for process in snapshot.processes] == [11]
    assert snapshot.processes[0].limited_access is True
    assert snapshot.processes[0].warning == snapshot.warnings[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "counter_failure",
    [psutil.AccessDenied(), OSError("unavailable"), None],
    ids=["access-denied", "os-error", "no-data"],
)
async def test_global_counter_failures_return_a_limited_zero_rate_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    counter_failure: BaseException | None,
) -> None:
    install_psutil_fakes(
        monkeypatch,
        counters=[counter_failure],
        processes=lambda **_kwargs: [],
    )
    sampler = PsutilNetworkBackend(clock=SequenceClock(1.0))

    snapshot = await sampler.sample()

    assert snapshot.global_rates.total_bytes_sent == 0
    assert snapshot.global_rates.total_bytes_received == 0
    assert snapshot.global_rates.upload_bytes_per_second == 0.0
    assert snapshot.global_rates.download_bytes_per_second == 0.0
    assert snapshot.limited_access is True
    assert len(snapshot.warnings) == 1


@pytest.mark.asyncio
async def test_legacy_process_connections_fallback_and_empty_process_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = LegacyFakeProcess(
        21,
        [
            connection(
                local=("192.0.2.10", 45_000),
                remote=("203.0.113.8", 443),
                status=psutil.CONN_ESTABLISHED,
            )
        ],
    )
    empty = FakeProcess(22)
    install_psutil_fakes(
        monkeypatch,
        counters=[(100, 200)],
        processes=lambda **_kwargs: [empty, legacy],
    )
    sampler = PsutilNetworkBackend(clock=SequenceClock(1.0))

    snapshot = await sampler.sample()

    assert [process.pid for process in snapshot.processes] == [21]
    assert snapshot.processes[0].connections[0].remote_host == "203.0.113.8"


@pytest.mark.asyncio
async def test_sample_offloads_sync_polling_to_a_thread_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_psutil_fakes(
        monkeypatch,
        counters=[(100, 200)],
        processes=lambda **_kwargs: [],
    )
    calls: list[Callable[[], Any]] = []

    async def fake_to_thread(function: Callable[[], Any]) -> Any:
        calls.append(function)
        return function()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    sampler = PsutilNetworkBackend(clock=SequenceClock(1.0))

    await sampler.sample()

    assert calls == [sampler._sample_sync]


def test_public_snapshot_values_are_immutable() -> None:
    rates = GlobalRates(
        total_bytes_sent=10,
        total_bytes_received=20,
        upload_bytes_per_second=1.0,
        download_bytes_per_second=2.0,
        interval_seconds=1.0,
    )

    with pytest.raises(FrozenInstanceError):
        rates.upload_bytes_per_second = 99.0  # type: ignore[misc]
