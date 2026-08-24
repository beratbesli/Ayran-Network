"""Tests for the export functionality."""

import json

from ayran_network.backend import GlobalRates, NetworkSnapshot, ProcessSnapshot
from ayran_network.export import export_csv, export_json


def test_export_json() -> None:
    """Test exporting a snapshot to JSON."""
    snapshot = NetworkSnapshot(
        sampled_at=1620000000.0,
        global_rates=GlobalRates(
            upload_bytes_per_second=100.0,
            download_bytes_per_second=200.0,
            total_bytes_sent=1000,
            total_bytes_received=2000,
            interval_seconds=1.0,
        ),
        processes=(
            ProcessSnapshot(
                pid=123,
                name="test_process",
                username="test_user",
                status="running",
                create_time=1610000000.0,
                connections=(),
                connection_count=0,
                established_connection_count=0,
                listening_connection_count=0,
                activity_score=10.0,
                rate_estimate_basis="test",
                limited_access=False,
            ),
        ),
        limited_access=False,
        warnings=(),
    )

    result = export_json(snapshot)
    data = json.loads(result)

    assert data["sampled_at"] == 1620000000.0
    assert data["global_rates"]["upload_bytes_per_second"] == 100.0
    assert len(data["processes"]) == 1
    assert data["processes"][0]["pid"] == 123
    assert data["processes"][0]["name"] == "test_process"


def test_export_csv() -> None:
    """Test exporting a snapshot to CSV."""
    snapshot = NetworkSnapshot(
        sampled_at=1620000000.0,
        global_rates=GlobalRates(
            upload_bytes_per_second=100.0,
            download_bytes_per_second=200.0,
            total_bytes_sent=1000,
            total_bytes_received=2000,
            interval_seconds=1.0,
        ),
        processes=(
            ProcessSnapshot(
                pid=123,
                name="test_process",
                username="test_user",
                status="running",
                create_time=1610000000.0,
                connections=(),
                connection_count=0,
                established_connection_count=0,
                listening_connection_count=0,
                activity_score=10.0,
                rate_estimate_basis="test",
                limited_access=False,
            ),
        ),
        limited_access=False,
        warnings=(),
    )

    result = export_csv(snapshot)

    assert (
        "timestamp,pid,name,username,status,connections,established,listening,"
        "activity_score,global_upload_bps,"
        "global_download_bps,total_bytes_sent,total_bytes_received"
    ) in result
    assert ("123,test_process,test_user,running,0,0,0,10.0,100.0,200.0,1000,2000") in result
