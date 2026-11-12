from pathlib import Path

from summer_hunter.scheduler import launchd


def test_os_plist_runs_dev_script_headless_and_kept_alive(tmp_path: Path) -> None:
    data = launchd.os_plist(tmp_path / "summer-hunter", tmp_path, tmp_path / "logs")
    assert data["Label"] == launchd.OS_LABEL
    assert data["ProgramArguments"] == ["/bin/bash", str(tmp_path / "scripts" / "os-dev.sh")]
    assert data["RunAtLoad"] is True and data["KeepAlive"] == {"SuccessfulExit": False}
    env = data["EnvironmentVariables"]
    assert env["SH_OS_NO_BROWSER"] == "1"
    assert str(Path.home() / ".local" / "bin") in env["PATH"]
