"""Snapshot export in JSON Lines and CSV formats."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Final
from uuid import uuid4

from ayran_network.backend import NetworkSnapshot, ProcessSnapshot

__all__ = ["export_json", "export_csv", "write_snapshot_exports"]

_CSV_HEADERS: Final[tuple[str, ...]] = (
    "timestamp",
    "pid",
    "name",
    "username",
    "status",
    "connections",
    "established",
    "listening",
    "activity_score",
    "global_upload_bps",
    "global_download_bps",
    "total_bytes_sent",
    "total_bytes_received",
    "create_time",
    "limited_access",
    "warning",
    "remote_endpoints",
    "connection_statuses",
    "families",
    "socket_types",
)


def export_json(snapshot: NetworkSnapshot) -> str:
    """Export a snapshot as a single JSON Lines record."""
    record = _snapshot_to_dict(snapshot)
    return json.dumps(record, ensure_ascii=False, default=str) + "\n"


def export_csv(snapshot: NetworkSnapshot) -> str:
    """Export a snapshot as CSV text with a header row."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_HEADERS)
    timestamp = datetime.fromtimestamp(snapshot.sampled_at, tz=timezone.utc).isoformat()
    for process in snapshot.processes:
        writer.writerow(
            [
                timestamp,
                process.pid,
                process.name,
                process.username,
                process.status,
                process.connection_count,
                process.established_connection_count,
                process.listening_connection_count,
                f"{process.activity_score:.1f}",
                f"{snapshot.global_rates.upload_bytes_per_second:.1f}",
                f"{snapshot.global_rates.download_bytes_per_second:.1f}",
                snapshot.global_rates.total_bytes_sent,
                snapshot.global_rates.total_bytes_received,
                process.create_time,
                process.limited_access,
                process.warning or "",
                json.dumps(
                    [
                        _endpoint(connection.remote_host, connection.remote_port)
                        for connection in process.connections
                        if connection.remote_host is not None
                    ],
                    ensure_ascii=False,
                ),
                json.dumps([connection.status for connection in process.connections]),
                json.dumps([connection.family for connection in process.connections]),
                json.dumps([connection.socket_type for connection in process.connections]),
            ]
        )
    return output.getvalue()


def write_snapshot_exports(snapshot: NetworkSnapshot, directory: str | Path) -> tuple[Path, Path]:
    """Write JSONL and CSV exports using private files and atomic replacement."""

    export_dir = Path(directory).expanduser()
    export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        export_dir.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    stem = f"snapshot_{stamp}_{uuid4().hex}"
    json_path = export_dir / f"{stem}.jsonl"
    csv_path = export_dir / f"{stem}.csv"
    _atomic_write(json_path, export_json(snapshot))
    try:
        _atomic_write(csv_path, export_csv(snapshot))
    except Exception:
        json_path.unlink(missing_ok=True)
        raise
    return json_path, csv_path


def _atomic_write(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _snapshot_to_dict(snapshot: NetworkSnapshot) -> dict[str, object]:
    """Convert a snapshot to a JSON-serializable dictionary."""
    return {
        "sampled_at": snapshot.sampled_at,
        "timestamp": datetime.fromtimestamp(snapshot.sampled_at, tz=timezone.utc).isoformat(),
        "global_rates": {
            "upload_bytes_per_second": snapshot.global_rates.upload_bytes_per_second,
            "download_bytes_per_second": snapshot.global_rates.download_bytes_per_second,
            "total_bytes_sent": snapshot.global_rates.total_bytes_sent,
            "total_bytes_received": snapshot.global_rates.total_bytes_received,
            "interval_seconds": snapshot.global_rates.interval_seconds,
        },
        "limited_access": snapshot.limited_access,
        "warnings": list(snapshot.warnings),
        "metadata": dict(snapshot.metadata),
        "processes": [_process_to_dict(process) for process in snapshot.processes],
    }


def _process_to_dict(process: ProcessSnapshot) -> dict[str, object]:
    return {
        "pid": process.pid,
        "name": process.name,
        "username": process.username,
        "status": process.status,
        "create_time": process.create_time,
        "limited_access": process.limited_access,
        "warning": process.warning,
        "connection_count": process.connection_count,
        "established_connection_count": process.established_connection_count,
        "listening_connection_count": process.listening_connection_count,
        "activity_score": process.activity_score,
        "connection_activity_basis": process.rate_estimate_basis,
        "connections": [
            {
                "local_host": c.local_host,
                "local_port": c.local_port,
                "remote_host": c.remote_host,
                "remote_port": c.remote_port,
                "status": c.status,
                "family": c.family,
                "socket_type": c.socket_type,
            }
            for c in process.connections
        ],
    }


def _endpoint(host: str | None, port: int | None) -> str:
    if host is None:
        return ""
    display_host = f"[{host}]" if ":" in host else host
    return display_host if port is None else f"{display_host}:{port}"
