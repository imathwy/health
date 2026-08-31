import tempfile
import unittest
from datetime import date
from pathlib import Path

from healthlog.errors import PipelineError
from healthlog.media import manifest_preview_path
from healthlog.presentation import audit_static_html
from healthlog.workspace import WorkspacePaths, paths_for


def profile(**pipeline_overrides: str) -> dict:
    pipeline = {
        "daily_records_directory": "data/daily",
        "runtime_directory": "runtime",
        "site_directory": "site",
    }
    pipeline.update(pipeline_overrides)
    return {"pipeline": pipeline}


class LayerBoundaryTests(unittest.TestCase):
    def test_workspace_paths_have_distinct_typed_owners(self) -> None:
        paths = paths_for(date(2026, 1, 2), profile())

        self.assertIsInstance(paths, WorkspacePaths)
        self.assertEqual(paths.analysis.name, "analysis.json")
        self.assertEqual(paths.report_md.suffix, ".md")
        self.assertEqual(paths.report_html.suffix, ".html")
        self.assertNotEqual(paths.runtime_root, paths.site_root)

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
