"""Hermetic deploy targeting and key-auth regression tests."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "stale", ["VPS_HOST=83.219.159.2\n", "VPS_PORT=22\n", "VPS_USER=old\n"]
)
def test_ship_refuses_old_target_without_side_effects(
    tmp_path: Path, stale: str
) -> None:
    result, calls, _ = _run_ship(tmp_path, stale)
    assert result.returncode != 0
    assert "production target mismatch" in result.stderr
    assert not calls.exists()


def test_ship_key_auth_expands_tilde_without_password(tmp_path: Path) -> None:
    result, calls, identity = _run_ship(tmp_path, "")
    assert result.returncode == 0, result.stderr
    args = calls.read_text().splitlines()
    assert args[args.index("-i") + 1] == str(identity)
    assert "dev01@162.55.137.149" in args
    assert args[args.index("-p") + 1] == "1996"
    assert "IdentitiesOnly=yes" in args


def _run_ship(tmp_path: Path, extra: str):
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts" / "ship.sh"
    target = scripts / "ship.sh"
    target.write_text(source.read_text())
    home = tmp_path / "home"
    identity = home / ".ssh" / "deploy"
    identity.parent.mkdir(parents=True)
    identity.write_text("test-key\n")
    (repo / ".env.local").write_text(
        "VPS_HOST=162.55.137.149\nVPS_USER=dev01\nVPS_PORT=1996\n"
        "VPS_SSH_IDENTITY_FILE=~/.ssh/deploy\n" + extra
    )
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    calls = tmp_path / "remote.log"
    for name in ("ssh", "sshpass", "git"):
        stub = fakebin / name
        stub.write_text(
            f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {calls}\n"
            + ("exit 0\n" if name == "ssh" else "exit 23\n")
        )
        stub.chmod(0o755)
    env = dict(
        os.environ,
        HOME=str(home),
        PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    result = subprocess.run(
        ["bash", str(target), "--no-push"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, calls, identity
