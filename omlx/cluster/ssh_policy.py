# SPDX-License-Identifier: Apache-2.0
"""One non-interactive OpenSSH policy for every cluster subprocess, and one
retrying way to actually run it.

Cluster peers are commonly discovered through both Bonjour names and changing
Thunderbolt IP addresses.  OpenSSH's default ``ask`` policy turns a harmless
new alias for an already-known key into an interactive prompt, which a server
process cannot answer.  ``accept-new`` records a first-seen alias without a
prompt while still refusing a changed key for an alias already on record.

``CheckHostIP=no`` is explicit because a user's ssh_config may enable it.  The
host name (or literal address when that is what the operator selected) remains
the known_hosts identity; DHCP and Thunderbolt address movement must not add a
second implicit IP check behind it.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MANAGED_IDENTITY = "~/.ssh/omlx_cluster"
# Pause between attempts. mDNS for a .local peer fails in short bursts.
_RETRY_DELAY_SECONDS = 0.5


def cluster_ssh_options(
    *,
    connect_timeout: float | None = None,
    keepalive: bool = False,
) -> list[str]:
    """Return ``-o`` arguments that never read from an interactive terminal."""

    options = [
        "BatchMode=yes",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        # Automatic cluster fabric discovery currently emits IPv4 addresses.
        # Keep the SSH control channel on the same address family instead of
        # letting a .local name select an unrelated or unroutable AAAA record.
        # The hostname remains the known_hosts identity; only resolution is
        # constrained, so pairing survives address changes.
        "AddressFamily=inet",
        "StrictHostKeyChecking=accept-new",
        "CheckHostIP=no",
        # The pairing UI creates this dedicated identity. Naming it explicitly
        # makes the exchanged key usable without ssh-agent or ~/.ssh/config,
        # while OpenSSH can still fall back to an operator's existing keys.
        f"IdentityFile={_MANAGED_IDENTITY}",
        # Suppress the benign "permanently added" / "known by other names"
        # chatter. Changed-key failures are errors and remain visible.
        "LogLevel=ERROR",
    ]
    if connect_timeout is not None:
        if isinstance(connect_timeout, bool) or connect_timeout <= 0:
            raise ValueError("SSH connect timeout must be positive")
        options.append(f"ConnectTimeout={int(max(1, connect_timeout))}")
    if keepalive:
        options.extend(
            (
                "ServerAliveInterval=15",
                "ServerAliveCountMax=4",
                "TCPKeepAlive=yes",
            )
        )
    return [part for option in options for part in ("-o", option)]


def apply_cluster_ssh_policy(
    argv: Sequence[str],
    *,
    connect_timeout: float | None = None,
    keepalive: bool = False,
) -> list[str]:
    """Insert the shared policy into an ``ssh`` or ``scp`` command."""

    if not argv or Path(argv[0]).name not in {"ssh", "scp"}:
        raise ValueError("cluster SSH policy requires an ssh or scp command")
    return [
        argv[0],
        *cluster_ssh_options(
            connect_timeout=connect_timeout,
            keepalive=keepalive,
        ),
        *argv[1:],
    ]


def run_ssh_retrying(
    argv: Sequence[str],
    *,
    timeout: float,
    attempts: int = 3,
    runner: Callable[..., Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ssh or scp with bounded retries; OSError becomes returncode 255.

    A timeout is not retried, so one hung peer costs one ``timeout``. Pass
    ``runner`` to inject a fake. The default is read at call time, so a test
    can patch ``subprocess.run``.
    """

    run = runner if runner is not None else subprocess.run
    argv = list(argv)
    result = subprocess.CompletedProcess(argv, 255, "", "")
    for attempt in range(attempts):
        try:
            result = run(
                argv, capture_output=True, text=True, check=False, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            logger.debug("%s timed out after %ss", argv[:1], timeout)
            return subprocess.CompletedProcess(
                argv, 255, "", f"timed out after {timeout}s"
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = subprocess.CompletedProcess(argv, 255, "", str(exc))
        if result.returncode == 0:
            return result
        logger.debug(
            "attempt %d/%d of %s exited %s: %s",
            attempt + 1,
            attempts,
            argv[:1],
            result.returncode,
            str(result.stderr or "").strip()[:200],
        )
        if attempt < attempts - 1:
            time.sleep(_RETRY_DELAY_SECONDS)
    return result
