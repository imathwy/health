import subprocess
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from healthlog.errors import PipelineError
from healthlog.media import run_shortcut


class ShortcutBoundaryTests(unittest.TestCase):
    def test_rejects_legacy_root_alias_even_if_it_could_be_a_symlink(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["shortcuts", "run", "test"],
            returncode=0,
            stdout=(
                "DATE=2026-08-31\n"
                "COUNT=0\n"
                "EXPORT_DIR=/repo/daily/20260831\n"
            ),
            stderr="",
        )
        with patch("healthlog.media.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(PipelineError, "输出目录不匹配"):
                run_shortcut(
                    date(2026, 8, 31),
                    "test",
                    10,
                    Path("/repo/data/daily/20260831"),
                )

    def test_accepts_the_exact_configured_record_directory(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["shortcuts", "run", "test"],
            returncode=0,
            stdout=(
                "DATE=2026-08-31\n"
                "COUNT=0\n"
                "EXPORT_DIR=/repo/data/daily/20260831\n"
            ),
            stderr="",
        )
        with patch("healthlog.media.subprocess.run", return_value=completed):
            result = run_shortcut(
                date(2026, 8, 31),
                "test",
                10,
                Path("/repo/data/daily/20260831"),
            )

        self.assertEqual(
            result["fields"]["EXPORT_DIR"],
            "/repo/data/daily/20260831",
        )


if __name__ == "__main__":
    unittest.main()
