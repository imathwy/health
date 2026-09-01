"""Durable audit and bounded deletion of unrelated health-workspace media."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .media import (
    MANIFEST_SCHEMA_VERSION,
    MEDIA_EXTENSIONS,
    PREVIEW_VERSION,
    manifest_preview_path,
    sha256_file,
)
from .workspace import WorkspacePaths, load_json


MEDIA_AUDIT_SCHEMA_VERSION = 1
STORAGE_RETAINED = "retained"
STORAGE_PURGED_UNRELATED = "purged_unrelated"
PURGE_REASON = "analysis.classification=unrelated"
PURGE_SOURCE_SCOPE = "health_workspace_export_copy"


def media_audit_template(target: date) -> dict[str, Any]:
    """Return the durable metadata ledger for purged workspace copies."""

    return {
        "schema_version": MEDIA_AUDIT_SCHEMA_VERSION,
        "date": target.isoformat(),
        "policy": "purge_unrelated_workspace_copies",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purged_assets": [],
    }


def _valid_zoned_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def load_media_audit(path: Path, target: date) -> dict[str, Any]:
    """Load and validate the private audit without trusting stored paths."""

    if not path.is_file():
        return media_audit_template(target)
    document = load_json(path)
    errors: list[str] = []
    if document.get("schema_version") != MEDIA_AUDIT_SCHEMA_VERSION:
        errors.append("schema_version 必须为 1")
    if document.get("date") != target.isoformat():
        errors.append("date 与记录日期不一致")
    if document.get("policy") != "purge_unrelated_workspace_copies":
        errors.append("policy 无效")
    if not _valid_zoned_timestamp(document.get("updated_at")):
        errors.append("updated_at 必须是带时区的 ISO 8601 时间")
    rows = document.get("purged_assets")
    if not isinstance(rows, list):
        errors.append("purged_assets 必须是数组")
        rows = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        prefix = f"purged_assets[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        filename = row.get("file")
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
        ):
            errors.append(f"{prefix}.file 必须是安全的单一文件名")
        digest = row.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            errors.append(f"{prefix}.sha256 无效")
        if isinstance(filename, str) and isinstance(digest, str):
            key = (filename, digest)
            if key in seen:
                errors.append(f"{prefix} 与已有审计记录重复")
            seen.add(key)
        size = row.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append(f"{prefix}.size_bytes 无效")
        if row.get("media_type") not in {"image", "video"}:
            errors.append(f"{prefix}.media_type 无效")
        if row.get("extension") not in MEDIA_EXTENSIONS:
            errors.append(f"{prefix}.extension 无效")
        if row.get("reason") != PURGE_REASON:
            errors.append(f"{prefix}.reason 无效")
        if row.get("source_scope") != PURGE_SOURCE_SCOPE:
            errors.append(f"{prefix}.source_scope 无效")
        if not _valid_zoned_timestamp(row.get("recorded_at")):
            errors.append(f"{prefix}.recorded_at 无效")
    if errors:
        raise PipelineError("媒体清理审计无效：\n- " + "\n- ".join(errors))
    return document


def _active_audit_entries(
    media_audit: dict[str, Any], analysis: dict[str, Any] | None
) -> list[dict[str, Any]]:
    rows = [
        row for row in media_audit.get("purged_assets", []) if isinstance(row, dict)
    ]
    if analysis is None:
        return rows
    classifications = {
        row.get("file"): row.get("classification")
        for row in analysis.get("images", [])
        if isinstance(row, dict)
    }
    return [row for row in rows if classifications.get(row.get("file")) == "unrelated"]


def _latest_audit_by_file(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        previous = selected.get(row["file"])
        if previous is None or str(row["recorded_at"]) > str(previous["recorded_at"]):
            selected[row["file"]] = row
    return selected


def _audit_asset(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": row["file"],
        "relative_path": row["file"],
        "preview_path": None,
        "preview_error": None,
        "preview_converter": None,
        "preview_version": PREVIEW_VERSION,
        "media_type": row["media_type"],
        "extension": row["extension"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "modified_at": row.get("modified_at"),
        "capture_time": row.get("capture_time"),
        "paired_files": [],
        "storage_state": STORAGE_PURGED_UNRELATED,
        "purged_at": row["recorded_at"],
    }


def refresh_manifest_counts(manifest: dict[str, Any]) -> dict[str, Any]:
    assets = manifest.get("assets", [])
    manifest["asset_count"] = len(assets)
    manifest["retained_asset_count"] = sum(
        asset.get("storage_state", STORAGE_RETAINED) == STORAGE_RETAINED
        for asset in assets
    )
    manifest["purged_asset_count"] = sum(
        asset.get("storage_state") == STORAGE_PURGED_UNRELATED for asset in assets
    )
    manifest["preview_count"] = sum(bool(asset.get("preview_path")) for asset in assets)
    return manifest


def manifest_source_path(asset: dict[str, Any], paths: WorkspacePaths) -> Path:
    """Resolve one export copy without permitting a daily-root escape."""

    raw_path = asset.get("relative_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise PipelineError(f"媒体清单缺少安全相对路径：{asset.get('file')}")
    resolved = (paths.record_dir / raw_path).resolve()
    try:
        resolved.relative_to(paths.record_dir.resolve())
    except ValueError as exc:
        raise PipelineError(f"媒体路径越过当日记录目录：{raw_path}") from exc
    return resolved


def retention_preview_path(
    asset: dict[str, Any], manifest: dict[str, Any], paths: WorkspacePaths
) -> Path | None:
    """Resolve a derived preview and confine deletion to this day's assets."""

    preview = manifest_preview_path(asset, manifest, paths)
    if preview is None:
        return None
    try:
        preview.relative_to(paths.preview_dir.resolve())
    except ValueError as exc:
        raise PipelineError(
            f"拒绝清理当日预览目录之外的文件：{asset.get('preview_path')}"
        ) from exc
    return preview


def _verify_source_before_delete(asset: dict[str, Any], source: Path) -> None:
    if not source.exists():
        return
    if not source.is_file():
        raise PipelineError(f"待清理媒体不是普通文件：{source}")
    if source.stat().st_size != asset.get("size_bytes"):
        raise PipelineError(f"拒绝清理大小已变化的媒体：{source.name}")
    if sha256_file(source) != asset.get("sha256"):
        raise PipelineError(f"拒绝清理哈希已变化的媒体：{source.name}")


def _delete_workspace_copy(
    asset: dict[str, Any], source: Path, preview: Path | None
) -> tuple[int, int]:
    try:
        source_deleted = int(source.exists())
        if source_deleted:
            source.unlink()
        preview_deleted = int(preview is not None and preview.exists())
        if preview_deleted and preview is not None:
            preview.unlink()
    except OSError as exc:
        raise PipelineError(
            f"无法清理无关照片的工作区副本：{asset['file']}: {exc}"
        ) from exc
    return source_deleted, preview_deleted


def reconcile_known_unrelated_exports(
    paths: WorkspacePaths,
    manifest: dict[str, Any],
    media_audit: dict[str, Any],
    analysis: dict[str, Any] | None,
    previous_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Remove exact re-exports and restore active audit tombstones."""

    active_audit = _active_audit_entries(media_audit, analysis)
    purged_hashes = {row["sha256"] for row in active_audit}
    audit_by_file = _latest_audit_by_file(active_audit)
    previous_hashes = {
        asset.get("file"): asset.get("sha256")
        for asset in (previous_manifest or {}).get("assets", [])
        if isinstance(asset, dict)
    }
    retained_assets: list[dict[str, Any]] = []
    reexport_purged_count = 0
    for raw_asset in manifest.get("assets", []):
        asset = dict(raw_asset)
        if asset.get("sha256") in purged_hashes:
            source = manifest_source_path(asset, paths)
            preview = retention_preview_path(asset, manifest, paths)
            _verify_source_before_delete(asset, source)
            _delete_workspace_copy(asset, source, preview)
            reexport_purged_count += 1
            continue
        asset["storage_state"] = STORAGE_RETAINED
        old_hash = previous_hashes.get(asset.get("file"))
        audited_same_name = audit_by_file.get(str(asset.get("file")))
        if (old_hash and old_hash != asset.get("sha256")) or (
            audited_same_name is not None
            and audited_same_name["sha256"] != asset.get("sha256")
        ):
            asset["review_required"] = True
        retained_assets.append(asset)

    retained_names = {asset["file"] for asset in retained_assets}
    retained_assets.extend(
        _audit_asset(row)
        for filename, row in audit_by_file.items()
        if filename not in retained_names
    )
    retained_assets.sort(key=lambda asset: str(asset["file"]).lower())
    updated = dict(manifest)
    updated.update(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "known_unrelated_reexports_purged": reexport_purged_count,
            "assets": retained_assets,
        }
    )
    return refresh_manifest_counts(updated)


def _audit_row(asset: dict[str, Any], recorded_at: str) -> dict[str, Any]:
    return {
        "file": asset["file"],
        "sha256": asset["sha256"],
        "size_bytes": asset["size_bytes"],
        "media_type": asset["media_type"],
        "extension": asset["extension"],
        "modified_at": asset.get("modified_at"),
        "capture_time": asset.get("capture_time"),
        "recorded_at": recorded_at,
        "reason": PURGE_REASON,
        "source_scope": PURGE_SOURCE_SCOPE,
    }


def purge_unrelated_workspace_copies(
    paths: WorkspacePaths,
    manifest: dict[str, Any],
    analysis: dict[str, Any],
    media_audit: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    """Delete only reviewed unrelated exports and their derived previews."""

    image_rows = {
        row.get("file"): row
        for row in analysis.get("images", [])
        if isinstance(row, dict)
    }
    recorded_at = datetime.now().astimezone().isoformat(timespec="seconds")
    existing_rows = [
        row for row in media_audit.get("purged_assets", []) if isinstance(row, dict)
    ]
    existing_keys = {(row.get("file"), row.get("sha256")) for row in existing_rows}
    planned: dict[str, tuple[dict[str, Any], Path, Path | None]] = {}

    for asset in manifest.get("assets", []):
        image = image_rows.get(asset.get("file"))
        if not image or image.get("classification") != "unrelated":
            continue
        source = manifest_source_path(asset, paths)
        _verify_source_before_delete(asset, source)
        preview = retention_preview_path(asset, manifest, paths)
        planned[asset["file"]] = (asset, source, preview)
        key = (asset.get("file"), asset.get("sha256"))
        if key not in existing_keys:
            existing_rows.append(_audit_row(asset, recorded_at))
            existing_keys.add(key)

    existing_rows.sort(key=lambda row: (str(row["file"]).lower(), row["recorded_at"]))
    updated_audit = dict(media_audit)
    updated_audit.update(
        {
            "schema_version": MEDIA_AUDIT_SCHEMA_VERSION,
            "date": analysis["date"],
            "policy": "purge_unrelated_workspace_copies",
            "updated_at": recorded_at,
            "purged_assets": existing_rows,
        }
    )

    deleted_sources = 0
    deleted_previews = 0
    updated_assets: list[dict[str, Any]] = []
    audit_by_key = {(row.get("file"), row.get("sha256")): row for row in existing_rows}
    for asset in manifest.get("assets", []):
        plan = planned.get(asset.get("file"))
        if plan is None:
            updated_assets.append(dict(asset))
            continue
        _, source, preview = plan
        source_count, preview_count = _delete_workspace_copy(asset, source, preview)
        deleted_sources += source_count
        deleted_previews += preview_count
        audit_match = audit_by_key[(asset.get("file"), asset.get("sha256"))]
        updated_assets.append(_audit_asset(audit_match))

    updated_manifest = dict(manifest)
    updated_manifest.update(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "retention_updated_at": recorded_at,
            "assets": updated_assets,
        }
    )
    refresh_manifest_counts(updated_manifest)
    return (
        updated_manifest,
        updated_audit,
        {
            "deleted_sources": deleted_sources,
            "deleted_previews": deleted_previews,
            "purged_assets": updated_manifest["purged_asset_count"],
        },
    )
