"""Snapshot export in JSON Lines and CSV formats."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Final

from beer_network.backend import NetworkSnapshot, ProcessSnapshot

__all__ = ["export_json", "export_csv"]

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
)


def export_json(snapshot: NetworkSnapshot) -> str:
    """Export a snapshot as a single JSON Lines record."""
    record = _snapshot_to_dict(snapshot)
    return json.dumps(record, ensure_ascii=False, default=str)


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
            ]
        )
    return output.getvalue()


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
        "processes": [_process_to_dict(process) for process in snapshot.processes],
    }


def _process_to_dict(process: ProcessSnapshot) -> dict[str, object]:
    return {
        "pid": process.pid,
        "name": process.name,
        "username": process.username,
        "status": process.status,
        "connection_count": process.connection_count,
        "established_connection_count": process.established_connection_count,
        "listening_connection_count": process.listening_connection_count,
        "activity_score": process.activity_score,
        "rate_estimate_basis": process.rate_estimate_basis,
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
