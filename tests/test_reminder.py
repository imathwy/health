import json
import os
import plistlib
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from healthlog.reminder import (
    DEFAULT_REMINDER_MESSAGE,
    DailyTime,
    ReminderConfig,
    build_reminder_config,
    launch_agent_label,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReminderDomainTests(unittest.TestCase):
    def test_time_and_config_are_strict_and_round_trip(self) -> None:
        config = build_reminder_config(
            profile_id="owner-1",
            agent_label=launch_agent_label("owner-1", "0123456789ab"),
            time_text="21:30",
            message=None,
            open_dashboard=None,
            updated_at=datetime(2026, 9, 1, 13, 0, tzinfo=timezone.utc),
        )

        restored = ReminderConfig.from_document(
            config.to_document(), expected_profile_id="owner-1"
        )

        self.assertEqual(restored.time, DailyTime(hour=21, minute=30))
        self.assertEqual(restored.message, DEFAULT_REMINDER_MESSAGE)
        self.assertFalse(restored.open_dashboard)
        for invalid in ("9:30", "24:00", "21:60", "tomorrow"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                DailyTime.parse(invalid)


class ReminderCliTests(unittest.TestCase):
    def test_set_status_test_update_and_remove_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "config").mkdir()
            source_package = root / "src" / "healthlog"
            source_package.mkdir(parents=True)
            (source_package / "__main__.py").write_text("", encoding="utf-8")
            settings = {
                "schema_version": 2,
                "active_profile_id": "owner-2",
                "privacy": {"allow_usda_text_queries": False},
                "pipeline": {
                    "shortcut_name": "test",
                    "daily_records_directory": "data/daily",
                    "profile_records_directory": "data/profiles",
                    "runtime_directory": "runtime",
                    "site_directory": "site",
                },
            }
            (root / "config" / "health_profile.json").write_text(
                json.dumps(settings), encoding="utf-8"
            )
            fake_tool = root / "fake-tool"
            fake_tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_tool.chmod(0o755)
            agents = root / "launch-agents"
            environment = dict(
                os.environ,
                HEALTHLOG_ROOT=str(root),
                HEALTHLOG_LAUNCH_AGENTS_DIR=str(agents),
                HEALTHLOG_LAUNCHCTL=str(fake_tool),
                HEALTHLOG_OSASCRIPT=str(fake_tool),
                HEALTHLOG_OPEN=str(fake_tool),
            )

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [str(PROJECT_ROOT / "bin" / "diet"), *arguments],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )

            initialized = run("profile-init")
            self.assertEqual(
                initialized.returncode, 0, initialized.stderr + initialized.stdout
            )
            installed = run(
                "reminder",
                "set",
                "--time",
                "21:45",
                "--message",
                "Review today's local log.",
            )
            self.assertEqual(
                installed.returncode, 0, installed.stderr + installed.stdout
            )

            config_path = root / "config" / "reminder.local.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            agent_path = agents / f"{config['launch_agent']['label']}.plist"
            with agent_path.open("rb") as handle:
                agent = plistlib.load(handle)
            self.assertEqual(config["schedule"]["time"], "21:45")
            self.assertEqual(agent["StartCalendarInterval"], {"Hour": 21, "Minute": 45})
            self.assertEqual(
                agent["ProgramArguments"][1:],
                ["-m", "healthlog", "reminder", "fire"],
            )
            self.assertEqual(
                agent["EnvironmentVariables"]["PYTHONPATH"],
                str((root / "src").resolve()),
            )
            self.assertEqual(stat.S_IMODE(agent_path.stat().st_mode), 0o600)

            status_result = run("reminder", "status")
            test_result = run("reminder", "test")
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            self.assertIn("REMINDER_STATUS=active", status_result.stdout)
            self.assertEqual(test_result.returncode, 0, test_result.stderr)
            self.assertTrue(
                (root / "runtime" / "reminders" / "last_fire.json").is_file()
            )
            profile_page = root / "site" / "profile" / "index.html"
            self.assertIn("21:45", profile_page.read_text(encoding="utf-8"))
            self.assertIn("已启用", profile_page.read_text(encoding="utf-8"))

            updated = run("reminder", "set", "--time", "22:10")
            self.assertEqual(updated.returncode, 0, updated.stderr)
            updated_config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_config["schedule"]["time"], "22:10")
            self.assertEqual(
                updated_config["notification"]["message"],
                "Review today's local log.",
            )

            removed = run("reminder", "remove")
            disabled = run("reminder", "status")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(config_path.exists())
            self.assertFalse(agent_path.exists())
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            self.assertIn("REMINDER_STATUS=disabled", disabled.stdout)
            self.assertIn("未启用", profile_page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
