from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections import deque
from typing import TypeAlias

import pytest
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Sparkline, Static

from ayran_network.app import (
    AyranNetworkApp,
    ProcessActionConfirmScreen,
    ProcessTable,
    format_bytes,
    format_rate,
)
from ayran_network.backend import (
    PROCESS_RATE_ESTIMATE_BASIS,
    GlobalRates,
    NetworkSnapshot,
    ProcessConnection,
    ProcessSnapshot,
)
from ayran_network.config import AyranNetworkConfig
from ayran_network.focus import FocusClassifier
from ayran_network.geoip import GeoIPResult
from ayran_network.process_control import ProcessAction, ProcessActionResult

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


class FakeGeoIPResolver:
    def __init__(
        self,
        results: dict[str, GeoIPResult] | None = None,
        *,
        blocked: bool = False,
    ) -> None:
        self.results = results or {}
        self.cached: dict[str, GeoIPResult] = {}
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()

    def get_cached(self, address: str) -> GeoIPResult | None:
        return self.cached.get(address)

    async def resolve(self, address: str) -> GeoIPResult:
        self.calls.append(address)
        self.started.set()
        await self.release.wait()
        result = self.results.get(address, GeoIPResult())
        self.cached[address] = result
        return result


class FakeProcessController:
    def __init__(self, *, success: bool = True) -> None:
        self.success = success
        self.calls: list[tuple[ProcessAction, int, str | None, float | None]] = []

    async def terminate(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        return self._result("terminate", pid, expected_name, expected_create_time)

    async def suspend(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        return self._result("suspend", pid, expected_name, expected_create_time)

    async def resume(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        return self._result("resume", pid, expected_name, expected_create_time)

    def _result(
        self,
        action: ProcessAction,
        pid: int,
        expected_name: str | None,
        expected_create_time: float | None,
    ) -> ProcessActionResult:
        self.calls.append((action, pid, expected_name, expected_create_time))
        state = "completed" if self.success else "denied"
        return ProcessActionResult(
            action=action,
            pid=pid,
            success=self.success,
            message=f"Process action {state} for PID {pid}.",
            error=None if self.success else "AccessDenied",
        )


def process(
    pid: int,
    *,
    name: str = "browser",
    username: str = "alice",
    status: str = "running",
    connections: int = 2,
    upload: float = 1024.0,
    download: float = 2048.0,
    create_time: float | None = None,
    socket_connections: tuple[ProcessConnection, ...] = (),
    limited_access: bool = False,
) -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=pid,
        name=name,
        username=username,
        status=status,
        connections=socket_connections,
        connection_count=connections,
        established_connection_count=0,
        listening_connection_count=0,
        activity_score=upload,
        rate_estimate_basis=PROCESS_RATE_ESTIMATE_BASIS,
        create_time=create_time,
        limited_access=limited_access,
    )


def remote_connection(host: str, port: int = 443) -> ProcessConnection:
    return ProcessConnection(
        local_host="192.0.2.10",
        local_port=50_000,
        remote_host=host,
        remote_port=port,
        status="ESTABLISHED",
        family="AF_INET",
        socket_type="SOCK_STREAM",
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


async def finish_refresh(app: AyranNetworkApp) -> None:
    await app.workers.wait_for_complete()


def is_displayed(widget: Vertical) -> bool:
    return widget.display


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
    app = AyranNetworkApp(backend=backend, poll_interval=3600.0, history_size=4)

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

        table = app.query_one("#process-table", ProcessTable)
        assert [str(column.label) for column in table.ordered_columns] == [
            "PID",
            "Name",
            "User",
            "Status",
            "Connections",
                "Connection Activity",
            "Remote Endpoint",
        ]
        row = table.get_row_at(0)
        assert row[0] == 42
        assert str(row[1]) == "🚨 web-browser"
        assert str(row[2]) == "alex"
        assert str(row[3]) == "running"
        assert str(row[4]) == "3"
        assert str(row[5]) == "🔴 High"
        assert str(row[6]) == "—"
        assert str(app.query_one("#status", Static).render()) == (
            "Monitoring 1 network-active process."
        )


@pytest.mark.asyncio
async def test_manual_refresh_preserves_selected_pid_when_rows_reorder() -> None:
    backend = FakeBackend(
        snapshot(process(10, name="first"), process(20, name="selected")),
        snapshot(process(20, name="selected"), process(30, name="new")),
    )
    app = AyranNetworkApp(backend=backend, poll_interval=3600.0)

    async with app.run_test(size=(110, 28)) as pilot:
        await finish_refresh(app)
        table = app.query_one("#process-table", ProcessTable)
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
    app = AyranNetworkApp(backend=backend, poll_interval=3600.0)

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

        assert app.query_one("#process-table", ProcessTable).row_count == 1


@pytest.mark.asyncio
async def test_search_matches_pid_and_name_and_keeps_focus_mode() -> None:
    backend = FakeBackend(snapshot(process(12, name="browser"), process(34, name="worker")))
    app = AyranNetworkApp(
        backend=backend,
        classifier=FocusClassifier(("browser",)),
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 30)) as pilot:
        await finish_refresh(app)
        await pilot.press("f")
        search = app.query_one("#search-input", Input)
        search.value = "34"
        await pilot.pause()

        assert app.query_one("#focus-process-layout", Vertical).display
        focused = app.query_one("#focused-process-table", ProcessTable)
        background = app.query_one("#background-process-table", ProcessTable)
        assert [focused.get_row_at(index)[0] for index in range(focused.row_count)] == []
        assert [background.get_row_at(index)[0] for index in range(background.row_count)] == [34]

        search.value = "browser"
        await pilot.pause()
        assert [focused.get_row_at(index)[0] for index in range(focused.row_count)] == [12]

        await pilot.press("escape")
        await pilot.pause()
        assert search.value == ""
        assert app.query_one("#focused-process-table", ProcessTable).row_count == 1


@pytest.mark.asyncio
async def test_search_empty_result_is_visible() -> None:
    app = AyranNetworkApp(
        backend=FakeBackend(snapshot(process(12, name="browser"))),
        geoip_enabled=False,
        poll_interval=3600.0,
    )
    async with app.run_test(size=(120, 30)) as pilot:
        await finish_refresh(app)
        await pilot.press("f")
        app.query_one("#search-input", Input).value = "missing"
        await pilot.pause()
        assert app.query_one("#search-empty", Static).display
        assert app.query_one("#process-table", ProcessTable).row_count == 0


def test_config_is_injected_into_app_runtime() -> None:
    config = AyranNetworkConfig(
        poll_interval=0.25,
        history_size=8,
        focus_apps=("custom-app",),
        focus_extend_defaults=False,
        geoip_enabled=False,
        interface_filter="no-virtual",
        export_dir="~/custom-exports",
    )
    classifier = FocusClassifier(("custom-app",))
    app = AyranNetworkApp(config=config, backend=FakeBackend(), classifier=classifier)

    assert app.poll_interval == 0.25
    assert app._upload_history.maxlen == 8
    assert classifier.matches("custom-app")
    assert not classifier.matches("firefox")
    assert app.config.export_dir == "~/custom-exports"


def test_module_and_console_entrypoint_smoke() -> None:
    module = subprocess.run(
        [sys.executable, "-m", "ayran_network", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert module.returncode == 0
    assert "usage: ayranetwork" in module.stdout

    command = shutil.which("ayranetwork")
    if command is not None:
        console = subprocess.run([command, "--help"], check=False, capture_output=True, text=True)
        assert console.returncode == 0
        assert "usage: ayranetwork" in console.stdout


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
    app = AyranNetworkApp(backend=backend, poll_interval=3600.0)

    async with app.run_test(size=(110, 28)) as pilot:
        await finish_refresh(app)
        await pilot.pause()

        status = app.query_one("#status", Static)
        assert str(status.render()) == f"Limited access: {warning} (+1 more)"
        assert status.has_class("warning")
        table = app.query_one("#process-table", ProcessTable)
        assert table.row_count == 1
        assert str(table.get_row_at(0)[3]).startswith("limited")


@pytest.mark.asyncio
async def test_sampling_error_is_reported_and_next_refresh_can_recover() -> None:
    backend = FakeBackend(PermissionError("not allowed"), snapshot(process(9)))
    app = AyranNetworkApp(backend=backend, poll_interval=3600.0)

    async with app.run_test(size=(100, 26)) as pilot:
        await finish_refresh(app)
        await pilot.pause()

        status = app.query_one("#status", Static)
        assert str(status.render()) == "Sampling failed (PermissionError): not allowed"
        assert status.has_class("error")

        await pilot.press("r")
        await finish_refresh(app)
        await pilot.pause()

        assert app.query_one("#process-table", ProcessTable).row_count == 1
        assert str(status.render()) == "Monitoring 1 network-active process."
        assert not status.has_class("error")


def test_constructor_validates_polling_configuration_and_declares_keys() -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        AyranNetworkApp(poll_interval=0.0)
    with pytest.raises(ValueError, match="history_size"):
        AyranNetworkApp(history_size=1)

    keys = {binding.key for binding in AyranNetworkApp.BINDINGS if isinstance(binding, Binding)}
    assert {"q", "r", "g", "k", "s"} <= keys


@pytest.mark.asyncio
async def test_focus_mode_splits_tables_and_preserves_selection_at_80_columns() -> None:
    backend = FakeBackend(
        snapshot(process(10, name="browser"), process(20, name="chat")),
        snapshot(
            process(30, name="game.exe"),
            process(20, name="chat"),
            process(40, name="updater"),
        ),
        snapshot(process(20, name="chat"), process(50, name="browser")),
    )
    app = AyranNetworkApp(
        backend=backend,
        classifier=FocusClassifier(("game.exe",)),
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(80, 24)) as pilot:
        await finish_refresh(app)
        normal_table = app.query_one("#process-table", ProcessTable)
        normal_table.move_cursor(row=1, animate=False)
        normal_table.focus()
        await pilot.pause()

        await pilot.press("r")
        await finish_refresh(app)
        await pilot.pause()

        normal_layout = app.query_one("#normal-process-layout", Vertical)
        focus_layout = app.query_one("#focus-process-layout", Vertical)
        assert not is_displayed(normal_layout)
        assert is_displayed(focus_layout)
        assert "game.exe" in str(app.query_one("#focus-mode-banner", Static).render())

        focused_table = app.query_one("#focused-process-table", ProcessTable)
        background_table = app.query_one("#background-process-table", ProcessTable)
        assert [focused_table.get_row_at(index)[0] for index in range(focused_table.row_count)] == [
            30
        ]
        assert [
            background_table.get_row_at(index)[0] for index in range(background_table.row_count)
        ] == [20, 40]
        assert background_table.get_row_at(background_table.cursor_row)[0] == 20
        assert focused_table.region.height >= 3
        assert background_table.region.height >= 3
        assert [str(column.label) for column in focused_table.ordered_columns] == [
            "PID",
            "Name",
            "State",
            "Conn",
                "Activity",
            "Remote",
        ]

        await pilot.press("r")
        await finish_refresh(app)
        await pilot.pause()

        assert is_displayed(normal_layout)
        assert not is_displayed(focus_layout)
        assert normal_table.get_row_at(normal_table.cursor_row)[0] == 20


@pytest.mark.asyncio
async def test_geoip_defaults_on_deduplicates_hosts_and_updates_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AYRAN_NETWORK_GEOIP_ENABLED", raising=False)
    host = "8.8.8.8"
    resolver = FakeGeoIPResolver(
        {host: GeoIPResult(success=True, country_code="US", country="United States", flag="🇺🇸")}
    )
    backend = FakeBackend(
        snapshot(
            process(
                10,
                connections=1,
                socket_connections=(remote_connection(host, 443),),
            ),
            process(
                20,
                connections=1,
                socket_connections=(remote_connection(host, 53),),
            ),
        )
    )
    app = AyranNetworkApp(
        backend=backend,
        geoip_resolver=resolver,
        poll_interval=3600.0,
        geoip_enabled=True,
    )

    async with app.run_test(size=(120, 30)) as pilot:
        await finish_refresh(app)
        await pilot.pause()

        table = app.query_one("#process-table", ProcessTable)
        assert resolver.calls == [host]
        assert str(table.get_row_at(0)[6]) == "🇺🇸 8.8.8.8:443"
        assert str(table.get_row_at(1)[6]) == "🇺🇸 8.8.8.8:53"


@pytest.mark.asyncio
async def test_geoip_environment_toggle_disables_and_restores_flag_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AYRAN_NETWORK_GEOIP_ENABLED", "off")
    host = "1.1.1.1"
    resolver = FakeGeoIPResolver(
        {host: GeoIPResult(success=True, country_code="AU", country="Australia", flag="🇦🇺")}
    )
    backend = FakeBackend(
        snapshot(
            process(
                11,
                connections=1,
                socket_connections=(remote_connection(host),),
            )
        )
    )
    app = AyranNetworkApp(
        backend=backend,
        geoip_resolver=resolver,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 28)) as pilot:
        await finish_refresh(app)
        table = app.query_one("#process-table", ProcessTable)
        assert resolver.calls == []
        assert str(table.get_row_at(0)[6]) == "1.1.1.1:443"

        await pilot.press("g")
        await finish_refresh(app)
        await pilot.pause()

        assert resolver.calls == [host]
        assert str(table.get_row_at(0)[6]) == "🇦🇺 1.1.1.1:443"

        await pilot.press("g")
        await pilot.pause()
        assert str(table.get_row_at(0)[6]) == "1.1.1.1:443"

        await pilot.press("g")
        await finish_refresh(app)
        await pilot.pause()
        assert resolver.calls == [host]
        assert str(table.get_row_at(0)[6]) == "🇦🇺 1.1.1.1:443"


@pytest.mark.asyncio
async def test_geoip_refresh_does_not_start_an_overlapping_lookup() -> None:
    host = "9.9.9.9"
    resolver = FakeGeoIPResolver(
        {host: GeoIPResult(success=True, country_code="US", flag="🇺🇸")},
        blocked=True,
    )
    first = snapshot(
        process(
            12,
            connections=1,
            socket_connections=(remote_connection(host),),
        )
    )
    backend = FakeBackend(first, first)
    app = AyranNetworkApp(
        backend=backend,
        geoip_resolver=resolver,
        poll_interval=3600.0,
        geoip_enabled=True,
    )

    async with app.run_test(size=(120, 28)) as pilot:
        await asyncio.wait_for(resolver.started.wait(), timeout=1.0)
        await pilot.press("r")
        await pilot.pause()

        assert backend.calls == 2
        assert resolver.calls == [host]

        resolver.release.set()
        await finish_refresh(app)
        await pilot.pause()
        assert resolver.calls == [host]


@pytest.mark.asyncio
async def test_process_action_requires_confirmation_and_uses_snapshot_identity() -> None:
    target = process(77, name="worker", create_time=1234.5)
    backend = FakeBackend(snapshot(target), snapshot(target))
    controller = FakeProcessController(success=True)
    app = AyranNetworkApp(
        backend=backend,
        process_controller=controller,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 28)) as pilot:
        await finish_refresh(app)
        table = app.query_one("#process-table", ProcessTable)
        table.focus()

        await pilot.press("k")
        await pilot.pause()
        assert isinstance(app.screen, ProcessActionConfirmScreen)
        assert controller.calls == []

        app.action_suspend_process()
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert controller.calls == []

        await pilot.press("escape")
        await pilot.pause()
        assert controller.calls == []

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, ProcessActionConfirmScreen)
        await pilot.press("y")
        await finish_refresh(app)
        await pilot.pause()

        assert controller.calls == [("suspend", 77, "worker", 1234.5)]
        notifications = tuple(app._notifications)
        assert any(
            notification.message == "Process action completed for PID 77."
            and notification.severity == "information"
            for notification in notifications
        )


@pytest.mark.asyncio
async def test_empty_table_never_opens_confirmation_or_calls_controller() -> None:
    controller = FakeProcessController(success=False)
    app = AyranNetworkApp(
        backend=FakeBackend(snapshot()),
        process_controller=controller,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 26)) as pilot:
        await finish_refresh(app)
        await pilot.press("k")
        await pilot.press("s")
        await pilot.pause()

        assert controller.calls == []
        assert not isinstance(app.screen, ProcessActionConfirmScreen)
        notifications = tuple(app._notifications)
        assert any(
            notification.message == "Select a process before using Kill, Suspend, or Resume."
            and notification.severity == "warning"
            for notification in notifications
        )


@pytest.mark.asyncio
async def test_process_controller_failure_is_reported_as_error_notification() -> None:
    target = process(88, name="protected", create_time=987.0)
    controller = FakeProcessController(success=False)
    app = AyranNetworkApp(
        backend=FakeBackend(snapshot(target)),
        process_controller=controller,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 26)) as pilot:
        await finish_refresh(app)
        app.query_one("#process-table", ProcessTable).focus()
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("y")
        await finish_refresh(app)
        await pilot.pause()

        assert controller.calls == [("terminate", 88, "protected", 987.0)]
        notifications = tuple(app._notifications)
        assert any(
            notification.message == "Process action denied for PID 88."
            and notification.severity == "error"
            for notification in notifications
        )
