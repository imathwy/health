"""Application workflow for durable personal profiles and medical indexes."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .media import sha256_file
from .personal_profile import (
    active_profile_id,
    medical_index_template,
    personal_profile_template,
    settings_v2_from_legacy,
    validate_profile_bundle,
)
from .presentation import audit_static_html, update_dashboard
from .profile_presentation import render_personal_profile_html
from .workspace import (
    PersonalProfilePaths,
    SETTINGS_PATH,
    atomic_write_text,
    load_json,
    load_profile,
    load_settings,
    personal_profile_paths,
    relative_private_path,
    write_json,
)


def _load_bundle(
    settings: dict[str, Any],
) -> tuple[PersonalProfilePaths, dict[str, Any], dict[str, Any], list[str]]:
    paths = personal_profile_paths(settings)
    personal_profile = load_json(paths.profile_json)
    medical_index = load_json(paths.medical_index)
    errors, warnings = validate_profile_bundle(personal_profile, medical_index)
    if personal_profile.get("profile_id") != paths.profile_id:
        errors.append(f"个人档案 profile_id 与活动档案 {paths.profile_id} 不一致")
    if errors:
        raise PipelineError("个人档案未通过校验：\n- " + "\n- ".join(errors))

    records = medical_index.get("records", [])
    for record in records:
        if not isinstance(record, dict):
            continue
        for source in record.get("source_files", []):
            raw_path = (
                source
                if isinstance(source, str)
                else source.get("path")
                if isinstance(source, dict)
                else None
            )
            expected_digest = source.get("sha256") if isinstance(source, dict) else None
            if not isinstance(raw_path, str):
                continue
            candidate = (paths.medical_dir / raw_path).resolve()
            try:
                candidate.relative_to(paths.medical_files_dir.resolve())
            except ValueError:
                errors.append(
                    f"病历 {record.get('id', 'unknown')} 的原件越过 medical/files 边界："
                    f"{raw_path}"
                )
                continue
            if not candidate.is_file():
                warnings.append(
                    f"病历 {record.get('id', 'unknown')} 的原件缺失：{raw_path}"
                )
            elif (
                isinstance(expected_digest, str)
                and sha256_file(candidate) != expected_digest
            ):
                errors.append(
                    f"病历 {record.get('id', 'unknown')} 的原件 SHA-256 不一致："
                    f"{raw_path}"
                )
    if errors:
        raise PipelineError("个人档案未通过校验：\n- " + "\n- ".join(errors))
    return paths, personal_profile, medical_index, sorted(set(warnings))


def validate_personal_profile_sources(
    settings: dict[str, Any],
) -> tuple[PersonalProfilePaths, list[str]]:
    """Validate canonical documents and raw-record integrity without rendering."""

    paths, _, _, warnings = _load_bundle(settings)
    return paths, warnings


def _render_outputs(
    settings: dict[str, Any],
) -> tuple[PersonalProfilePaths, list[str]]:
    paths, personal_profile, medical_index, warnings = _load_bundle(settings)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    snapshot = {
        "schema_version": 1,
        "generated_at": generated_at,
        "profile": personal_profile,
        "medical_index": medical_index,
        "warnings": warnings,
    }
    write_json(paths.runtime_snapshot, snapshot)
    page = render_personal_profile_html(
        personal_profile,
        medical_index,
        generated_at=generated_at,
        profile_source=relative_private_path(paths.profile_json),
        medical_index_source=relative_private_path(paths.medical_index),
        warnings=warnings,
    )
    atomic_write_text(paths.site_html, page)
    html_errors, html_warnings, _ = audit_static_html(paths.site_html)
    if html_errors:
        raise PipelineError("个人档案 HTML 未通过校验：\n- " + "\n- ".join(html_errors))
    return paths, sorted(set(warnings + html_warnings))


def refresh_personal_profile_if_available(settings: dict[str, Any]) -> list[str]:
    """Refresh derived profile outputs when both canonical documents exist."""

    try:
        paths = personal_profile_paths(settings)
    except PipelineError:
        return []
    if not paths.profile_json.is_file() or not paths.medical_index.is_file():
        return []
    _, warnings = _render_outputs(settings)
    return warnings


def initialize_personal_profile(args: argparse.Namespace) -> int:
    """Create durable profile documents without overwriting user-owned facts."""

    settings = load_settings()
    try:
        profile_id = active_profile_id(settings)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc
    paths = personal_profile_paths(settings)
    paths.medical_files_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    if not paths.profile_json.exists():
        write_json(paths.profile_json, personal_profile_template(settings))
        created.append(paths.profile_json)
    if not paths.medical_index.exists():
        write_json(paths.medical_index, medical_index_template(profile_id))
        created.append(paths.medical_index)

    migrated = False
    backup_path: Path | None = None
    if args.migrate_config and settings.get("schema_version") == 1:
        backup_path = paths.migrations_dir / "health_profile-v1.json"
        if not backup_path.exists():
            write_json(backup_path, settings)
        write_json(SETTINGS_PATH, settings_v2_from_legacy(settings))
        settings = load_settings()
        migrated = True

    _, personal_profile, medical_index, warnings = _load_bundle(settings)
    if personal_profile.get("profile_id") != profile_id:
        raise PipelineError("初始化后的 profile_id 与活动档案不一致")
    if medical_index.get("profile_id") != profile_id:
        raise PipelineError("初始化后的病历索引 profile_id 与活动档案不一致")

    print(f"PROFILE_ID={profile_id}")
    print(f"PROFILE_JSON={paths.profile_json}")
    print(f"MEDICAL_INDEX={paths.medical_index}")
    print(f"MEDICAL_FILES_DIR={paths.medical_files_dir}")
    print(f"CREATED={len(created)}")
    print(f"CONFIG_MIGRATED={'yes' if migrated else 'no'}")
    if backup_path:
        print(f"LEGACY_CONFIG_BACKUP={backup_path}")
    for warning in warnings:
        print(f"WARNING={warning}")
    return 0


def personal_profile_command(_: argparse.Namespace) -> int:
    settings = load_settings()
    paths, warnings = _render_outputs(settings)
    profile = load_profile()
    dashboard_path = update_dashboard(profile)
    dashboard_errors, dashboard_warnings, _ = audit_static_html(dashboard_path)
    if dashboard_errors:
        raise PipelineError("健康门户未通过校验：\n- " + "\n- ".join(dashboard_errors))
    print(f"PROFILE_ID={paths.profile_id}")
    print(f"PROFILE_JSON={paths.profile_json}")
    print(f"MEDICAL_INDEX={paths.medical_index}")
    print(f"PROFILE_SNAPSHOT={paths.runtime_snapshot}")
    print(f"PROFILE_HTML={paths.site_html}")
    print(f"DASHBOARD={dashboard_path}")
    print("PROFILE_STATUS=ready")
    for warning in sorted(set(warnings + dashboard_warnings)):
        print(f"WARNING={warning}")
    return 0
