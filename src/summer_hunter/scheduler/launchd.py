"""launchd LaunchAgent generation (user-level, no root, no security bypass)."""

from __future__ import annotations

import plistlib
from pathlib import Path

NIGHT_LABEL = "com.summerhunter.night"
BOT_LABEL = "com.summerhunter.bot"
WEB_LABEL = "com.summerhunter.web"
OS_LABEL = "com.summerhunter.os"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def night_plist(executable: Path, project_root: Path, log_dir: Path) -> dict:
    """Runs `summer-hunter night` at 00:05 and 06:00 local time.

    StartCalendarInterval uses the Mac's local time zone (expected: Europe/Paris). If the Mac is
    asleep at those times, launchd runs the job once at wake; the runner then catches up.
    `caffeinate -i -s` keeps the Mac from idle-sleeping while the job runs (supported API).
    """
    return {
        "Label": NIGHT_LABEL,
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", "-s", str(executable), "night"],
        "WorkingDirectory": str(project_root),
        "StartCalendarInterval": [{"Hour": 0, "Minute": 5}, {"Hour": 6, "Minute": 0}],
        "StandardOutPath": str(log_dir / "launchd-night.out.log"),
        "StandardErrorPath": str(log_dir / "launchd-night.err.log"),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin", "PYTHONUNBUFFERED": "1"},
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 5,
    }


def bot_plist(executable: Path, project_root: Path, log_dir: Path) -> dict:
    """Optional always-on Telegram bot (long polling) so buttons and commands answer during the day."""
    return {
        "Label": BOT_LABEL,
        "ProgramArguments": [str(executable), "bot"],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 60,
        "StandardOutPath": str(log_dir / "launchd-bot.out.log"),
        "StandardErrorPath": str(log_dir / "launchd-bot.err.log"),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin", "PYTHONUNBUFFERED": "1"},
        "ProcessType": "Background",
    }


def web_plist(executable: Path, project_root: Path, log_dir: Path) -> dict:
    """Mini App server + Cloudflare quick tunnel, kept alive."""
    data = bot_plist(executable, project_root, log_dir)
    data.update({"Label": WEB_LABEL, "ProgramArguments": [str(executable), "web"],
                 "StandardOutPath": str(log_dir / "launchd-web.out.log"),
                 "StandardErrorPath": str(log_dir / "launchd-web.err.log")})
    return data


def os_plist(executable: Path, project_root: Path, log_dir: Path) -> dict:
    """Summer Hunter OS (API 127.0.0.1:8800 + Next.js CRM 127.0.0.1:3000), kept alive across reboots.

    Runs scripts/os-dev.sh without opening a browser; ~/.local/bin is on PATH for node and pnpm.
    """
    data = bot_plist(executable, project_root, log_dir)
    path = f"{Path.home() / '.local' / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
    data.update({"Label": OS_LABEL, "ProgramArguments": ["/bin/bash", str(project_root / "scripts" / "os-dev.sh")],
                 "StandardOutPath": str(log_dir / "launchd-os.out.log"),
                 "StandardErrorPath": str(log_dir / "launchd-os.err.log"),
                 "EnvironmentVariables": {"PATH": path, "PYTHONUNBUFFERED": "1", "SH_OS_NO_BROWSER": "1"}})
    return data


def write_plist(data: dict, label: str, agents_dir: Path = AGENTS_DIR) -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{label}.plist"
    with path.open("wb") as handle:
        plistlib.dump(data, handle)
    return path
