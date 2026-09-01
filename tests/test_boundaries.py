import ast
import tempfile
import unittest
from datetime import date
from pathlib import Path

from healthlog.errors import PipelineError
from healthlog.media import manifest_preview_path
from healthlog.media_retention import manifest_source_path, retention_preview_path
from healthlog.presentation import audit_static_html
from healthlog.workspace import (
    PersonalProfilePaths,
    WorkspacePaths,
    paths_for,
    personal_profile_paths,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def local_imports(module_name: str) -> set[str]:
    source = PROJECT_ROOT / "src" / "healthlog" / f"{module_name}.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
    }


def profile(**pipeline_overrides: str) -> dict:
    pipeline = {
        "daily_records_directory": "data/daily",
        "runtime_directory": "runtime",
        "site_directory": "site",
    }
    pipeline.update(pipeline_overrides)
    return {"pipeline": pipeline}


class LayerBoundaryTests(unittest.TestCase):
    def test_domain_modules_do_not_import_outward_adapters(self) -> None:
        adapters = {
            "cli",
            "commands",
            "fdc",
            "media",
            "media_retention",
            "presentation",
            "profile_presentation",
            "profile_workflow",
            "reminder_workflow",
            "store",
            "workspace",
        }

        for module_name in (
            "analysis",
            "nutrition",
            "personal_profile",
            "reminder",
            "tracking",
            "tracking_summary",
            "summary",
        ):
            with self.subTest(module=module_name):
                self.assertFalse(local_imports(module_name) & adapters)

    def test_workspace_paths_have_distinct_typed_owners(self) -> None:
        paths = paths_for(date(2026, 1, 2), profile())

        self.assertIsInstance(paths, WorkspacePaths)
        self.assertEqual(paths.analysis.name, "analysis.json")
        self.assertEqual(paths.media_audit.name, "media-audit.json")
        self.assertEqual(paths.report_md.suffix, ".md")
        self.assertEqual(paths.report_html.suffix, ".html")
        self.assertNotEqual(paths.runtime_root, paths.site_root)

    def test_personal_profile_paths_are_scoped_to_the_active_owner(self) -> None:
        settings = profile()
        settings["active_profile_id"] = "researcher-1"

        paths = personal_profile_paths(settings)

        self.assertIsInstance(paths, PersonalProfilePaths)
        self.assertEqual(
            paths.profile_json.relative_to(PROJECT_ROOT).as_posix(),
            "data/profiles/researcher-1/profile.json",
        )
        self.assertEqual(paths.medical_index.name, "index.json")
        self.assertEqual(
            paths.site_html.relative_to(PROJECT_ROOT).as_posix(),
            "site/profile/index.html",
        )

    def test_rejects_overlapping_private_roots(self) -> None:
        with self.assertRaisesRegex(PipelineError, "互不包含"):
            paths_for(
                date(2026, 1, 2),
                profile(daily_records_directory="runtime/daily"),
            )

    def test_rejects_manifest_preview_outside_site_boundary(self) -> None:
        paths = paths_for(date(2026, 1, 2), profile())
        manifest = {"schema_version": 3}
        asset = {"preview_path": "runtime/not-a-site-asset.jpg"}

        with self.assertRaisesRegex(PipelineError, "越过目录边界"):
            manifest_preview_path(asset, manifest, paths)

    def test_rejects_media_purge_path_outside_daily_record(self) -> None:
        paths = paths_for(date(2026, 1, 2), profile())
        asset = {"file": "outside.jpg", "relative_path": "../../outside.jpg"}

        with self.assertRaisesRegex(PipelineError, "越过当日记录目录"):
            manifest_source_path(asset, paths)

    def test_rejects_media_purge_preview_outside_dated_assets(self) -> None:
        paths = paths_for(date(2026, 1, 2), profile())
        manifest = {"schema_version": 4}
        asset = {"file": "outside.jpg", "preview_path": "site/index.html"}

        with self.assertRaisesRegex(PipelineError, "当日预览目录之外"):
            retention_preview_path(asset, manifest, paths)

    def test_static_html_audit_rejects_external_loaded_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            page = Path(raw_directory) / "index.html"
            page.write_text(
                '<!doctype html><html><body><img src="https://example.test/private.jpg"></body></html>',
                encoding="utf-8",
            )

            errors, _, _ = audit_static_html(page)

        self.assertTrue(any("外部资源" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
