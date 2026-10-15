"""Tests for schedule.py and uninstall.py (tasks 6.3, 6.4) — schtasks is always faked."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from daily_agenda import schedule, uninstall


class FakeSchtasks:
    def __init__(self, rc: int = 0, stdout: str = ""):
        self.rc = rc
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        assert kwargs.get("encoding") == "utf-8"
        return subprocess.CompletedProcess(cmd, self.rc, stdout=self.stdout, stderr="")


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_validate_time():
    assert schedule.validate_time("08:30") == "08:30"
    for bad in ("8:30", "24:00", "08:60", "0830", "08:30; calc", ""):
        with pytest.raises(ValueError):
            schedule.validate_time(bad)


def test_launcher_is_noninteractive_and_quoted(home: Path, tmp_path: Path):
    root = tmp_path / "plugin root"  # contains a space
    launcher = schedule.write_launcher(home, plugin_root=root, uv="C:\\uv\\uv.exe")
    text = launcher.read_text(encoding="utf-8")
    assert "DAILY_AGENDA_NONINTERACTIVE=1" in text
    assert "run --noninteractive" in text
    assert f'"{root / "scripts"}"' in text
    # never launches an interactive or tool-enabled Claude session
    assert "claude -p" not in text and "claude.exe" not in text


def test_install_registers_daily_task(home: Path, tmp_path: Path):
    fake = FakeSchtasks()
    res = schedule.install(home, time="08:30", plugin_root=tmp_path, uv="uv", runner=fake)
    cmd = fake.calls[0]
    assert cmd[:2] == ["schtasks", "/Create"]
    assert cmd[cmd.index("/TN") + 1] == schedule.TASK_NAME
    assert cmd[cmd.index("/SC") + 1] == "DAILY"
    assert cmd[cmd.index("/ST") + 1] == "08:30"
    assert "/RU" not in cmd and "/RL" not in cmd  # current user, limited, logged-on only
    assert res["ok"] is True


def test_install_rejects_bad_time(home: Path, tmp_path: Path):
    fake = FakeSchtasks()
    with pytest.raises(ValueError):
        schedule.install(home, time="25:00", plugin_root=tmp_path, uv="uv", runner=fake)
    assert fake.calls == []


def test_remove_and_status(home: Path):
    fake = FakeSchtasks()
    assert schedule.remove(home, runner=fake)["ok"] is True
    assert fake.calls[0][:2] == ["schtasks", "/Delete"]
    missing = FakeSchtasks(rc=1, stdout="ERROR: The system cannot find the file specified.")
    assert schedule.status(runner=missing) == {"registered": False}
    present = FakeSchtasks(stdout="TaskName: \\daily-agenda\nNext Run Time: 2026/10/9 08:30:00\n")
    assert schedule.status(runner=present)["registered"] is True


def test_uninstall_keeps_output_and_home_by_default(home: Path, tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "agenda-2026-10-08.html").write_text("x", encoding="utf-8")
    (home / "runtime").mkdir()
    fake = FakeSchtasks()
    res = uninstall.uninstall(home, purge=False, runner=fake)
    assert home.exists() and (out / "agenda-2026-10-08.html").exists()
    assert res["schedule_removed"] is True and res["home_removed"] is False


def test_uninstall_purge_removes_home_only(home: Path, tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "agenda.html").write_text("x", encoding="utf-8")
    (home / "gen" / "1").mkdir(parents=True)
    res = uninstall.uninstall(home, purge=True, runner=FakeSchtasks())
    assert not home.exists()
    assert (out / "agenda.html").exists()
    assert res["home_removed"] is True


def test_uninstall_refuses_suspicious_home(tmp_path: Path):
    for bad in (Path(tmp_path.anchor), Path.home()):
        with pytest.raises(ValueError):
            uninstall.uninstall(bad, purge=True, runner=FakeSchtasks())
