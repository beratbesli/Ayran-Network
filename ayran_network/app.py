"""Textual application for the Ayran-Network traffic dashboard."""

from __future__ import annotations

import argparse
import asyncio
import math
import re
import unicodedata
from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from functools import partial
from ipaddress import ip_address
from typing import Final, Protocol

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Sparkline, Static

from ayran_network.backend import NetworkSnapshot, ProcessSnapshot, PsutilNetworkBackend
from ayran_network.config import AyranNetworkConfig, load_config
from ayran_network.export import write_snapshot_exports
from ayran_network.focus import FocusClassifier, FocusSelection
from ayran_network.geoip import GeoIPResolver, GeoIPResult
from ayran_network.interface_filter import InterfaceFilter
from ayran_network.process_control import ProcessAction, ProcessActionResult, ProcessController

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_HISTORY_SIZE = 60
MAX_GEOIP_LOOKUPS_PER_BATCH = 8

_COMPACT_LAYOUT_MAX_WIDTH: Final = 90
_ANSI_ESCAPE_PATTERN: Final = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])"
)
_BIDI_CONTROL_CHARACTERS: Final[frozenset[str]] = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)
_PROCESS_TABLE_IDS: Final[tuple[str, ...]] = (
    "process-table",
    "focused-process-table",
    "background-process-table",
)
_WIDE_COLUMNS: Final[tuple[tuple[str, str, int], ...]] = (
    ("PID", "pid", 7),
    ("Name", "name", 18),
    ("User", "user", 14),
    ("Status", "status", 10),
    ("Connections", "connections", 11),
    ("Connection Activity", "activity", 18),
    ("Remote Endpoint", "remote-endpoint", 28),
)
_COMPACT_COLUMNS: Final[tuple[tuple[str, str, int], ...]] = (
    ("PID", "pid", 7),
    ("Name", "name", 14),
    ("State", "status", 8),
    ("Conn", "connections", 4),
    ("Activity", "activity", 17),
    ("Remote", "remote-endpoint", 16),
)


class NetworkSampler(Protocol):
    """The backend interface required by :class:`AyranNetworkApp`."""

    async def sample(self) -> NetworkSnapshot:
        """Return the next network snapshot."""

        ...


class ProcessClassifier(Protocol):
    """The focus-classification interface required by the application."""

    def split(self, processes: Sequence[ProcessSnapshot]) -> FocusSelection:
        """Partition processes into focused and background groups."""

        ...


class GeoIPLookup(Protocol):
    """The Geo-IP interface used by the asynchronous table renderer."""

    def get_cached(self, address: str) -> GeoIPResult | None:
        """Return a cached result without performing I/O."""

        ...

    async def resolve(self, address: str) -> GeoIPResult:
        """Resolve an address asynchronously."""

        ...


class ProcessControl(Protocol):
    """The guarded process-control interface used by keyboard actions."""

    async def terminate(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        """Terminate a process after validating its identity."""

        ...

    async def suspend(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        """Suspend a process after validating its identity."""

        ...

    async def resume(
        self,
        pid: int,
        *,
        expected_name: str | None = None,
        expected_create_time: float | None = None,
    ) -> ProcessActionResult:
        """Resume a process after validating its identity."""

        ...


class ProcessActionConfirmScreen(ModalScreen[bool]):
    """Require an explicit confirmation before controlling a process."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", "No", show=False),
        Binding("y", "confirm", "Yes", show=False),
    ]

    CSS = """
    ProcessActionConfirmScreen {
        align: center middle;
        background: $background 70%;
    }

    #process-action-dialog {
        width: 72%;
        min-width: 38;
        max-width: 68;
        height: auto;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }

    #process-action-title {
        height: 1;
        text-style: bold;
        color: $warning;
    }

    #process-action-question {
        height: auto;
        margin: 1 0;
    }

    #process-action-buttons {
        height: 3;
        align-horizontal: right;
    }

    #process-action-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, action: ProcessAction, process: ProcessSnapshot) -> None:
        super().__init__()
        self.action = action
        self.process = process

    def compose(self) -> ComposeResult:
        """Build the guarded process-action dialog."""

        action_name = self.action.capitalize()
        with Vertical(id="process-action-dialog"):
            yield Static(f"Confirm {action_name}", id="process-action-title")
            yield Static(
                Text(
                    f"{action_name} {self.process.name} (PID {self.process.pid})? "
                    "This directly changes the operating-system process."
                ),
                id="process-action-question",
            )
            with Horizontal(id="process-action-buttons"):
                yield Button("Cancel", id="cancel-action")
                yield Button(action_name, id="confirm-action", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the choice represented by the pressed button."""

        self.dismiss(event.button.id == "confirm-action")

    def action_confirm(self) -> None:
        """Confirm the requested process action."""

        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel the requested process action."""

        self.dismiss(False)


class ProcessDetailsScreen(ModalScreen[None]):
    """Show details and all connections for a selected process."""

    BINDINGS = [Binding("escape", "close", "Close")]

    CSS = """
    ProcessDetailsScreen {
        align: center middle;
        background: $background 70%;
    }

    #process-details-dialog {
        width: 85%;
        min-width: 50;
        max-width: 100;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #process-details-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #process-details-info {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    #process-details-table {
        height: auto;
        max-height: 60%;
    }

    #process-details-buttons {
        height: 3;
        align-horizontal: right;
    }
    """

    def __init__(
        self,
        process: ProcessSnapshot,
        activity_history: tuple[float, ...],
    ) -> None:
        super().__init__()
        self.process = process
        self.activity_history = activity_history

    def compose(self) -> ComposeResult:
        """Build the details dialog."""
        with VerticalScroll(id="process-details-dialog"):
            yield Static(
                Text(f"Process Details: {self.process.name} (PID {self.process.pid})"),
                id="process-details-title",
            )

            info = (
                f"User: {self.process.username} | Status: {self.process.status}\n"
                f"Connections: {self.process.connection_count} "
                f"(Established: {self.process.established_connection_count}, "
                f"Listening: {self.process.listening_connection_count})\n"
                f"Traffic Score: {self.process.activity_score:.1f}"
            )
            yield Static(Text(info), id="process-details-info")

            yield Static("Traffic History", classes="metric-name")
            yield Sparkline(
                self.activity_history,
                min_color="#4b8bd8",
                max_color="#5eead4",
                classes="metric-sparkline",
            )

            table = ProcessTable(id="process-details-table")
            table.add_columns("Local", "Remote", "Family", "Type", "Status")
            for c in self.process.connections:
                local = _format_endpoint(c.local_host, c.local_port) if c.local_host else "—"
                remote = _format_endpoint(c.remote_host, c.remote_port) if c.remote_host else "—"
                table.add_row(local, remote, c.family, c.socket_type, c.status)

            yield table

            with Horizontal(id="process-details-buttons"):
                yield Button("Close", id="close-process-details", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close the dialog when its only button is pressed."""
        if event.button.id == "close-process-details":
            self.dismiss()

    def action_close(self) -> None:
        """Close the dialog."""
        self.dismiss()


class ProcessTable(DataTable[object]):
    def action_cursor_down(self, **kwargs: object) -> None:
        old_row = self.cursor_row
        # Wait for textual to do cursor down
        super().action_cursor_down(**kwargs)
        if (
            self.cursor_row == old_row
            and self.cursor_row >= self.row_count - 1
            and self.id == "focused-process-table"
        ):
                bg = self.app.query_one("#background-process-table", ProcessTable)
                bg.focus()
                if bg.row_count:
                    bg.move_cursor(row=0, animate=False)

    def action_cursor_up(self, **kwargs: object) -> None:
        old_row = self.cursor_row
        super().action_cursor_up(**kwargs)
        if (
            self.cursor_row == old_row
            and self.cursor_row <= 0
            and self.id == "background-process-table"
        ):
                fg = self.app.query_one("#focused-process-table", ProcessTable)
                fg.focus()
                if fg.row_count:
                    fg.move_cursor(row=fg.row_count - 1, animate=False)

class AyranNetworkApp(App[None]):
    """A live terminal dashboard for global and per-process network activity."""

    TITLE = "Ayran-Network"
    SUB_TITLE = "Live network traffic"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("g", "toggle_geoip", "Geo-IP"),
        Binding("f", "focus_search", "Search"),
        Binding("k", "terminate_process", "Kill"),
        Binding("s", "suspend_process", "Suspend"),
        Binding("u", "resume_process", "Resume"),
        Binding("d", "show_details", "Details"),
        Binding("e", "export_snapshot", "Export"),
        Binding("escape", "clear_search", "Clear Search", show=False),
    ]

    CSS = """
    Screen {
        layout: vertical;
        min-width: 60;
    }

    #metrics {
        height: 6;
        padding: 0 1;
    }

    .metric-panel {
        width: 1fr;
        height: 6;
        border: round $primary;
        padding: 0 1;
    }

    .metric-panel:first-of-type {
        margin-right: 1;
    }

    .metric-name, .metric-value, .metric-total {
        height: 1;
    }

    .metric-name, .section-title {
        text-style: bold;
    }

    .metric-value {
        color: $accent;
    }

    .metric-sparkline {
        height: 1;
    }

    .metric-total {
        color: $text-muted;
    }

    #status {
        height: 2;
        padding: 0 2;
        content-align: left middle;
        color: $text-muted;
    }

    #status.warning {
        color: #ffd866;
        background: #463b16;
    }

    #status.error {
        color: #ffb3b3;
        background: #4b1f24;
    }

    #normal-process-layout, #focus-process-layout {
        height: 1fr;
    }

    .section-title, #focus-mode-banner {
        height: 1;
        padding: 0 1;
    }

    #focus-mode-banner {
        color: $accent;
        text-style: bold;
    }

    #process-table {
        height: 1fr;
    }

    #focused-process-table {
        height: auto;
        max-height: 50%;
        min-height: 3;
    }

    #background-process-table {
        height: 1fr;
        min-height: 3;
    }

    DataTable {
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
    }
    
    #search-input {
        display: none;
        height: 3;
        margin-bottom: 1;
    }

    #search-empty {
        display: none;
        height: 2;
        padding: 0 1;
        color: $warning;
    }
    """

    def __init__(
        self,
        *,
        backend: NetworkSampler | None = None,
        poll_interval: float | None = None,
        history_size: int | None = None,
        classifier: ProcessClassifier | None = None,
        geoip_resolver: GeoIPLookup | None = None,
        process_controller: ProcessControl | None = None,
        geoip_enabled: bool | None = None,
        config: AyranNetworkConfig | None = None,
    ) -> None:
        effective_config = config if config is not None else load_config()
        effective_poll_interval = (
            effective_config.poll_interval if poll_interval is None else poll_interval
        )
        effective_history_size = (
            effective_config.history_size if history_size is None else history_size
        )
        if not math.isfinite(effective_poll_interval) or effective_poll_interval <= 0.0:
            raise ValueError("poll_interval must be a positive finite number")
        if effective_history_size < 2:
            raise ValueError("history_size must be at least two")

        super().__init__()
        self.config = effective_config
        self.backend: NetworkSampler = backend if backend is not None else PsutilNetworkBackend(
            interface_filter=InterfaceFilter.from_value(effective_config.interface_filter)
        )
        self.poll_interval = effective_poll_interval
        self.classifier: ProcessClassifier
        if classifier is None:
            focus_names = effective_config.focus_apps
            if effective_config.focus_extend_defaults:
                from ayran_network.focus import DEFAULT_FOCUS_APPS

                focus_names = (*DEFAULT_FOCUS_APPS, *focus_names)
            self.classifier = FocusClassifier(focus_names)
        else:
            self.classifier = classifier
        self.geoip_resolver: GeoIPLookup = (
            geoip_resolver
            if geoip_resolver is not None
            else GeoIPResolver(endpoint_template=effective_config.geoip_endpoint)
        )
        self.process_controller: ProcessControl = (
            process_controller if process_controller is not None else ProcessController()
        )
        self._owns_geoip_resolver = geoip_resolver is None
        self._geoip_enabled = (
            effective_config.geoip_enabled if geoip_enabled is None else geoip_enabled
        )

        self._upload_history: deque[float] = deque([0.0, 0.0], maxlen=effective_history_size)
        self._download_history: deque[float] = deque([0.0, 0.0], maxlen=effective_history_size)
        self._latest_snapshot: NetworkSnapshot | None = None
        self._refresh_running = False
        self._shutting_down = False
        self._process_action_running = False
        self._confirmation_open = False
        self._focus_mode_active = False
        self._compact_layout: bool | None = None
        self._rendering_tables = False
        self._last_active_table_id = "process-table"
        self._selected_identity: tuple[int, float | None] | None = None
        self._table_processes: dict[str, tuple[ProcessSnapshot, ...]] = {
            table_id: () for table_id in _PROCESS_TABLE_IDS
        }

        self._geoip_pending_hosts: set[str] = set()
        self._geoip_lookup_running = False
        self._geoip_generation = 0
        self._history_size = effective_history_size
        self._process_activity_history: dict[int, deque[float]] = {}
        
        self._search_query: str = ""

    def compose(self) -> ComposeResult:
        """Create the dashboard widgets."""

        yield Header(show_clock=True)
        with Horizontal(id="metrics"):
            with Vertical(classes="metric-panel"):
                yield Static("Upload", classes="metric-name")
                yield Static("0 B/s", id="upload-rate", classes="metric-value")
                yield Sparkline(
                    tuple(self._upload_history),
                    min_color="#4b8bd8",
                    max_color="#5eead4",
                    id="upload-sparkline",
                    classes="metric-sparkline",
                )
                yield Static("Total sent: 0 B", id="upload-total", classes="metric-total")
            with Vertical(classes="metric-panel"):
                yield Static("Download", classes="metric-name")
                yield Static("0 B/s", id="download-rate", classes="metric-value")
                yield Sparkline(
                    tuple(self._download_history),
                    min_color="#4b8bd8",
                    max_color="#f9a8d4",
                    id="download-sparkline",
                    classes="metric-sparkline",
                )
                yield Static("Total received: 0 B", id="download-total", classes="metric-total")
        yield Static("Waiting for the first network sample...", id="status")
        with Vertical(id="normal-process-layout"):
            yield Static("Active Process Connections", classes="section-title")
            yield ProcessTable(id="process-table", cursor_type="row", zebra_stripes=True)
        yield Input(placeholder="Search processes by name or PID (Esc clears)", id="search-input")
        yield Static("No processes match the current search.", id="search-empty")
        with Vertical(id="focus-process-layout"):
            yield Static("FOCUS MODE", id="focus-mode-banner")
            yield Static("Focused App Traffic", classes="section-title")
            yield ProcessTable(id="focused-process-table", cursor_type="row", zebra_stripes=True)
            yield Static("Background Noise", classes="section-title")
            yield ProcessTable(id="background-process-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        """Configure process tables and begin background sampling."""

        self.query_one("#focus-process-layout", Vertical).display = False
        search_input = self.query_one("#search-input", Input)
        search_input.display = False
        self.query_one("#search-empty", Static).display = False
        self.query_one("#process-table", ProcessTable).focus()

        self._configure_process_tables(self.size.width <= _COMPACT_LAYOUT_MAX_WIDTH)
        self.set_interval(
            self.poll_interval,
            self.request_refresh,
            name="network-poll",
        )
        self.request_refresh()

    async def on_unmount(self) -> None:
        """Close optional services created and owned by this application."""

        self._shutting_down = True
        self._geoip_generation += 1
        if self._owns_geoip_resolver:
            resolver = self.geoip_resolver
            if isinstance(resolver, GeoIPResolver):
                await resolver.aclose()

    def on_resize(self, event: events.Resize) -> None:
        """Use a compact table schema on narrow terminals."""

        compact = event.size.width <= _COMPACT_LAYOUT_MAX_WIDTH
        if self._compact_layout == compact:
            return
        selected = self._selected_process()
        if selected is not None:
            self._selected_identity = _process_identity(selected)
        self._configure_process_tables(compact)
        if self._latest_snapshot is not None:
            self._render_processes(self._latest_snapshot.processes)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Remember the last user-controlled table and process identity."""

        if (
            self._rendering_tables
            or not event.data_table.has_focus
            or not self._table_is_active(event.data_table)
        ):
            return
        processes = self._table_processes.get(event.data_table.id or "", ())
        if 0 <= event.cursor_row < len(processes):
            process = processes[event.cursor_row]
            self._last_active_table_id = event.data_table.id or self._last_active_table_id
            self._selected_identity = _process_identity(process)

    def action_refresh_now(self) -> None:
        """Request an immediate sample when the user presses ``r``."""

        self.request_refresh()

    def action_toggle_geoip(self) -> None:
        """Toggle background Geo-IP resolution and flag display."""

        self._geoip_enabled = not self._geoip_enabled
        self._geoip_generation += 1
        if self._geoip_enabled:
            self.notify(
                "Geo-IP enabled: public IP addresses will be sent to a third-party HTTPS service.",
                title="Privacy warning",
                severity="warning",
            )
        else:
            self.notify("Geo-IP lookups disabled.", title="Geo-IP")
        if self._latest_snapshot is not None:
            self._render_processes(self._latest_snapshot.processes)

    def action_terminate_process(self) -> None:
        """Request confirmation before terminating the selected process."""

        self._request_process_action("terminate")

    def action_suspend_process(self) -> None:
        """Request confirmation before suspending the selected process."""

        self._request_process_action("suspend")

    def action_resume_process(self) -> None:
        """Request confirmation before resuming the selected process."""

        self._request_process_action("resume")

    def action_focus_search(self) -> None:
        search_input = self.query_one("#search-input", Input)
        search_input.display = True
        search_input.focus()

    def action_clear_search(self) -> None:
        search_input = self.query_one("#search-input", Input)
        if search_input.has_focus or self._search_query:
            search_input.value = ""
            search_input.display = False
            self._search_query = ""
            self.query_one("#search-empty", Static).display = False
            if self._latest_snapshot:
                self._render_processes(self._latest_snapshot.processes)
            self.query_one(f"#{self._last_active_table_id}", ProcessTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._search_query = event.value
            if self._latest_snapshot:
                self._render_processes(self._latest_snapshot.processes)


    def action_show_details(self) -> None:
        """Show details for the selected process."""
        process_snapshot = self._selected_process()
        if process_snapshot is None:
            self.notify(
                "Select a process before requesting details.",
                title="Process Details",
                severity="warning",
            )
            return

        activity_hist = tuple(self._process_activity_history.get(process_snapshot.pid, (0.0,)))
        self.push_screen(ProcessDetailsScreen(process_snapshot, activity_hist))

    def action_export_snapshot(self) -> None:
        if self._latest_snapshot is None:
            self.notify("No snapshot available to export.", severity="warning")
            return
        self._do_export(self._latest_snapshot)

    @work(exclusive=True, group="export")
    async def _do_export(self, snapshot: NetworkSnapshot) -> None:
        try:
            from pathlib import Path

            export_dir = self.config.export_dir or str(Path.home() / ".ayran-network" / "exports")
            snapshot = replace(
                snapshot,
                metadata={
                    "config_source": self.config.source_path,
                    "interface_filter": self.config.interface_filter,
                    "geoip_enabled": self._geoip_enabled,
                },
            )
            json_path, csv_path = await asyncio.to_thread(
                write_snapshot_exports, snapshot, export_dir
            )
            self.notify(
                f"Exported {json_path.name} and {csv_path.name}.",
                title="Export",
            )
        except Exception as exc:
            self.notify(f"Export failed: {exc}", severity="error")

    def request_refresh(self) -> None:
        """Start a sample worker unless another sample is already in progress."""

        if self._refresh_running:
            return

        self._refresh_running = True
        self.run_worker(
            self._sample_and_render(),
            name="network-refresh",
            group="network",
        )

    async def _sample_and_render(self) -> None:
        try:
            snapshot = await self.backend.sample()
        except Exception as error:
            self._render_sample_error(error)
        else:
            self._latest_snapshot = snapshot
            self._render_snapshot(snapshot)
        finally:
            self._refresh_running = False

    def _render_snapshot(self, snapshot: NetworkSnapshot) -> None:
        rates = snapshot.global_rates
        self._upload_history.append(max(0.0, rates.upload_bytes_per_second))
        self._download_history.append(max(0.0, rates.download_bytes_per_second))

        self.query_one("#upload-rate", Static).update(format_rate(rates.upload_bytes_per_second))
        self.query_one("#download-rate", Static).update(
            format_rate(rates.download_bytes_per_second)
        )
        self.query_one("#upload-total", Static).update(
            f"Total sent: {format_bytes(rates.total_bytes_sent)}"
        )
        self.query_one("#download-total", Static).update(
            f"Total received: {format_bytes(rates.total_bytes_received)}"
        )
        self.query_one("#upload-sparkline", Sparkline).data = tuple(self._upload_history)
        self.query_one("#download-sparkline", Sparkline).data = tuple(self._download_history)

        current_pids = {p.pid for p in snapshot.processes}
        for p in snapshot.processes:
            if p.pid not in self._process_activity_history:
                self._process_activity_history[p.pid] = deque(
                    [0.0] * self._history_size, maxlen=self._history_size
                )
            self._process_activity_history[p.pid].append(p.activity_score)

        for pid in list(self._process_activity_history.keys()):
            if pid not in current_pids:
                del self._process_activity_history[pid]

        self._render_processes(snapshot.processes)
        self._render_status(snapshot)

    def _get_peak_traffic(self, pid: int) -> float:
        hist = self._process_activity_history.get(pid)
        recent = list(hist)[-5:] if hist else []
        return max(recent) if recent else 0.0

    def _render_processes(self, processes: tuple[ProcessSnapshot, ...]) -> None:
        processes = tuple(
            sorted(
                processes,
                key=lambda p: self._get_peak_traffic(p.pid),
                reverse=True,
            )
        )
        focus_selection = self.classifier.split(processes)
        query = self._search_query.casefold().strip()

        def matches(process: ProcessSnapshot) -> bool:
            return not query or query in process.name.casefold() or query in str(process.pid)

        visible_processes = tuple(process for process in processes if matches(process))
        visible_focused = tuple(process for process in focus_selection.focused if matches(process))
        visible_background = tuple(
            process for process in focus_selection.background if matches(process)
        )
        self.query_one("#search-empty", Static).display = bool(query) and not visible_processes
        selected = self._selected_process()
        selected_identity = (
            _process_identity(selected) if selected is not None else self._selected_identity
        )
        previous_rows = {
            table_id: self.query_one(f"#{table_id}", ProcessTable).cursor_row
            for table_id in _PROCESS_TABLE_IDS
        }
        focus_changed = focus_selection.active != self._focus_mode_active
        previously_focused = self.focused

        self._focus_mode_active = focus_selection.active
        self.query_one("#normal-process-layout", Vertical).display = not focus_selection.active
        self.query_one("#focus-process-layout", Vertical).display = focus_selection.active
        self._update_focus_banner(focus_selection)

        self._rendering_tables = True
        try:
            normal_match = self._populate_process_table(
                "process-table",
                visible_processes,
                selected_identity,
                previous_rows["process-table"],
            )
            focused_match = self._populate_process_table(
                "focused-process-table",
                visible_focused,
                selected_identity,
                previous_rows["focused-process-table"],
            )
            background_match = self._populate_process_table(
                "background-process-table",
                visible_background,
                selected_identity,
                previous_rows["background-process-table"],
            )
        finally:
            self._rendering_tables = False

        if focus_selection.active:
            if focused_match:
                self._last_active_table_id = "focused-process-table"
            elif background_match:
                self._last_active_table_id = "background-process-table"
            elif focus_selection.focused:
                self._last_active_table_id = "focused-process-table"
            else:
                self._last_active_table_id = "background-process-table"
        else:
            self._last_active_table_id = "process-table"
            if normal_match:
                self._selected_identity = selected_identity

        selected_after_render = self._selected_process()
        self._selected_identity = (
            _process_identity(selected_after_render) if selected_after_render is not None else None
        )

        if focus_changed and isinstance(previously_focused, ProcessTable):
            target = self.query_one(f"#{self._last_active_table_id}", ProcessTable)
            if target.row_count:
                target.focus()

        self._geoip_generation += 1
        self._schedule_geoip_lookups(processes, self._geoip_generation)

    def _populate_process_table(
        self,
        table_id: str,
        processes: Sequence[ProcessSnapshot],
        selected_identity: tuple[int, float | None] | None,
        previous_row: int,
    ) -> bool:
        table = self.query_one(f"#{table_id}", ProcessTable)

        scroll_x = table.scroll_x
        scroll_y = table.scroll_y

        table.clear()
        process_tuple = tuple(processes)
        self._table_processes[table_id] = process_tuple

        selected_row: int | None = None
        for row_index, process in enumerate(process_tuple):
            identity = _process_identity(process)
            if identity == selected_identity:
                selected_row = row_index
            table.add_row(
                *self._process_cells(process),
                key=_process_row_key(process),
            )

        if table.row_count:
            target_row = selected_row
            if target_row is None:
                target_row = min(max(previous_row, 0), table.row_count - 1)
            table.move_cursor(row=target_row, animate=False, scroll=False)
            self.call_later(table.scroll_to, x=scroll_x, y=scroll_y, animate=False)

        return selected_row is not None

    def _process_cells(self, process: ProcessSnapshot) -> tuple[object, ...]:
        compact = bool(self._compact_layout)

        is_high_traffic = process.activity_score >= 10.0

        display_name = f"🚨 {process.name}" if is_high_traffic else process.name
        name = Text(
            display_name,
            overflow="ellipsis",
            no_wrap=True,
            style="bold red" if is_high_traffic else "bold white",
        )

        raw_status = _display_status(process)
        status = Text(raw_status, overflow="ellipsis", no_wrap=True)
        if "running" in raw_status:
            status.stylize("bold green")
        elif "sleeping" in raw_status:
            status.stylize("cyan")
        elif "suspended" in raw_status or "stopped" in raw_status:
            status.stylize("bold yellow")
        elif "zombie" in raw_status or "dead" in raw_status:
            status.stylize("bold red")
        else:
            status.stylize("dim white")


        if process.activity_score >= 10.0:
            speed = Text("🔴 High", style="bold red", overflow="ellipsis", no_wrap=True)
        elif process.activity_score >= 3.0:
            speed = Text("🟡 Medium", style="bold yellow", overflow="ellipsis", no_wrap=True)
        elif process.activity_score > 0:
            speed = Text("🟢 Low", style="bold green", overflow="ellipsis", no_wrap=True)
        else:
            speed = Text("⚪ Idle", style="dim white", overflow="ellipsis", no_wrap=True)

        remote_text = self._format_remote_endpoint(process)
        remote = Text(remote_text, overflow="ellipsis", no_wrap=True)
        if "—" not in remote_text:
            remote.stylize("bright_blue")
        else:
            remote.stylize("dim")

        conn_count = Text(str(process.connection_count), style="bold cyan")

        if compact:
            return (
                process.pid,
                name,
                status,
                conn_count,
                speed,
                remote,
            )

        user = Text(process.username, overflow="ellipsis", no_wrap=True, style="dim")

        return (
            process.pid,
            name,
            user,
            status,
            conn_count,
            speed,
            remote,
        )

    def _configure_process_tables(self, compact: bool) -> None:
        if self._compact_layout == compact:
            return
        self._compact_layout = compact
        columns = _COMPACT_COLUMNS if compact else _WIDE_COLUMNS
        self._rendering_tables = True
        try:
            for table_id in _PROCESS_TABLE_IDS:
                table = self.query_one(f"#{table_id}", ProcessTable)
                table.clear(columns=True)
                for label, key, width in columns:
                    table.add_column(label, key=key, width=width)
                self._table_processes[table_id] = ()
        finally:
            self._rendering_tables = False

    def _update_focus_banner(self, selection: FocusSelection) -> None:
        banner = self.query_one("#focus-mode-banner", Static)
        if not selection.active:
            banner.update("FOCUS MODE")
            return
        names = ", ".join(selection.matched_names)
        banner.update(_ellipsized_text(f"FOCUS MODE · {names}", 72))

    def _table_is_active(self, table: DataTable[object]) -> bool:
        table_id = table.id
        if table_id == "process-table":
            return not self._focus_mode_active
        return self._focus_mode_active and table_id in {
            "focused-process-table",
            "background-process-table",
        }

    def _selected_process(self) -> ProcessSnapshot | None:
        focused = self.focused
        if isinstance(focused, ProcessTable) and self._table_is_active(focused):
            selected = self._process_at_cursor(focused)
            if selected is not None:
                return selected

        table_id = self._last_active_table_id
        if self._focus_mode_active and table_id == "process-table":
            table_id = "focused-process-table"
        elif not self._focus_mode_active:
            table_id = "process-table"
        table = self.query_one(f"#{table_id}", ProcessTable)
        return self._process_at_cursor(table)

    def _process_at_cursor(self, table: ProcessTable) -> ProcessSnapshot | None:
        processes = self._table_processes.get(table.id or "", ())
        if not table.row_count or not (0 <= table.cursor_row < len(processes)):
            return None
        return processes[table.cursor_row]

    def _request_process_action(self, action: ProcessAction) -> None:
        if self._process_action_running or self._confirmation_open:
            self.notify(
                "A process action or confirmation is already in progress.",
                title="Process Control",
                severity="warning",
            )
            return
        process = self._selected_process()
        if process is None:
            self.notify(
                "Select a process before using Kill, Suspend, or Resume.",
                title="Process Control",
                severity="warning",
            )
            return

        callback = partial(self._handle_process_action_confirmation, action, process)
        self._confirmation_open = True
        self.push_screen(ProcessActionConfirmScreen(action, process), callback)

    def _handle_process_action_confirmation(
        self,
        action: ProcessAction,
        process: ProcessSnapshot,
        confirmed: bool,
    ) -> None:
        self._confirmation_open = False
        if not confirmed or self._process_action_running:
            return
        self._process_action_running = True
        self.run_worker(
            self._perform_process_action(action, process),
            name=f"process-{action}-{process.pid}",
            group="process-control",
            exit_on_error=False,
        )

    async def _perform_process_action(
        self,
        action: ProcessAction,
        process: ProcessSnapshot,
    ) -> None:
        try:
            if action == "terminate":
                result = await self.process_controller.terminate(
                    process.pid,
                    expected_name=process.name,
                    expected_create_time=process.create_time,
                )
            elif action == "suspend":
                result = await self.process_controller.suspend(
                    process.pid,
                    expected_name=process.name,
                    expected_create_time=process.create_time,
                )
            elif action == "resume":
                result = await self.process_controller.resume(
                    process.pid,
                    expected_name=process.name,
                    expected_create_time=process.create_time,
                )
        except Exception as error:
            detail = str(error).strip()
            suffix = f": {detail}" if detail else ""
            self.notify(
                f"Process action failed ({type(error).__name__}){suffix}",
                title="Process Control",
                severity="error",
            )
        else:
            self.notify(
                result.message,
                title="Process Control",
                severity=(
                    "warning"
                    if result.success and result.process_exited is False
                    else "information" if result.success else "error"
                ),
            )
            if result.success:
                self.request_refresh()
        finally:
            self._process_action_running = False

    def _format_remote_endpoint(self, process: ProcessSnapshot) -> str:
        endpoints: list[tuple[str, int | None]] = []
        seen: set[tuple[str, int | None]] = set()
        for connection in process.connections:
            host = connection.remote_host
            if host is None:
                continue
            endpoint = (host, connection.remote_port)
            if endpoint in seen:
                continue
            seen.add(endpoint)
            endpoints.append(endpoint)

        if not endpoints:
            return "—"

        host, port = endpoints[0]
        endpoint_text = _format_endpoint(host, port)
        if self._geoip_enabled:
            geoip_result = self._cached_geoip_result(host)
            if geoip_result is not None and geoip_result.success and geoip_result.flag:
                endpoint_text = f"{geoip_result.flag} {endpoint_text}"
        if len(endpoints) > 1:
            endpoint_text = f"{endpoint_text} +{len(endpoints) - 1}"
        return endpoint_text

    def _cached_geoip_result(self, host: str) -> GeoIPResult | None:
        try:
            return self.geoip_resolver.get_cached(host)
        except Exception:
            return None

    def _schedule_geoip_lookups(
        self,
        processes: Sequence[ProcessSnapshot],
        generation: int,
    ) -> None:
        if self._shutting_down or not self._geoip_enabled or self._geoip_lookup_running:
            return

        unresolved_hosts: list[str] = []
        seen_hosts: set[str] = set()
        for process in processes:
            for connection in process.connections:
                host = connection.remote_host
                if (
                    host is None
                    or host in seen_hosts
                    or host in self._geoip_pending_hosts
                    or not _is_public_ip(host)
                ):
                    continue
                seen_hosts.add(host)
                if self._cached_geoip_result(host) is None:
                    unresolved_hosts.append(host)
                if len(unresolved_hosts) >= MAX_GEOIP_LOOKUPS_PER_BATCH:
                    break
            if len(unresolved_hosts) >= MAX_GEOIP_LOOKUPS_PER_BATCH:
                break

        if not unresolved_hosts:
            return
        hosts = tuple(unresolved_hosts)
        self._geoip_pending_hosts.update(hosts)
        self._geoip_lookup_running = True
        self.run_worker(
            self._resolve_geoip_batch(hosts, generation),
            name=f"geoip-{generation}",
            group="geoip",
            exit_on_error=False,
        )

    async def _resolve_geoip_batch(self, hosts: tuple[str, ...], generation: int) -> None:
        async def resolve_one(host: str) -> None:
            try:
                await self.geoip_resolver.resolve(host)
            except Exception:
                return

        try:
            await asyncio.gather(*(resolve_one(host) for host in hosts))
        finally:
            self._geoip_pending_hosts.difference_update(hosts)
            self._geoip_lookup_running = False

        if generation != self._geoip_generation or not self._geoip_enabled:
            if (
                not self._shutting_down
                and self._geoip_enabled
                and self._latest_snapshot is not None
            ):
                self._schedule_geoip_lookups(
                    self._latest_snapshot.processes,
                    self._geoip_generation,
                )
            return

        if self._latest_snapshot is not None:
            self._render_processes(self._latest_snapshot.processes)

    def _render_status(self, snapshot: NetworkSnapshot) -> None:
        status = self.query_one("#status", Static)
        status.remove_class("warning", "error")

        if snapshot.limited_access:
            status.add_class("warning")
            if snapshot.warnings:
                extra_count = len(snapshot.warnings) - 1
                suffix = f" (+{extra_count} more)" if extra_count else ""
                status.update(Text(f"Limited access: {snapshot.warnings[0]}{suffix}"))
            else:
                status.update("Limited access: some network details are unavailable.")
            return

        process_count = len(snapshot.processes)
        noun = "process" if process_count == 1 else "processes"
        status.update(f"Monitoring {process_count} network-active {noun}.")

    def _render_sample_error(self, error: Exception) -> None:
        status = self.query_one("#status", Static)
        status.remove_class("warning")
        status.add_class("error")
        detail = str(error).strip()
        suffix = f": {detail}" if detail else ""
        status.update(Text(f"Sampling failed ({type(error).__name__}){suffix}"))


def _process_identity(process: ProcessSnapshot) -> tuple[int, float | None]:
    return process.pid, process.create_time


def _process_row_key(process: ProcessSnapshot) -> str:
    create_time = "unknown" if process.create_time is None else f"{process.create_time:.6f}"
    return f"{process.pid}:{create_time}"


def _display_status(process: ProcessSnapshot) -> str:
    status = _truncate(process.status, 28)
    return f"limited · {status}" if process.limited_access else status


def _format_endpoint(host: str, port: int | None) -> str:
    display_host = f"[{host}]" if ":" in host else host
    return display_host if port is None else f"{display_host}:{port}"


def _is_public_ip(host: str) -> bool:
    try:
        address = ip_address(host.strip())
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_reserved
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
    )


def _ellipsized_text(value: str, length: int) -> Text:
    return Text(_truncate(value, length), overflow="ellipsis", no_wrap=True)


def _truncate(value: str, length: int) -> str:
    if len(value) <= length:
        return value
    return f"{value[: length - 1]}\N{HORIZONTAL ELLIPSIS}"


def _bounded_terminal_text(
    value: str,
    length: int,
    *,
    preserve_newlines: bool = False,
) -> str:
    """Remove terminal controls and bound untrusted text for literal display."""

    value = value[: max(length * 4, length)]
    if preserve_newlines:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    without_ansi = _ANSI_ESCAPE_PATTERN.sub("", value)
    cleaned: list[str] = []
    for character in without_ansi:
        if character in _BIDI_CONTROL_CHARACTERS:
            continue
        if preserve_newlines and character == "\n":
            cleaned.append(character)
            continue
        codepoint = ord(character)
        if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
            cleaned.append(" ")
            continue
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            continue
        cleaned.append(character)
    return _truncate("".join(cleaned), length)


def format_rate(bytes_per_second: float) -> str:
    """Format a byte rate for compact display."""

    return f"{_format_quantity(bytes_per_second)}/s"


def format_bytes(byte_count: int) -> str:
    """Format an accumulated byte count for compact display."""

    return _format_quantity(float(byte_count))


def _format_quantity(value: float) -> str:
    if not math.isfinite(value) or value <= 0.0:
        return "0 B"

    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1

    if unit_index == 0:
        return f"{value:.0f} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def main(argv: Sequence[str] | None = None) -> None:
    """Run the terminal application with CLI-over-environment precedence."""

    parser = argparse.ArgumentParser(prog="ayranetwork")
    parser.add_argument("--config", help="Path to a TOML configuration file")
    parser.add_argument("--poll-interval", type=float)
    parser.add_argument("--history-size", type=int)
    geoip = parser.add_mutually_exclusive_group()
    geoip.add_argument("--geoip", dest="geoip_enabled", action="store_true")
    geoip.add_argument("--no-geoip", dest="geoip_enabled", action="store_false")
    parser.set_defaults(geoip_enabled=None)
    parser.add_argument("--interface-filter")
    parser.add_argument("--export-dir")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.interface_filter is not None or args.export_dir is not None:
        config = replace(
            config,
            interface_filter=(
                args.interface_filter
                if args.interface_filter is not None
                else config.interface_filter
            ),
            export_dir=args.export_dir if args.export_dir is not None else config.export_dir,
        )
    AyranNetworkApp(
        config=config,
        poll_interval=args.poll_interval,
        history_size=args.history_size,
        geoip_enabled=args.geoip_enabled,
    ).run()


__all__ = [
    "AyranNetworkApp",
    "GeoIPLookup",
    "NetworkSampler",
    "ProcessActionConfirmScreen",
    "ProcessClassifier",
    "ProcessControl",
    "format_bytes",
    "format_rate",
    "main",
]
