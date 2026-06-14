"""
Ephemeral Tor SOCKS proxy for geo-blocked feeds.

`tor_socks_proxy()` yields a working `socks5://host:port` URL:
  1. if a proxy is already listening (Tor Browser on 9150, or a tor daemon on
     9050), reuse it and spawn nothing;
  2. else, if the `tor` binary is on PATH, launch an ephemeral daemon on a free
     port, wait for it to bootstrap, yield it, and terminate it on exit;
  3. else yield None (caller skips Tor sources).

Requires the `tor` binary for case 2 (`brew install tor`).
"""

import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from typing import Iterator

from loguru import logger

BOOTSTRAP_TIMEOUT = 90  # seconds to wait for "Bootstrapped 100%"


def _socks_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _parse_host_port(proxy_url: str) -> tuple[str, int]:
    rest = proxy_url.split("://", 1)[-1]
    host, _, port = rest.partition(":")
    return host or "127.0.0.1", int(port or 9050)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def tor_socks_proxy(preferred: str = "socks5://127.0.0.1:9150") -> Iterator[str | None]:
    """Yield a usable SOCKS proxy URL for Tor, or None if unavailable."""
    # 1. reuse an already-running proxy (Tor Browser 9150 or daemon 9050)
    for url in (preferred, "socks5://127.0.0.1:9050"):
        host, port = _parse_host_port(url)
        if _socks_reachable(host, port):
            logger.info(f"Tor: reusing running proxy {url}")
            yield url
            return

    # 2. spawn an ephemeral daemon
    tor_bin = shutil.which("tor")
    if not tor_bin:
        logger.warning(
            "Tor: no running proxy and `tor` binary not found "
            "(brew install tor) — skipping Tor sources"
        )
        yield None
        return

    port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="pathos-tor-")
    proc = subprocess.Popen(
        [
            tor_bin,
            "--SocksPort", str(port),
            "--DataDirectory", data_dir,
            "--ControlPort", "0",
            "--Log", "notice stdout",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        logger.info(f"Tor: launching ephemeral daemon on port {port}…")
        bootstrapped = False
        deadline = time.time() + BOOTSTRAP_TIMEOUT
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if "Bootstrapped 100%" in line:
                bootstrapped = True
                break

        if bootstrapped:
            logger.success(f"Tor: daemon ready on port {port}")
            yield f"socks5://127.0.0.1:{port}"
        else:
            logger.warning("Tor: daemon failed to bootstrap — skipping Tor sources")
            yield None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)
        logger.info("Tor: ephemeral daemon stopped")
