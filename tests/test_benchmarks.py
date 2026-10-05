"""Performance benchmarking suite for Milestone 10 with deterministic median reporting."""

import io
import os
import sys
import time
import json
import platform
import statistics
import subprocess
from pathlib import Path
from typing import Dict, Any, List

import pytest

from backend.project_model.scanner import ProjectScanner
from backend.project_model.graph_builder import ProjectGraphBuilder
from backend.project_model.db import Database
from backend.context_engine.engine import ContextEngine
from backend.domain.models import ContextRequest, ContextPurpose
from backend.project_model.context_detector import ContextDetector
from tests.fixtures.synthetic_repos import create_large_benchmark_repo


def get_system_metadata() -> Dict[str, str]:
    """Collects host environment metadata for benchmark audit reporting."""
    cpu_info = platform.processor() or platform.machine()
    return {
        "os": platform.platform(),
        "python_version": sys.version.split()[0],
        "cpu": cpu_info,
    }


def format_benchmark_report(
    benchmark_name: str,
    file_count: int,
    warmup_count: int,
    measured_count: int,
    measured_durations_ms: List[float],
    sla_target_ms: float,
    passed: bool,
) -> str:
    """Formats standard benchmark execution audit string."""
    meta = get_system_metadata()
    median_ms = statistics.median(measured_durations_ms)
    min_ms = min(measured_durations_ms)
    max_ms = max(measured_durations_ms)
    return (
        f"\n=== BENCHMARK REPORT: {benchmark_name} ===\n"
        f"OS:               {meta['os']}\n"
        f"Python Version:   {meta['python_version']}\n"
        f"CPU Info:         {meta['cpu']}\n"
        f"Fixture Files:    {file_count}\n"
        f"Warm-up Runs:     {warmup_count}\n"
        f"Measured Runs:    {measured_count}\n"
        f"Durations (ms):   {[round(d, 2) for d in measured_durations_ms]}\n"
        f"Min / Median / Max: {min_ms:.2f}ms / {median_ms:.2f}ms / {max_ms:.2f}ms\n"
        f"SLA Target (ms):  {sla_target_ms:.2f}ms\n"
        f"Result:           {'PASS' if passed else 'FAIL'}\n"
        f"=========================================="
    )


@pytest.fixture(scope="module")
def large_repo(tmp_path_factory) -> Path:
    """Creates a 500+ file repository fixture (~600 files) shared across the module benchmarks."""
    repo_dir = tmp_path_factory.mktemp("benchmark_repo")
    return create_large_benchmark_repo(repo_dir, file_count=600)


@pytest.fixture(scope="module")
def standard_repo(tmp_path_factory) -> Path:
    """Creates a standard 50-file repository fixture for CLI overhead benchmarking."""
    repo_dir = tmp_path_factory.mktemp("benchmark_cli_repo")
    return create_large_benchmark_repo(repo_dir, file_count=50)


def test_benchmark_in_process_scan_large_repository(large_repo: Path):
    """1. In-process local scan & file indexing benchmark on 500+ files (SLA: < 1000ms)."""
    db_path = large_repo / ".buildcoach" / "bench_scan.db"
    if db_path.exists():
        db_path.unlink()

    scanner = ProjectScanner(project_root=large_repo, db_path=db_path)

    # 3 warm-up runs
    for _ in range(3):
        scanner.scan()

    # 5 measured runs
    durations_ms: List[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        scan_res = scanner.scan()
        t1 = time.perf_counter()
        durations_ms.append((t1 - t0) * 1000.0)

    median_duration = statistics.median(durations_ms)
    sla_target = 1000.0  # 1.0 second
    passed = median_duration < sla_target

    report = format_benchmark_report(
        benchmark_name="in_process_scan_large_repository",
        file_count=len(scan_res.files),
        warmup_count=3,
        measured_count=5,
        measured_durations_ms=durations_ms,
        sla_target_ms=sla_target,
        passed=passed,
    )
    print(report)
    assert scan_res is not None
    assert len(scan_res.files) >= 500
    assert len(durations_ms) == 5
    assert passed, f"in_process_scan_large_repository SLA violated: median {median_duration:.2f}ms exceeds target {sla_target:.2f}ms"


def test_benchmark_in_process_graph_construction(large_repo: Path):
    """2. In-process project graph construction benchmark on 500+ files (SLA: < 500ms)."""
    db_path = large_repo / ".buildcoach" / "bench_graph.db"
    db = Database(db_path)
    scanner = ProjectScanner(project_root=large_repo, db_path=db_path)
    scan_res = scanner.scan()

    builder = ProjectGraphBuilder(
        project_root=large_repo,
        project=scan_res.project,
        files=scan_res.files,
        db=db,
    )

    # 1 warm-up run
    builder.build()

    # 5 measured runs
    durations_ms: List[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        graph = builder.build()
        t1 = time.perf_counter()
        durations_ms.append((t1 - t0) * 1000.0)

    median_duration = statistics.median(durations_ms)
    sla_target = 500.0  # 500 milliseconds
    passed = median_duration < sla_target

    report = format_benchmark_report(
        benchmark_name="in_process_graph_construction",
        file_count=len(scan_res.files),
        warmup_count=1,
        measured_count=5,
        measured_durations_ms=durations_ms,
        sla_target_ms=sla_target,
        passed=passed,
    )
    print(report)
    assert graph is not None
    assert len(graph.nodes) >= 500
    assert len(durations_ms) == 5
    assert passed, f"in_process_graph_construction SLA violated: median {median_duration:.2f}ms exceeds target {sla_target:.2f}ms"


def test_benchmark_in_process_context_pipeline(large_repo: Path):
    """3. In-process context pipeline benchmark (filtering, secret redaction, token budgeting) (SLA: < 500ms)."""
    db_path = large_repo / ".buildcoach" / "bench_ctx.db"
    db = Database(db_path)
    scanner = ProjectScanner(project_root=large_repo, db_path=db_path)
    scan_res = scanner.scan()

    detector = ContextDetector(large_repo, scan_res.project.id)
    changeset = detector.collect()

    engine = ContextEngine(db=db)
    request = ContextRequest(
        project_id=scan_res.project.id,
        change_set=changeset,
        purpose=ContextPurpose.CHANGE_EXPLANATION,
        token_budget=4000,
    )

    # 1 warm-up run
    engine.build_context_packet(request)

    # 5 measured runs
    durations_ms: List[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        packet = engine.build_context_packet(request)
        t1 = time.perf_counter()
        durations_ms.append((t1 - t0) * 1000.0)

    median_duration = statistics.median(durations_ms)
    sla_target = 500.0  # 500 milliseconds
    passed = median_duration < sla_target

    report = format_benchmark_report(
        benchmark_name="in_process_context_pipeline",
        file_count=len(scan_res.files),
        warmup_count=1,
        measured_count=5,
        measured_durations_ms=durations_ms,
        sla_target_ms=sla_target,
        passed=passed,
    )
    print(report)
    assert packet is not None
    assert len(durations_ms) == 5
    assert passed, f"in_process_context_pipeline SLA violated: median {median_duration:.2f}ms exceeds target {sla_target:.2f}ms"


def test_benchmark_cli_json_overhead(standard_repo: Path):
    """4. In-process CLI execution overhead (status --json) (SLA: < 250ms)."""
    from backend.cli.main import main
    from unittest.mock import patch
    argv = ["--project-root", str(standard_repo), "status", "--json"]

    # 2 warm-up runs
    for _ in range(2):
        with patch("sys.stdout", io.StringIO()):
            main(argv)

    # 5 measured runs
    durations_ms: List[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = main(argv)
        t1 = time.perf_counter()
        assert rc == 0
        durations_ms.append((t1 - t0) * 1000.0)

    median_duration = statistics.median(durations_ms)
    sla_target = 250.0  # 250 milliseconds
    passed = median_duration < sla_target

    report = format_benchmark_report(
        benchmark_name="cli_status_json_overhead",
        file_count=len([f for f in standard_repo.iterdir() if f.is_file()]),
        warmup_count=2,
        measured_count=5,
        measured_durations_ms=durations_ms,
        sla_target_ms=sla_target,
        passed=passed,
    )
    print(report)
    assert rc == 0
    assert len(durations_ms) == 5
    assert passed, f"cli_status_json_overhead SLA violated: median {median_duration:.2f}ms exceeds target {sla_target:.2f}ms"
