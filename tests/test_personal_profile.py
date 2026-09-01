import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from healthlog.personal_profile import (
    medical_index_template,
    personal_profile_template,
    runtime_profile_context,
    settings_v2_from_legacy,
    validate_profile_bundle,
)
from healthlog.presentation import audit_static_html
from healthlog.profile_presentation import render_personal_profile_html


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def legacy_settings() -> dict:
    return {
        "schema_version": 1,
        "profile": {
            "label": "owner-1",
            "age_years": 23,
            "sex": "male",
            "height_cm": 178,
            "weight_kg": 72,
            "goals": ["增肌"],
            "activity": {"work_pattern": "desk", "strength_sessions_per_week": "2"},
        },
        "targets": {"protein_g": [100, 140]},
        "diet_context": {"food_photo_means_consumed": True},
        "health_context": {
            "digestive": "user-reported note",
            "skin": "",
            "sleep": "",
            "caffeine": "",
            "supplement_guardrails": ["review interactions"],
        },
        "privacy": {"allow_usda_text_queries": False},
        "pipeline": {
            "shortcut_name": "test",
            "daily_records_directory": "data/daily",
            "runtime_directory": "runtime",
            "site_directory": "site",
        },
    }


class PersonalProfileTests(unittest.TestCase):
    def test_legacy_profile_migrates_to_canonical_runtime_context(self) -> None:
        settings = legacy_settings()
        document = personal_profile_template(settings, reviewed_on=date(2026, 9, 1))
        medical = medical_index_template("owner-1")

        errors, warnings = validate_profile_bundle(document, medical)
        context = runtime_profile_context(settings, document)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(document["profile_id"], "owner-1")
        self.assertEqual(document["nutrition_targets"]["protein_g"], [100, 140])
        self.assertEqual(context["profile"]["height_cm"], 178)
        self.assertEqual(context["health_context"]["digestive"], "user-reported note")

    def test_schema_v2_settings_contain_no_personal_health_facts(self) -> None:
        settings = settings_v2_from_legacy(legacy_settings())

        self.assertEqual(settings["schema_version"], 2)
        self.assertEqual(settings["active_profile_id"], "owner-1")
        for private_key in ("profile", "targets", "diet_context", "health_context"):
            self.assertNotIn(private_key, settings)

    def test_medical_index_rejects_traversal_and_dangling_reference(self) -> None:
        profile = personal_profile_template(
            legacy_settings(), reviewed_on=date(2026, 9, 1)
        )
        profile["health_status"]["conditions"] = [
            {
                "id": "condition-1",
                "name": "example",
                "status": "monitoring",
                "record_ids": ["missing-record"],
                "notes": [],
            }
        ]
        medical = medical_index_template("owner-1")
        medical["records"] = [
            {
                "id": "record-1",
                "date": "2026-08-31",
                "category": "examination",
                "title": "Example",
                "provider": "",
                "status": "historical",
                "summary": [],
                "findings": [],
                "source_files": ["../outside.pdf"],
                "tags": [],
                "notes": [],
            }
        ]

        errors, _ = validate_profile_bundle(profile, medical)

        self.assertTrue(any("安全相对路径" in error for error in errors))
        self.assertTrue(any("不存在的病历" in error for error in errors))

    def test_profile_rejects_targets_that_would_break_daily_rendering(self) -> None:
        profile = personal_profile_template(
            legacy_settings(), reviewed_on=date(2026, 9, 1)
        )
        profile["nutrition_targets"]["protein_g"] = "high"

        errors, _ = validate_profile_bundle(profile, medical_index_template("owner-1"))

        self.assertTrue(any("nutrition_targets.protein_g" in error for error in errors))

    def test_medication_record_references_are_cross_checked(self) -> None:
        profile = personal_profile_template(
            legacy_settings(), reviewed_on=date(2026, 9, 1)
        )
        profile["health_status"]["medications"] = [
            {
                "id": "medication-1",
                "name": "Example",
                "status": "active",
                "record_ids": ["missing-prescription"],
                "notes": [],
            }
        ]

        errors, _ = validate_profile_bundle(profile, medical_index_template("owner-1"))

        self.assertTrue(any("不存在的病历" in error for error in errors))

    def test_profile_html_summarizes_but_never_links_raw_record(self) -> None:
        profile = personal_profile_template(
            legacy_settings(), reviewed_on=date(2026, 9, 1)
        )
        medical = medical_index_template("owner-1")
        medical["records"] = [
            {
                "id": "record-1",
                "date": "2026-08-31",
                "category": "examination",
                "title": "Example examination",
                "provider": "",
                "status": "historical",
                "summary": ["Structured summary"],
                "findings": [],
                "source_files": ["files/private-record.pdf"],
                "tags": [],
                "notes": [],
            }
        ]
        page_text = render_personal_profile_html(
            profile,
            medical,
            generated_at="2026-09-01T12:00:00+08:00",
            profile_source="data/profiles/owner-1/profile.json",
            medical_index_source="data/profiles/owner-1/medical/index.json",
        )

        with tempfile.TemporaryDirectory() as raw_directory:
            page = Path(raw_directory) / "index.html"
            page.write_text(page_text, encoding="utf-8")
            errors, _, audit = audit_static_html(page)

        self.assertEqual(errors, [])
        self.assertEqual(audit.references, [])
        self.assertIn("Example examination", page_text)
        self.assertIn("原件 1 份", page_text)
        self.assertIn("每日本地提醒", page_text)
        self.assertNotIn("private-record.pdf", page_text)

    def test_cli_initializes_and_renders_a_schema_v2_profile(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "config").mkdir()
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
            environment = dict(os.environ, HEALTHLOG_ROOT=str(root))

            for command in (["profile-init"], ["profile"], ["dashboard"]):
                completed = subprocess.run(
                    [str(PROJECT_ROOT / "bin" / "diet"), *command],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode, 0, completed.stderr + completed.stdout
                )

            profile_path = root / "data" / "profiles" / "owner-2" / "profile.json"
            medical_path = (
                root / "data" / "profiles" / "owner-2" / "medical" / "index.json"
            )
            page_path = root / "site" / "profile" / "index.html"
            dashboard_path = root / "site" / "index.html"
            self.assertTrue(profile_path.is_file())
            self.assertTrue(medical_path.is_file())
            self.assertTrue(page_path.is_file())
            self.assertIn("owner-2", page_path.read_text(encoding="utf-8"))
            self.assertIn("个人简介与病历", dashboard_path.read_text(encoding="utf-8"))
            self.assertFalse(any((root / "data").rglob("*.html")))

            actual = medical_path.parent / "files" / "actual-record.pdf"
            actual.write_text("private", encoding="utf-8")
            medical = json.loads(medical_path.read_text(encoding="utf-8"))
            medical["records"] = [
                {
                    "id": "changed-record",
                    "date": "2026-09-01",
                    "category": "examination",
                    "title": "Changed record",
                    "provider": "",
                    "status": "historical",
                    "summary": [],
                    "findings": [],
                    "source_files": [
                        {
                            "path": "files/actual-record.pdf",
                            "sha256": "0" * 64,
                            "media_type": "application/pdf",
                        }
                    ],
                    "tags": [],
                    "notes": [],
                }
            ]
            medical_path.write_text(json.dumps(medical), encoding="utf-8")
            changed = subprocess.run(
                [str(PROJECT_ROOT / "bin" / "diet"), "profile"],
                cwd=PROJECT_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(changed.returncode, 2)
            self.assertIn("原件 SHA-256 不一致", changed.stderr)

            outside = root / "outside-record.pdf"
            outside.write_text("private", encoding="utf-8")
            linked = medical_path.parent / "files" / "linked-record.pdf"
            linked.symlink_to(outside)
            medical = json.loads(medical_path.read_text(encoding="utf-8"))
            medical["records"] = [
                {
                    "id": "linked-record",
                    "date": "2026-09-01",
                    "category": "examination",
                    "title": "Linked record",
                    "provider": "",
                    "status": "historical",
                    "summary": [],
                    "findings": [],
                    "source_files": ["files/linked-record.pdf"],
                    "tags": [],
                    "notes": [],
                }
            ]
            medical_path.write_text(json.dumps(medical), encoding="utf-8")
            rejected = subprocess.run(
                [str(PROJECT_ROOT / "bin" / "diet"), "profile"],
                cwd=PROJECT_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("越过 medical/files 边界", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
