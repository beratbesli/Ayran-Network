from __future__ import annotations

import asyncio
from collections import deque
from typing import TypeAlias

import pytest
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Sparkline, Static

from beer_network.ai_analysis import AIAnalysisResult, AIAnalysisService
from beer_network.app import (
    AIAnalysisScreen,
    BeerNetworkApp,
    ProcessActionConfirmScreen,
    format_bytes,
    format_rate,
)
from beer_network.backend import (
    PROCESS_RATE_ESTIMATE_BASIS,
    GlobalRates,
    NetworkSnapshot,
    ProcessConnection,
    ProcessSnapshot,
)
from beer_network.focus import FocusClassifier
from beer_network.geoip import GeoIPResult
from beer_network.process_control import ProcessAction, ProcessActionResult

SampleResult: TypeAlias = NetworkSnapshot | Exception
AnalysisOutcome: TypeAlias = AIAnalysisResult | Exception


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


class FakeAnalyzer:
    def __init__(
        self,
        outcome: AnalysisOutcome | None = None,
        *,
        available: bool = True,
        provider: str | None = "test-provider",
        model: str | None = "test-model",
        blocked: bool = False,
    ) -> None:
        self.outcome = outcome or AIAnalysisResult(
            success=True,
            analysis="Low risk, but verify independently.",
            provider=provider,
            model=model,
        )
        self._available = available
        self._provider = provider
        self._model = model
        self.calls: list[ProcessSnapshot] = []
        self.close_calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        if not blocked:
            self.release.set()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def provider(self) -> str | None:
        return self._provider

    @property
    def model(self) -> str | None:
        return self._model

    async def analyze(self, process: ProcessSnapshot) -> AIAnalysisResult:
        self.calls.append(process)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def aclose(self) -> None:
        self.close_calls += 1


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
        estimated_upload_bytes_per_second=upload,
        estimated_download_bytes_per_second=download,
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


async def finish_refresh(app: BeerNetworkApp) -> None:
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
            "Remote Endpoint",
        ]
        row = table.get_row_at(0)
        assert row[0] == 42
        assert str(row[1]) == "web-browser"
        assert str(row[2]) == "alex"
        assert str(row[3]) == "running"
        assert row[4] == 3
        assert str(row[5]) == "Up 4.0 KiB/s / Down 8…"
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
        assert str(table.get_row_at(0)[3]).startswith("limited")


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
    assert {"q", "r", "g", "a", "k", "s"} <= keys


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
    app = BeerNetworkApp(
        backend=backend,
        classifier=FocusClassifier(("game.exe",)),
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(80, 24)) as pilot:
        await finish_refresh(app)
        normal_table = app.query_one("#process-table", DataTable)
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

        focused_table = app.query_one("#focused-process-table", DataTable)
        background_table = app.query_one("#background-process-table", DataTable)
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
            "Traffic",
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
    monkeypatch.delenv("BEER_NETWORK_GEOIP_ENABLED", raising=False)
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
    app = BeerNetworkApp(
        backend=backend,
        geoip_resolver=resolver,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 30)) as pilot:
        await finish_refresh(app)
        await pilot.pause()

        table = app.query_one("#process-table", DataTable)
        assert resolver.calls == [host]
        assert str(table.get_row_at(0)[6]) == "🇺🇸 8.8.8.8:443"
        assert str(table.get_row_at(1)[6]) == "🇺🇸 8.8.8.8:53"


@pytest.mark.asyncio
async def test_geoip_environment_toggle_disables_and_restores_flag_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BEER_NETWORK_GEOIP_ENABLED", "off")
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
    app = BeerNetworkApp(
        backend=backend,
        geoip_resolver=resolver,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 28)) as pilot:
        await finish_refresh(app)
        table = app.query_one("#process-table", DataTable)
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
    app = BeerNetworkApp(
        backend=backend,
        geoip_resolver=resolver,
        poll_interval=3600.0,
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
    app = BeerNetworkApp(
        backend=backend,
        process_controller=controller,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 28)) as pilot:
        await finish_refresh(app)
        table = app.query_one("#process-table", DataTable)
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
    app = BeerNetworkApp(
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
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot(target)),
        process_controller=controller,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 26)) as pilot:
        await finish_refresh(app)
        app.query_one("#process-table", DataTable).focus()
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


@pytest.mark.asyncio
async def test_ai_analysis_unavailable_is_inert_and_injected_analyzer_is_not_closed() -> None:
    analyzer = FakeAnalyzer(available=False, provider=None, model=None)
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot(process(31))),
        analyzer=analyzer,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 28)) as pilot:
        await finish_refresh(app)
        await pilot.press("a")
        await pilot.pause()

        assert analyzer.calls == []
        assert not isinstance(app.screen, AIAnalysisScreen)
        assert any(
            notification.message.startswith("AI analysis is unavailable.")
            and notification.severity == "warning"
            for notification in app._notifications
        )

    assert analyzer.close_calls == 0


@pytest.mark.asyncio
async def test_ai_analysis_requires_a_selected_process() -> None:
    analyzer = FakeAnalyzer()
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot()),
        analyzer=analyzer,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 26)) as pilot:
        await finish_refresh(app)
        await pilot.press("a")
        await pilot.pause()

        assert analyzer.calls == []
        assert any(
            notification.message == "Select a process before requesting AI analysis."
            and notification.severity == "warning"
            for notification in app._notifications
        )


@pytest.mark.asyncio
async def test_ai_analysis_success_uses_captured_snapshot_and_literal_bounded_text() -> None:
    target = process(
        44,
        name="\x1b[31m[bold]Game[/bold]\x1b[0m\u202e\ud800",
        create_time=456.0,
    )
    analysis = "\x1b[31m[red]Literal\x00assessment[/red]\x1b[0m\u202e\ud800\n" + ("x" * 5_000)
    analyzer = FakeAnalyzer(
        AIAnalysisResult(
            success=True,
            analysis=analysis,
            provider="\x1b[31mlocal\x1b[0m\u202e\ud800",
            model="safe\x85model\ud800",
        )
    )
    controller = FakeProcessController()
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot(target)),
        analyzer=analyzer,
        process_controller=controller,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await finish_refresh(app)
        app.query_one("#process-table", DataTable).focus()
        await pilot.press("a")
        await finish_refresh(app)
        await pilot.pause()

        assert isinstance(app.screen, AIAnalysisScreen)
        screen = app.screen
        assert screen.process is target
        assert analyzer.calls == [target]
        assert controller.calls == []
        assert str(screen.query_one("#ai-analysis-title", Static).render()) == (
            "AI Analysis Complete"
        )
        assert str(screen.query_one("#ai-analysis-process", Static).render()) == (
            "Process: [bold]Game[/bold] (PID 44)"
        )
        assert str(screen.query_one("#ai-analysis-provider", Static).render()) == (
            "Provider: local | Model: safe model"
        )
        rendered_analysis = str(screen.query_one("#ai-analysis-message", Static).render())
        assert rendered_analysis.startswith("[red]Literal assessment[/red]\n")
        assert len(rendered_analysis) == 4_000
        assert rendered_analysis.endswith("…")
        assert "\x1b" not in rendered_analysis
        assert "\u202e" not in rendered_analysis
        assert "\ud800" not in rendered_analysis
        assert "IP addresses, PID, and username are not sent." in str(
            screen.query_one("#ai-analysis-privacy", Static).render()
        )
        assert "AI analysis never triggers process actions." in str(
            screen.query_one("#ai-analysis-advisory", Static).render()
        )

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, AIAnalysisScreen)


@pytest.mark.asyncio
async def test_ai_analysis_failure_and_unexpected_errors_are_safe_literal_text() -> None:
    target = process(52, name="browser")
    provider_failure = FakeAnalyzer(
        AIAnalysisResult(
            success=False,
            error="[red]Provider unavailable[/red]",
            provider="groq",
            model="remote-model",
        )
    )
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot(target)),
        analyzer=provider_failure,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 32)) as pilot:
        await finish_refresh(app)
        await pilot.press("a")
        await finish_refresh(app)
        await pilot.pause()

        assert isinstance(app.screen, AIAnalysisScreen)
        assert str(app.screen.query_one("#ai-analysis-title", Static).render()) == (
            "AI Analysis Failed"
        )
        assert str(app.screen.query_one("#ai-analysis-message", Static).render()) == (
            "[red]Provider unavailable[/red]"
        )

    unexpected = FakeAnalyzer(RuntimeError("private provider detail"))
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot(target)),
        analyzer=unexpected,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 32)) as pilot:
        await finish_refresh(app)
        await pilot.press("a")
        await finish_refresh(app)
        await pilot.pause()

        message = str(app.screen.query_one("#ai-analysis-message", Static).render())
        assert message == "AI analysis failed unexpectedly. Please try again."
        assert "private provider detail" not in message


@pytest.mark.asyncio
async def test_ai_running_guard_does_not_block_refresh_and_keeps_original_selection() -> None:
    original = process(61, name="original", create_time=100.0)
    refreshed = process(62, name="refreshed", create_time=200.0)
    analyzer = FakeAnalyzer(blocked=True)
    backend = FakeBackend(snapshot(original), snapshot(refreshed, upload=8_192.0))
    app = BeerNetworkApp(
        backend=backend,
        analyzer=analyzer,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(120, 30)) as pilot:
        await finish_refresh(app)
        await pilot.press("a")
        await asyncio.wait_for(analyzer.started.wait(), timeout=1.0)

        await pilot.press("a")
        await pilot.press("r")
        await pilot.pause()

        assert analyzer.calls == [original]
        assert backend.calls == 2
        assert str(app.query_one("#upload-rate", Static).render()) == "8.0 KiB/s"
        assert any(
            notification.message == "An AI analysis is already in progress."
            for notification in app._notifications
        )

        analyzer.release.set()
        await finish_refresh(app)
        await pilot.pause()

        assert isinstance(app.screen, AIAnalysisScreen)
        assert app.screen.process is original


@pytest.mark.asyncio
async def test_default_analyzer_is_closed_by_app_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = FakeAnalyzer(available=False)
    monkeypatch.setattr(AIAnalysisService, "from_environment", lambda: analyzer)
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot()),
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(100, 24)):
        await finish_refresh(app)
        assert analyzer.close_calls == 0

    assert analyzer.close_calls == 1


@pytest.mark.asyncio
async def test_ai_modal_scrolls_all_content_inside_a_short_terminal() -> None:
    analyzer = FakeAnalyzer(
        AIAnalysisResult(
            success=True,
            analysis="Assessment\n" + ("detail " * 400),
            provider="local",
            model="compact-model",
        )
    )
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot(process(70, name="compact-app"))),
        analyzer=analyzer,
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(60, 24)) as pilot:
        await finish_refresh(app)
        await pilot.press("a")
        await finish_refresh(app)
        await pilot.pause()

        dialog = app.screen.query_one("#ai-analysis-dialog", VerticalScroll)
        close_button = app.screen.query_one("#close-ai-analysis", Button)
        assert dialog.region.y >= 0
        assert dialog.region.bottom <= app.screen.size.height

        close_button.scroll_visible(animate=False, immediate=True, force=True)
        await pilot.pause()
        assert close_button.region.y >= dialog.content_region.y
        assert close_button.region.bottom <= dialog.content_region.bottom

        close_button.press()
        await pilot.pause()
        assert not isinstance(app.screen, AIAnalysisScreen)


@pytest.mark.asyncio
async def test_unmount_cancels_running_ai_worker_and_ignores_its_late_result() -> None:
    analyzer = FakeAnalyzer(blocked=True)
    app = BeerNetworkApp(
        backend=FakeBackend(snapshot(process(71))),
        analyzer=analyzer,
        geoip_resolver=FakeGeoIPResolver(),
        geoip_enabled=False,
        poll_interval=3600.0,
    )

    async with app.run_test(size=(100, 26)) as pilot:
        await finish_refresh(app)
        await pilot.press("a")
        await asyncio.wait_for(analyzer.started.wait(), timeout=1.0)

        await app.on_unmount()
        await pilot.pause()

        assert analyzer.cancelled.is_set()
        assert analyzer.close_calls == 0
        assert not app._analysis_running
        assert not isinstance(app.screen, AIAnalysisScreen)
