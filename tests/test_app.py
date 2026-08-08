from __future__ import annotations

import asyncio
from collections import deque
from typing import TypeAlias

import pytest
from textual.binding import Binding
from textual.widgets import DataTable, Sparkline, Static

from beer_network.app import BeerNetworkApp, format_bytes, format_rate
from beer_network.backend import (
    PROCESS_RATE_ESTIMATE_BASIS,
    GlobalRates,
    NetworkSnapshot,
    ProcessSnapshot,
)

SampleResult: TypeAlias = NetworkSnapshot | Exception


class FakeBackend:
    def __init__(self, *results: SampleResult) -> None:
        self.results: deque[SampleResult] = deque(results)
        self.calls = 0

    async def sample(self) -> NetworkSnapshot:
        self.calls += 1
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


class BlockingBackend:
    def __init__(self, result: NetworkSnapshot) -> None:
        self.result = result
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def sample(self) -> NetworkSnapshot:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.result


def process(
    pid: int,
    *,
    name: str = "browser",
    username: str = "alice",
    status: str = "running",
    connections: int = 2,
    upload: float = 1024.0,
    download: float = 2048.0,
    limited_access: bool = False,
) -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=pid,
        name=name,
        username=username,
        status=status,
        connections=(),
        connection_count=connections,
        established_connection_count=0,
        listening_connection_count=0,
        estimated_upload_bytes_per_second=upload,
        estimated_download_bytes_per_second=download,
        rate_estimate_basis=PROCESS_RATE_ESTIMATE_BASIS,
        limited_access=limited_access,
    )


def snapshot(
    *processes: ProcessSnapshot,
    upload: float = 1536.0,
    download: float = 2 * 1024 * 1024.0,
    total_sent: int = 3 * 1024,
    total_received: int = 4 * 1024 * 1024,
    limited_access: bool = False,
    warnings: tuple[str, ...] = (),
) -> NetworkSnapshot:
    return NetworkSnapshot(
        sampled_at=1.0,
        global_rates=GlobalRates(
            total_bytes_sent=total_sent,
            total_bytes_received=total_received,
            upload_bytes_per_second=upload,
            download_bytes_per_second=download,
            interval_seconds=1.0,
        ),
        processes=processes,
        limited_access=limited_access,
        warnings=warnings,
    )


async def finish_refresh(app: BeerNetworkApp) -> None:
    await app.workers.wait_for_complete()


def test_compact_byte_and_rate_formatting() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(5 * 1024 * 1024) == "5.0 MiB"
    assert format_rate(1536.0) == "1.5 KiB/s"
    assert format_rate(float("nan")) == "0 B/s"


@pytest.mark.asyncio
async def test_snapshot_updates_metrics_sparklines_and_process_table() -> None:
    backend = FakeBackend(
        snapshot(
            process(
                42,
                name="web-browser",
                username="alex",
                connections=3,
                upload=4096.0,
                download=8192.0,
            )
        )
    )
    app = BeerNetworkApp(backend=backend, poll_interval=3600.0, history_size=4)

    async with app.run_test(size=(120, 32)) as pilot:
        await finish_refresh(app)
        await pilot.pause()

        assert str(app.query_one("#upload-rate", Static).render()) == "1.5 KiB/s"
        assert str(app.query_one("#download-rate", Static).render()) == "2.0 MiB/s"
        assert str(app.query_one("#upload-total", Static).render()) == "Total sent: 3.0 KiB"
        assert str(app.query_one("#download-total", Static).render()) == "Total received: 4.0 MiB"
        assert tuple(app.query_one("#upload-sparkline", Sparkline).data or ()) == (
            0.0,
            0.0,
            1536.0,
        )
        assert tuple(app.query_one("#download-sparkline", Sparkline).data or ()) == (
            0.0,
            0.0,
            2 * 1024 * 1024.0,
        )

        table = app.query_one("#process-table", DataTable)
        assert [str(column.label) for column in table.ordered_columns] == [
            "PID",
            "Name",
            "User",
            "Status",
            "Connections",
            "Estimated Speed",
        ]
        row = table.get_row_at(0)
        assert row[0] == 42
        assert str(row[1]) == "web-browser"
        assert str(row[2]) == "alex"
        assert str(row[3]) == "running"
        assert row[4] == 3
        assert row[5] == "Up 4.0 KiB/s / Down 8.0 KiB/s"
        assert str(app.query_one("#status", Static).render()) == (
            "Monitoring 1 network-active process."
        )


@pytest.mark.asyncio
async def test_manual_refresh_preserves_selected_pid_when_rows_reorder() -> None:
    backend = FakeBackend(
        snapshot(process(10, name="first"), process(20, name="selected")),
        snapshot(process(20, name="selected"), process(30, name="new")),
    )
    app = BeerNetworkApp(backend=backend, poll_interval=3600.0)

    async with app.run_test(size=(110, 28)) as pilot:
        await finish_refresh(app)
        table = app.query_one("#process-table", DataTable)
        table.move_cursor(row=1, animate=False)

        await pilot.press("r")
        await finish_refresh(app)
        await pilot.pause()

        assert backend.calls == 2
        assert table.cursor_row == 0
        assert table.get_row_at(table.cursor_row)[0] == 20


@pytest.mark.asyncio
async def test_slow_sampling_runs_in_background_without_overlapping_refreshes() -> None:
    backend = BlockingBackend(snapshot(process(5)))
    app = BeerNetworkApp(backend=backend, poll_interval=3600.0)

    async with app.run_test(size=(100, 26)) as pilot:
        await asyncio.wait_for(backend.started.wait(), timeout=1.0)

        await pilot.press("r")

        assert backend.calls == 1
        assert str(app.query_one("#status", Static).render()) == (
            "Waiting for the first network sample..."
        )

        backend.release.set()
        await finish_refresh(app)
        await pilot.pause()

        assert app.query_one("#process-table", DataTable).row_count == 1


@pytest.mark.asyncio
async def test_limited_access_warning_is_visible_without_hiding_rows() -> None:
    warning = "PID 77: network details unavailable (AccessDenied)."
    backend = FakeBackend(
        snapshot(
            process(77, status="unknown", connections=0, limited_access=True),
            limited_access=True,
            warnings=(warning, "Another process was inaccessible."),
        )
    )
    app = BeerNetworkApp(backend=backend, poll_interval=3600.0)

    async with app.run_test(size=(110, 28)) as pilot:
        await finish_refresh(app)
        await pilot.pause()

        status = app.query_one("#status", Static)
        assert str(status.render()) == f"Limited access: {warning} (+1 more)"
        assert status.has_class("warning")
        table = app.query_one("#process-table", DataTable)
        assert table.row_count == 1
        assert str(table.get_row_at(0)[3]) == "unknown (limited)"


@pytest.mark.asyncio
async def test_sampling_error_is_reported_and_next_refresh_can_recover() -> None:
    backend = FakeBackend(PermissionError("not allowed"), snapshot(process(9)))
    app = BeerNetworkApp(backend=backend, poll_interval=3600.0)

    async with app.run_test(size=(100, 26)) as pilot:
        await finish_refresh(app)
        await pilot.pause()

        status = app.query_one("#status", Static)
        assert str(status.render()) == "Sampling failed (PermissionError): not allowed"
        assert status.has_class("error")

        await pilot.press("r")
        await finish_refresh(app)
        await pilot.pause()

        assert app.query_one("#process-table", DataTable).row_count == 1
        assert str(status.render()) == "Monitoring 1 network-active process."
        assert not status.has_class("error")


def test_constructor_validates_polling_configuration_and_declares_keys() -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        BeerNetworkApp(poll_interval=0.0)
    with pytest.raises(ValueError, match="history_size"):
        BeerNetworkApp(history_size=1)

    keys = {binding.key for binding in BeerNetworkApp.BINDINGS if isinstance(binding, Binding)}
    assert {"q", "r"} <= keys
