import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def png_bytes(red: int, green: int, blue: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
        )

    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(bytes((0, red, green, blue)))
    return (
        signature
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def test_profile() -> dict:
    day_types = ("unknown", "rest", "strength", "swim", "tennis", "mixed")
    return {
        "schema_version": 1,
        "targets": {
            "energy_kcal": {key: [1800, 2200] for key in day_types},
            "protein_g": [90, 130],
            "carbohydrate_g": {key: [200, 300] for key in day_types},
            "fat_g": [50, 80],
            "fiber_g": [25, 35],
            "sodium_mg_max": 2000,
        },
        "pipeline": {
            "shortcut_name": "test",
            "daily_records_directory": "data/daily",
            "runtime_directory": "runtime",
            "site_directory": "site",
        },
    }


class MediaRetentionTests(unittest.TestCase):
    def test_render_purges_only_unrelated_workspace_copy_and_reexports(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            outer = Path(raw_root)
            root = outer / "health"
            photos = outer / "mock-apple-photos"
            record_dir = root / "data" / "daily" / "20260103"
            (root / "config").mkdir(parents=True)
            photos.mkdir()
            record_dir.mkdir(parents=True)
            (root / "config" / "health_profile.json").write_text(
                json.dumps(test_profile()), encoding="utf-8"
            )

            food = record_dir / "food.png"
            unrelated = record_dir / "unrelated.png"
            photo_original = photos / unrelated.name
            food.write_bytes(png_bytes(220, 40, 40))
            photo_original.write_bytes(png_bytes(30, 70, 210))
            shutil.copy2(photo_original, unrelated)

            environment = dict(os.environ, HEALTHLOG_ROOT=str(root))

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [str(PROJECT_ROOT / "bin" / "diet"), *arguments],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )

            prepared = run("prepare", "2026-01-03", "--skip-export")
            self.assertEqual(prepared.returncode, 0, prepared.stderr + prepared.stdout)
            analysis_path = record_dir / "analysis.json"
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            for row in analysis["images"]:
                if row["file"] == food.name:
                    row["classification"] = "possible_food"
                    row["observations"] = ["food-related test image"]
                else:
                    row["classification"] = "unrelated"
                    row["observations"] = ["non-food test image"]
            analysis["day_context"].update(
                {"day_type": "rest", "photo_coverage": "partial"}
            )
            analysis["overall_confidence"] = "medium"
            analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

            manifest_path = (
                root / "runtime" / "daily" / "20260103" / "pipeline" / "manifest.json"
            )
            before = json.loads(manifest_path.read_text(encoding="utf-8"))
            unrelated_before = next(
                asset for asset in before["assets"] if asset["file"] == unrelated.name
            )
            preview_before = (
                root / unrelated_before["preview_path"]
                if unrelated_before["preview_path"]
                else None
            )

            rendered = run("render", "2026-01-03")
            verified = run("verify", "2026-01-03")
            self.assertEqual(rendered.returncode, 0, rendered.stderr + rendered.stdout)
            self.assertEqual(verified.returncode, 0, verified.stderr + verified.stdout)
            self.assertIn("DELETED_WORKSPACE_COPIES=1", rendered.stdout)
            self.assertIn("APPLE_PHOTOS_ORIGINALS=untouched", rendered.stdout)
            self.assertTrue(food.is_file())
            self.assertFalse(unrelated.exists())
            self.assertTrue(photo_original.is_file())
            if preview_before is not None:
                self.assertFalse(preview_before.exists())

            audit_path = record_dir / "media-audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(len(audit["purged_assets"]), 1)
            self.assertEqual(
                audit["purged_assets"][0]["source_scope"],
                "health_workspace_export_copy",
            )
            after = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(after["asset_count"], 2)
            self.assertEqual(after["retained_asset_count"], 1)
            self.assertEqual(after["purged_asset_count"], 1)
            unrelated_after = next(
                asset for asset in after["assets"] if asset["file"] == unrelated.name
            )
            self.assertEqual(unrelated_after["storage_state"], "purged_unrelated")
            self.assertIsNone(unrelated_after["preview_path"])

            report_html = root / "site" / "daily" / "20260103" / "index.html"
            report_md = root / "runtime" / "daily" / "20260103" / "README.md"
            self.assertNotIn(unrelated.name, report_html.read_text(encoding="utf-8"))
            self.assertNotIn(unrelated.name, report_md.read_text(encoding="utf-8"))

            shutil.copy2(photo_original, unrelated)
            repeated = run("prepare", "2026-01-03", "--skip-export")
            self.assertEqual(repeated.returncode, 0, repeated.stderr + repeated.stdout)
            self.assertIn("REEXPORTED_UNRELATED_PURGED=1", repeated.stdout)
            self.assertFalse(unrelated.exists())
            self.assertTrue(photo_original.is_file())
            reverified = run("verify", "2026-01-03")
            self.assertEqual(
                reverified.returncode, 0, reverified.stderr + reverified.stdout
            )

            shutil.rmtree(root / "runtime")
            rebuilt = run("rebuild-db")
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr + rebuilt.stdout)
            rebuilt_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(rebuilt_manifest["purged_asset_count"], 1)


if __name__ == "__main__":
    unittest.main()
