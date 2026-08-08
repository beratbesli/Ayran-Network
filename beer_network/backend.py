"""Asynchronous, read-only network sampling backed by :mod:`psutil`.

``psutil`` exposes system-wide byte counters but no per-process byte counters.
The per-process rates in this module are therefore estimates.  They apportion
the measured global rate using visible remote connections and their states;
callers must present them as estimates rather than measured traffic.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any, Final

import psutil

# Machine-readable label for the per-process estimation strategy.
PROCESS_RATE_ESTIMATE_BASIS: Final = "visible_connection_activity_share"


@dataclass(frozen=True, slots=True)
class GlobalRates:
    """Measured system-wide network counters and rates for one interval."""

    total_bytes_sent: int
    total_bytes_received: int
    upload_bytes_per_second: float
    download_bytes_per_second: float
    interval_seconds: float

    @property
    def upload_bps(self) -> float:
        """Return the upload rate using a compact UI-friendly name."""

        return self.upload_bytes_per_second

    @property
    def download_bps(self) -> float:
        """Return the download rate using a compact UI-friendly name."""

        return self.download_bytes_per_second


@dataclass(frozen=True, slots=True)
class ProcessConnection:
    """An immutable representation of one process-owned internet socket."""

    local_host: str | None
    local_port: int | None
    remote_host: str | None
    remote_port: int | None
    status: str
    family: str
    socket_type: str


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    """Process identity, connections, counts, and explicitly estimated rates."""

    pid: int
    name: str
    username: str
    status: str
    connections: tuple[ProcessConnection, ...]
    connection_count: int
    established_connection_count: int
    listening_connection_count: int
    estimated_upload_bytes_per_second: float
    estimated_download_bytes_per_second: float
    rate_estimate_basis: str
    create_time: float | None = None
    limited_access: bool = False
    warning: str | None = None

    @property
    def user(self) -> str:
        """Return the process owner using a concise display-oriented name."""

        return self.username

    @property
    def estimated_upload_bps(self) -> float:
        """Return the estimated upload rate using a compact name."""

        return self.estimated_upload_bytes_per_second

    @property
    def estimated_download_bps(self) -> float:
        """Return the estimated download rate using a compact name."""

        return self.estimated_download_bytes_per_second


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    """One coherent global and per-process network observation."""

    sampled_at: float
    global_rates: GlobalRates
    processes: tuple[ProcessSnapshot, ...]
    limited_access: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CounterSample:
    sampled_at: float
    bytes_sent: int
    bytes_received: int


@dataclass(frozen=True, slots=True)
class _UnratedProcess:
    snapshot: ProcessSnapshot
    activity_score: float


class PsutilNetworkBackend:
    """Collect psutil network data without blocking the asyncio event loop."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._previous_counters: _CounterSample | None = None
        self._latest_snapshot: NetworkSnapshot | None = None
        self._sample_lock = asyncio.Lock()

    @property
    def latest_snapshot(self) -> NetworkSnapshot | None:
        """Return the most recently completed immutable snapshot, if any."""

        return self._latest_snapshot

    async def sample(self) -> NetworkSnapshot:
        """Collect a snapshot in a worker thread and store it as the latest one."""

        async with self._sample_lock:
            snapshot = await asyncio.to_thread(self._sample_sync)
            self._latest_snapshot = snapshot
            return snapshot

    def _sample_sync(self) -> NetworkSnapshot:
        sampled_at = self._clock()
        global_rates, counter_warning = self._read_global_rates(sampled_at)
        unrated_processes, warnings = self._collect_processes()
        if counter_warning is not None:
            warnings.insert(0, counter_warning)
        processes = self._apply_rate_estimates(unrated_processes, global_rates)
        return NetworkSnapshot(
            sampled_at=sampled_at,
            global_rates=global_rates,
            processes=processes,
            limited_access=bool(warnings),
            warnings=tuple(warnings),
        )

    def _read_global_rates(self, sampled_at: float) -> tuple[GlobalRates, str | None]:
        try:
            counters: Any = psutil.net_io_counters()
        except (psutil.AccessDenied, OSError) as error:
            return self._unavailable_global_rates(), _format_counter_warning(error)

        if counters is None:
            return self._unavailable_global_rates(), "Network I/O counters returned no data."

        current_counters = _CounterSample(
            sampled_at=sampled_at,
            bytes_sent=int(counters.bytes_sent),
            bytes_received=int(counters.bytes_recv),
        )
        global_rates = self._calculate_global_rates(current_counters)
        self._previous_counters = current_counters
        return global_rates, None

    def _unavailable_global_rates(self) -> GlobalRates:
        previous = self._previous_counters
        return GlobalRates(
            total_bytes_sent=0 if previous is None else previous.bytes_sent,
            total_bytes_received=0 if previous is None else previous.bytes_received,
            upload_bytes_per_second=0.0,
            download_bytes_per_second=0.0,
            interval_seconds=0.0,
        )

    def _calculate_global_rates(self, current: _CounterSample) -> GlobalRates:
        previous = self._previous_counters
        interval = 0.0 if previous is None else current.sampled_at - previous.sampled_at
        upload_rate = 0.0
        download_rate = 0.0

        if previous is not None and interval > 0.0:
            sent_delta = current.bytes_sent - previous.bytes_sent
            received_delta = current.bytes_received - previous.bytes_received
            # Either negative delta indicates a restart, reset, or counter wrap.
            # The whole interval is invalid, so both rates deliberately remain zero.
            if sent_delta >= 0 and received_delta >= 0:
                upload_rate = sent_delta / interval
                download_rate = received_delta / interval

        return GlobalRates(
            total_bytes_sent=current.bytes_sent,
            total_bytes_received=current.bytes_received,
            upload_bytes_per_second=upload_rate,
            download_bytes_per_second=download_rate,
            interval_seconds=max(0.0, interval),
        )

    def _collect_processes(self) -> tuple[list[_UnratedProcess], list[str]]:
        processes: list[_UnratedProcess] = []
        warnings: list[str] = []
        try:
            process_iterator = psutil.process_iter(
                attrs=("pid", "name", "username", "status", "create_time"),
                ad_value=None,
            )
            for process in process_iterator:
                unrated = self._read_process(process, warnings)
                if unrated is not None:
                    processes.append(unrated)
        except psutil.AccessDenied as error:
            warnings.append(_format_enumeration_warning(error))
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            # A process can disappear while psutil is advancing the iterator.
            pass

        processes.sort(key=lambda process: process.snapshot.pid)
        return processes, warnings

    def _read_process(
        self,
        process: psutil.Process,
        warnings: list[str],
    ) -> _UnratedProcess | None:
        try:
            info = process.info
            raw_pid = info.get("pid")
            pid = process.pid if raw_pid is None else int(raw_pid)
            name = _text_or_unknown(info.get("name"))
            username = _text_or_unknown(info.get("username"))
            status = _text_or_unknown(info.get("status"))
            create_time = _optional_float(info.get("create_time"))
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied as error:
            pid = process.pid
            warning = _format_process_warning(pid, error)
            warnings.append(warning)
            return _limited_process(pid=pid, warning=warning)

        try:
            connection_reader = getattr(process, "net_connections", None)
            if connection_reader is None:
                # Process.net_connections was added after the oldest supported
                # psutil release; Process.connections is its compatible predecessor.
                connection_reader = process.connections
            raw_connections = connection_reader(kind="inet")
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied as error:
            warning = _format_process_warning(pid, error)
            warnings.append(warning)
            return _UnratedProcess(
                snapshot=ProcessSnapshot(
                    pid=pid,
                    name=name,
                    username=username,
                    status=status,
                    connections=(),
                    connection_count=0,
                    established_connection_count=0,
                    listening_connection_count=0,
                    estimated_upload_bytes_per_second=0.0,
                    estimated_download_bytes_per_second=0.0,
                    rate_estimate_basis=PROCESS_RATE_ESTIMATE_BASIS,
                    limited_access=True,
                    warning=warning,
                ),
                activity_score=0.0,
            )

        connections = tuple(
            sorted(
                (_convert_connection(item) for item in raw_connections),
                key=_connection_sort_key,
            )
        )
        if not connections:
            return None

        established_count = sum(
            connection.status.upper() == "ESTABLISHED" for connection in connections
        )
        listening_count = sum(connection.status.upper() == "LISTEN" for connection in connections)
        activity_score = sum(_connection_activity_score(connection) for connection in connections)
        return _UnratedProcess(
            snapshot=ProcessSnapshot(
                pid=pid,
                name=name,
                username=username,
                status=status,
                connections=connections,
                connection_count=len(connections),
                established_connection_count=established_count,
                listening_connection_count=listening_count,
                estimated_upload_bytes_per_second=0.0,
                estimated_download_bytes_per_second=0.0,
                rate_estimate_basis=PROCESS_RATE_ESTIMATE_BASIS,
                create_time=create_time,
            ),
            activity_score=activity_score,
        )

    @staticmethod
    def _apply_rate_estimates(
        unrated_processes: Iterable[_UnratedProcess],
        global_rates: GlobalRates,
    ) -> tuple[ProcessSnapshot, ...]:
        unrated = tuple(unrated_processes)
        total_score = sum(process.activity_score for process in unrated)
        if total_score <= 0.0:
            return tuple(process.snapshot for process in unrated)

        return tuple(
            replace(
                process.snapshot,
                estimated_upload_bytes_per_second=(
                    global_rates.upload_bytes_per_second * process.activity_score / total_score
                ),
                estimated_download_bytes_per_second=(
                    global_rates.download_bytes_per_second * process.activity_score / total_score
                ),
            )
            for process in unrated
        )


def _limited_process(*, pid: int, warning: str) -> _UnratedProcess:
    return _UnratedProcess(
        snapshot=ProcessSnapshot(
            pid=pid,
            name="unknown",
            username="unknown",
            status="unknown",
            connections=(),
            connection_count=0,
            established_connection_count=0,
            listening_connection_count=0,
            estimated_upload_bytes_per_second=0.0,
            estimated_download_bytes_per_second=0.0,
            rate_estimate_basis=PROCESS_RATE_ESTIMATE_BASIS,
            limited_access=True,
            warning=warning,
        ),
        activity_score=0.0,
    )


def _convert_connection(connection: Any) -> ProcessConnection:
    local_host, local_port = _split_address(connection.laddr)
    remote_host, remote_port = _split_address(connection.raddr)
    return ProcessConnection(
        local_host=local_host,
        local_port=local_port,
        remote_host=remote_host,
        remote_port=remote_port,
        status=str(connection.status),
        family=_enum_name(connection.family),
        socket_type=_socket_type_name(connection.type),
    )


def _split_address(address: Any) -> tuple[str | None, int | None]:
    if not address:
        return None, None

    host = getattr(address, "ip", None)
    port = getattr(address, "port", None)
    if host is None:
        host = address[0]
    if port is None and len(address) > 1:
        port = address[1]
    return str(host), int(port) if port is not None else None


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    return str(name) if name is not None else str(value)


def _socket_type_name(value: Any) -> str:
    if value == socket.SOCK_STREAM:
        return "SOCK_STREAM"
    if value == socket.SOCK_DGRAM:
        return "SOCK_DGRAM"
    return _enum_name(value)


def _connection_sort_key(
    connection: ProcessConnection,
) -> tuple[str, int, str, int, str, str, str]:
    return (
        connection.local_host or "",
        connection.local_port if connection.local_port is not None else -1,
        connection.remote_host or "",
        connection.remote_port if connection.remote_port is not None else -1,
        connection.status,
        connection.family,
        connection.socket_type,
    )


def _connection_activity_score(connection: ProcessConnection) -> float:
    if connection.remote_host is None:
        return 0.0

    status = connection.status.upper()
    if status == "ESTABLISHED":
        return 1.0
    if status in {"SYN_SENT", "SYN_RECV"}:
        return 0.75
    if connection.socket_type == "SOCK_DGRAM":
        return 0.5
    return 0.25


def _text_or_unknown(value: Any) -> str:
    return str(value) if value not in (None, "") else "unknown"


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _format_process_warning(pid: int, error: BaseException) -> str:
    return f"PID {pid}: network details unavailable ({type(error).__name__})."


def _format_enumeration_warning(error: BaseException) -> str:
    return f"Process enumeration was incomplete ({type(error).__name__})."


def _format_counter_warning(error: BaseException) -> str:
    return f"Network I/O counters unavailable ({type(error).__name__})."


NetworkBackend = PsutilNetworkBackend

__all__ = [
    "GlobalRates",
    "NetworkBackend",
    "NetworkSnapshot",
    "PROCESS_RATE_ESTIMATE_BASIS",
    "ProcessConnection",
    "ProcessSnapshot",
    "PsutilNetworkBackend",
]
