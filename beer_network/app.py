"""Textual application for the Beer-Network traffic dashboard."""

from __future__ import annotations

import math
from collections import deque
from typing import Protocol

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Sparkline, Static

from beer_network.backend import NetworkSnapshot, ProcessSnapshot, PsutilNetworkBackend

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_HISTORY_SIZE = 60


class NetworkSampler(Protocol):
    """The backend interface required by :class:`BeerNetworkApp`."""

    async def sample(self) -> NetworkSnapshot:
        """Return the next network snapshot."""

        ...


class BeerNetworkApp(App[None]):
    """A live terminal dashboard for global and per-process network activity."""

    TITLE = "Beer-Network"
    SUB_TITLE = "Live network traffic"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    CSS = """
    Screen {
        layout: vertical;
        min-width: 60;
    }

    #metrics {
        height: 7;
        padding: 0 1;
    }

    .metric-panel {
        width: 1fr;
        height: 7;
        border: round $primary;
        padding: 0 1;
    }

    .metric-panel:first-child {
        margin-right: 1;
    }

    .metric-name {
        height: 1;
        text-style: bold;
    }

    .metric-value {
        height: 1;
        color: $accent;
    }

    .metric-sparkline {
        height: 2;
    }

    .metric-total {
        height: 1;
        color: $text-muted;
    }

    #status {
        height: 3;
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

    #process-table {
        height: 1fr;
    }
    """

    def __init__(
        self,
        *,
        backend: NetworkSampler | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        history_size: int = DEFAULT_HISTORY_SIZE,
    ) -> None:
        if not math.isfinite(poll_interval) or poll_interval <= 0.0:
            raise ValueError("poll_interval must be a positive finite number")
        if history_size < 2:
            raise ValueError("history_size must be at least two")

        super().__init__()
        self.backend: NetworkSampler = backend if backend is not None else PsutilNetworkBackend()
        self.poll_interval = poll_interval
        self._upload_history: deque[float] = deque([0.0, 0.0], maxlen=history_size)
        self._download_history: deque[float] = deque([0.0, 0.0], maxlen=history_size)
        self._refresh_running = False

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
        yield DataTable(
            id="process-table",
            cursor_type="row",
            zebra_stripes=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        """Configure the process table and begin background sampling."""

        table = self.query_one("#process-table", DataTable)
        table.add_column("PID", key="pid", width=8)
        table.add_column("Name", key="name", width=24)
        table.add_column("User", key="user", width=18)
        table.add_column("Status", key="status", width=18)
        table.add_column("Connections", key="connections", width=12)
        table.add_column("Estimated Speed", key="estimated-speed", width=32)
        self.set_interval(
            self.poll_interval,
            self.request_refresh,
            name="network-poll",
        )
        self.request_refresh()

    def action_refresh_now(self) -> None:
        """Request an immediate sample when the user presses ``r``."""

        self.request_refresh()

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

        self._render_processes(snapshot.processes)
        self._render_status(snapshot)

    def _render_processes(self, processes: tuple[ProcessSnapshot, ...]) -> None:
        table = self.query_one("#process-table", DataTable)
        previous_row = table.cursor_row
        selected_pid = _selected_pid(table)
        table.clear()

        selected_row: int | None = None
        for row_index, process in enumerate(processes):
            if process.pid == selected_pid:
                selected_row = row_index
            table.add_row(
                process.pid,
                Text(_truncate(process.name, 48)),
                Text(_truncate(process.username, 36)),
                Text(_display_status(process)),
                process.connection_count,
                _format_estimated_speed(process),
                key=str(process.pid),
            )

        if table.row_count:
            target_row = selected_row
            if target_row is None:
                target_row = min(previous_row, table.row_count - 1)
            table.move_cursor(row=target_row, animate=False)

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


def _selected_pid(table: DataTable[object]) -> int | None:
    if table.row_count == 0:
        return None

    try:
        raw_pid = table.get_row_at(table.cursor_row)[0]
    except IndexError:
        return None

    if isinstance(raw_pid, int):
        return raw_pid
    if isinstance(raw_pid, str):
        try:
            return int(raw_pid)
        except ValueError:
            return None
    return None


def _display_status(process: ProcessSnapshot) -> str:
    status = _truncate(process.status, 28)
    return f"{status} (limited)" if process.limited_access else status


def _format_estimated_speed(process: ProcessSnapshot) -> str:
    upload = format_rate(process.estimated_upload_bytes_per_second)
    download = format_rate(process.estimated_download_bytes_per_second)
    return f"Up {upload} / Down {download}"


def _truncate(value: str, length: int) -> str:
    if len(value) <= length:
        return value
    return f"{value[: length - 1]}\N{HORIZONTAL ELLIPSIS}"


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


def main() -> None:
    """Run the Beer-Network terminal application."""

    BeerNetworkApp().run()


__all__ = ["BeerNetworkApp", "NetworkSampler", "format_bytes", "format_rate", "main"]
