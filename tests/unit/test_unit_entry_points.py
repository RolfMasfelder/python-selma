# ============================================================
# Entry-Point-Tests: pyproject [project.scripts] → CLI-Befehle
#
# Deckung für:
#   - pyproject [project.scripts] (Single-Source-of-Truth)
#   - selma/gateway.py::main
#   - selma/setup.py::main
#   - selma/dashboard_cli.py (find_dashboard_path / build_command / main)
#
# Run via: source venv/bin/activate && python -m pytest tests/unit -q
# ============================================================

import importlib
import importlib.util
import sys
import tomllib
from pathlib import Path

import selma.dashboard_cli as dashboard_cli
import selma.gateway as gateway
import selma.setup as setup

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_bytes() -> bytes:
    return (PROJECT_ROOT / "pyproject.toml").read_bytes()


# --- pyproject: [project.scripts] vorhanden & korrekt ------------------------


def test_pyproject_declares_all_three_scripts():
    data = tomllib.loads(_pyproject_bytes().decode("utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["selma-gateway"] == "selma.gateway:main"
    assert scripts["selma-setup"] == "selma.setup:main"
    assert scripts["selma-dashboard"] == "selma.dashboard_cli:main"


def test_all_three_entry_points_resolve_to_functions():
    for dotted in ("selma.gateway:main", "selma.setup:main", "selma.dashboard_cli:main"):
        mod_name, func_name = dotted.split(":")
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, func_name)
        assert callable(fn), f"{dotted} nicht callable"


# --- gateway.main ------------------------------------------------------------


def test_gateway_main_falls_into_keyboard_interrupt(monkeypatch, capsys):
    calls = []

    def fake_run_gateway():
        calls.append("called")
        raise KeyboardInterrupt

    def fake_asyncio_run(coro):
        raise KeyboardInterrupt

    monkeypatch.setattr(gateway, "run_gateway", fake_run_gateway)
    monkeypatch.setattr(gateway.asyncio, "run", fake_asyncio_run)
    gateway.main()

    assert calls == ["called"]
    assert "Gateway shutting down" in capsys.readouterr().out


# --- setup.main --------------------------------------------------------------


def test_setup_main_delegates_to_setup(monkeypatch):
    calls = []
    monkeypatch.setattr(setup, "setup", lambda base_dir=".": calls.append(base_dir))
    setup.main()  # Default "."
    setup.main("/x/y")  # explizit
    assert calls == [".", "/x/y"]


# --- dashboard_cli -----------------------------------------------------------


def test_find_dashboard_path_handles_missing_module(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert dashboard_cli.find_dashboard_path() is None


def test_find_dashboard_path_returns_real_dashboard_module():
    p = dashboard_cli.find_dashboard_path()
    assert p is not None
    assert p.name == "dashboard.py"
    assert p.exists()


def test_build_command_shape_and_passthrough():
    cmd = dashboard_cli.build_command(Path("/x/y/dashboard.py"), ["--server.port=9999", "gateway-url=http://h:1/x"])
    assert cmd[:6] == [sys.executable, "-m", "streamlit", "run", "/x/y/dashboard.py", "--server.port=9999"]
    assert cmd[6] == "gateway-url=http://h:1/x"


def test_main_runs_streamlit_with_dashboard_path(monkeypatch, tmp_path, capsys):
    fake_app = tmp_path / "dashboard.py"
    fake_app.write_text("# noop")
    captured = {}

    class _FakeCompleted:
        returncode = 0

    def fake_subprocess_run(cmd, *a, **kw):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(dashboard_cli, "find_dashboard_path", lambda: fake_app)
    monkeypatch.setattr(dashboard_cli.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(sys, "argv", ["selma-dashboard", "--server.port=42"])
    rc = dashboard_cli.main()

    assert rc == 0
    assert captured["cmd"][4] == str(fake_app)
    assert "--server.port=42" in captured["cmd"]


def test_main_reports_install_hint_when_missing(monkeypatch, capsys):
    monkeypatch.setattr(dashboard_cli, "find_dashboard_path", lambda: None)
    rc = dashboard_cli.main()
    assert rc == 1
    assert "installieren" in capsys.readouterr().err
