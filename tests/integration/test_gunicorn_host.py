# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Opt-in real-process validation for the supported Gunicorn/Uvicorn topology."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from urllib.request import urlopen

import pytest

pytestmark = [
    pytest.mark.hosting,
    pytest.mark.skipif(
        os.environ.get("ORBIT_RUN_HOSTING_TESTS") != "1",
        reason="Set ORBIT_RUN_HOSTING_TESTS=1 to run real worker-process hosting tests.",
    ),
]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_http(port: int, process: subprocess.Popen[str], timeout: float = 15) -> bytes:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"Gunicorn exited with {process.returncode}: {output}")
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                return cast(bytes, response.read())
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError(f"Gunicorn did not become ready: {last_error}")


def _request_http(port: int, path: str = "/") -> bytes:
    """Fetch the smoke-test route and close the response before returning its body."""
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return cast(bytes, response.read())


def _request_burst(port: int, count: int = 64) -> list[bytes]:
    """Exercise bounded concurrent request admission across the worker pool."""
    with ThreadPoolExecutor(max_workers=min(16, count)) as executor:
        return list(executor.map(lambda _: _request_http(port), range(count)))


def _wait_for_markers(directory: Path, prefix: str, count: int, timeout: float = 5) -> list[Path]:
    """Wait briefly for every worker marker to become visible on disk."""
    deadline = time.monotonic() + timeout
    markers: list[Path] = []
    while time.monotonic() < deadline:
        markers = sorted(directory.glob(f"{prefix}*"))
        if len(markers) >= count:
            return markers
        time.sleep(0.05)
    return markers


def _write_host_app(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create one lifecycle-instrumented Runtime module for process-host tests."""
    module = tmp_path / "host_app.py"
    started = tmp_path / "service-started"
    stopped = tmp_path / "service-stopped"
    in_flight = tmp_path / "request-in-flight"
    started.mkdir()
    stopped.mkdir()
    in_flight.mkdir()
    module.write_text(
        "import asyncio\n"
        "import os\n"
        "from pathlib import Path\n"
        "from orbit import Application, ApplicationConfig, Service, ServiceDescriptor\n"
        "from orbit.asgi import Response\n"
        "from orbit.runtime import Runtime\n"
        "class Managed(Service):\n"
        "    descriptor = ServiceDescriptor(name='managed')\n"
        "    async def start(self):\n"
        f"        marker = Path({str(started)!r}) / f'started-{{os.getpid()}}'\n"
        "        marker.write_text('started', encoding='utf-8')\n"
        "    async def stop(self):\n"
        f"        marker = Path({str(stopped)!r}) / f'stopped-{{os.getpid()}}'\n"
        "        marker.write_text('stopped', encoding='utf-8')\n"
        "application = Application(ApplicationConfig(name='host-smoke'))\n"
        "application.register(Managed())\n"
        "@application.router.route('/', name='root')\n"
        "async def root(request):\n"
        "    return Response.text('ok')\n"
        "@application.router.route('/slow', name='slow')\n"
        "async def slow(request):\n"
        f"    marker = Path({str(in_flight)!r}) / f'request-{{os.getpid()}}'\n"
        "    marker.write_text('started', encoding='utf-8')\n"
        "    await asyncio.sleep(0.5)\n"
        "    return Response.text('slow')\n"
        "runtime = Runtime(application)\n",
        encoding="utf-8",
    )
    return started, stopped, in_flight


def test_two_uvicorn_workers_serve_reload_and_terminate(tmp_path: Path) -> None:
    """Exercise repeated load, worker replacement, draining, and per-worker cleanup."""
    started, stopped, in_flight = _write_host_app(tmp_path)
    port = _free_port()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gunicorn",
            "host_app:runtime",
            "--chdir",
            str(tmp_path),
            "--bind",
            f"127.0.0.1:{port}",
            "--workers",
            "2",
            "--worker-class",
            "uvicorn_worker.UvicornWorker",
            "--timeout",
            "10",
            "--graceful-timeout",
            "5",
            "--log-level",
            "warning",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert len(_wait_for_markers(started, "started-", 2)) == 2
        assert _wait_for_http(port, process) == b"ok"
        for _ in range(4):
            assert _request_burst(port) == [b"ok"] * 64
        process.send_signal(signal.SIGHUP)
        assert len(_wait_for_markers(started, "started-", 4)) >= 4
        assert _wait_for_http(port, process) == b"ok"
        for _ in range(4):
            assert _request_burst(port) == [b"ok"] * 64
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(_request_http, port, "/slow")
            assert len(_wait_for_markers(in_flight, "request-", 1)) == 1
            process.terminate()
            assert pending.result(timeout=10) == b"slow"
        assert process.wait(timeout=10) == 0
        markers = _wait_for_markers(stopped, "stopped-", 4)
        assert len(markers) >= 4
        assert all(marker.read_text(encoding="utf-8") == "stopped" for marker in markers)
    finally:
        if process.poll() is None:
            process.kill()
        try:
            # ``wait()`` reaps the child but does not close PIPE handles.  Drain and
            # close both streams so strict warning runs do not report leaked files.
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def test_uvicorn_development_process_serves_and_stops(tmp_path: Path) -> None:
    """Validate direct Uvicorn development hosting and ASGI lifespan cleanup."""
    started, stopped, _ = _write_host_app(tmp_path)
    port = _free_port()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "host_app:runtime",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--lifespan",
            "on",
            "--log-level",
            "warning",
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert len(_wait_for_markers(started, "started-", 1)) == 1
        assert _wait_for_http(port, process) == b"ok"
        for _ in range(2):
            assert _request_burst(port, count=16) == [b"ok"] * 16
        # Uvicorn's development CLI is stopped interactively with SIGINT; managed
        # production SIGTERM behavior is covered by the Gunicorn test above.
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=10) == 0
        assert len(_wait_for_markers(stopped, "stopped-", 1)) == 1
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
