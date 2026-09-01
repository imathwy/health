"""Workspace configuration, private path boundaries, and atomic file I/O."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .personal_profile import (
    active_profile_id,
    runtime_profile_context,
    validate_profile_bundle,
)


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("HEALTHLOG_ROOT", str(DEFAULT_ROOT))).expanduser().resolve()
SETTINGS_PATH = ROOT / "config" / "health_profile.json"
REMINDER_SETTINGS_PATH = ROOT / "config" / "reminder.local.json"
PIPELINE_DIR_NAME = "pipeline"
MANIFEST_NAME = "manifest.json"
ANALYSIS_NAME = "analysis.json"
REPORT_MD_NAME = "README.md"
REPORT_HTML_NAME = "index.html"


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Typed locations for one dated workflow inside the private workspace."""

    records_root: Path
    record_dir: Path
    runtime_root: Path
    site_root: Path
    daily_runtime_root: Path
    daily_site_root: Path
    runtime_day_dir: Path
    site_day_dir: Path
    pipeline_dir: Path
    preview_dir: Path
    manifest: Path
    template: Path
    analysis: Path
    report_md: Path
    report_html: Path
    daily_index_md: Path
    daily_index_html: Path
    dashboard: Path


@dataclass(frozen=True, slots=True)
class PersonalProfilePaths:
    """Typed locations owned by one active local personal profile."""

    profile_id: str
    records_root: Path
    profile_dir: Path
    profile_json: Path
    medical_dir: Path
    medical_index: Path
    medical_files_dir: Path
    migrations_dir: Path
    runtime_dir: Path
    runtime_snapshot: Path
    site_dir: Path
    site_html: Path


def resolve_date(value: str) -> date:
    value = value.strip().lower()
    today = date.today()
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PipelineError("日期必须是 YYYY-MM-DD、today 或 yesterday") from exc
    if parsed.isoformat() != value:
        raise PipelineError("日期必须使用 YYYY-MM-DD 格式")
    return parsed


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"JSON 格式错误：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"JSON 顶层必须是对象：{path}")
    return value


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, payload)


def load_settings() -> dict[str, Any]:
    """Load operational settings without requiring the durable profile yet."""

    settings = load_json(SETTINGS_PATH)
    if settings.get("schema_version") not in {1, 2}:
        raise PipelineError(f"不支持的本地设置版本：{SETTINGS_PATH}")
    pipeline = settings.get("pipeline")
    if not isinstance(pipeline, dict):
        raise PipelineError(f"本地设置缺少 pipeline 对象：{SETTINGS_PATH}")
    legacy_keys = {
        "daily_directory",
        "database_path",
        "nutrition_reports_directory",
    }.intersection(pipeline)
    if legacy_keys:
        joined = ", ".join(sorted(legacy_keys))
        raise PipelineError(
            f"健康档案仍使用旧目录字段（{joined}）；"
            "请改为 daily_records_directory、runtime_directory 和 site_directory"
        )
    return settings


def load_profile() -> dict[str, Any]:
    """Load the analysis context, preferring the canonical durable profile."""

    settings = load_settings()
    try:
        paths = personal_profile_paths(settings)
    except PipelineError:
        if settings.get("schema_version") == 1:
            return settings
        raise
    if not paths.profile_json.is_file():
        if settings.get("schema_version") == 1:
            return settings
        raise PipelineError(
            f"缺少活动个人档案：{paths.profile_json}；请先运行 diet profile-init"
        )
    if not paths.medical_index.is_file():
        if settings.get("schema_version") == 1:
            return settings
        raise PipelineError(
            f"缺少活动档案的病历索引：{paths.medical_index}；请先运行 diet profile-init"
        )
    personal_profile = load_json(paths.profile_json)
    medical_index = load_json(paths.medical_index)
    errors, _ = validate_profile_bundle(personal_profile, medical_index)
    if errors:
        raise PipelineError("个人档案未通过校验：\n- " + "\n- ".join(errors))
    return runtime_profile_context(settings, personal_profile)


def configured_private_path(profile: dict[str, Any], key: str, default: str) -> Path:
    raw = str(profile.get("pipeline", {}).get(key, default))
    candidate = Path(raw).expanduser()
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    )
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise PipelineError(f"pipeline.{key} 必须位于仓库目录内：{resolved}") from exc
    return resolved


def runtime_root(profile: dict[str, Any]) -> Path:
    return configured_private_path(profile, "runtime_directory", "runtime")


def site_root(profile: dict[str, Any]) -> Path:
    return configured_private_path(profile, "site_directory", "site")


def profile_records_root(profile: dict[str, Any]) -> Path:
    return configured_private_path(
        profile, "profile_records_directory", "data/profiles"
    )


def database_path(profile: dict[str, Any]) -> Path:
    return runtime_root(profile) / "state" / "healthlog.sqlite3"


def nutrition_reports_dir(profile: dict[str, Any]) -> Path:
    return runtime_root(profile) / "reports" / "nutrition"


def nutrition_site_dir(profile: dict[str, Any]) -> Path:
    return site_root(profile) / "nutrition"


def relative_private_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _ensure_disjoint_roots(configured_roots: dict[str, Path]) -> None:
    root_items = list(configured_roots.items())
    for index, (left_name, left_path) in enumerate(root_items):
        for right_name, right_path in root_items[index + 1 :]:
            if (
                left_path == right_path
                or left_path in right_path.parents
                or right_path in left_path.parents
            ):
                raise PipelineError(f"{left_name} 与 {right_name} 必须是互不包含的目录")


def personal_profile_paths(profile: dict[str, Any]) -> PersonalProfilePaths:
    """Resolve one active owner's durable, runtime, and presentation paths."""

    try:
        profile_id = active_profile_id(profile)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc
    records_root = profile_records_root(profile)
    local_runtime_root = runtime_root(profile)
    local_site_root = site_root(profile)
    daily_records_root = configured_private_path(
        profile, "daily_records_directory", "data/daily"
    )
    _ensure_disjoint_roots(
        {
            "pipeline.profile_records_directory": records_root,
            "pipeline.daily_records_directory": daily_records_root,
            "pipeline.runtime_directory": local_runtime_root,
            "pipeline.site_directory": local_site_root,
        }
    )
    profile_dir = records_root / profile_id
    medical_dir = profile_dir / "medical"
    runtime_dir = local_runtime_root / "profile"
    site_dir = local_site_root / "profile"
    return PersonalProfilePaths(
        profile_id=profile_id,
        records_root=records_root,
        profile_dir=profile_dir,
        profile_json=profile_dir / "profile.json",
        medical_dir=medical_dir,
        medical_index=medical_dir / "index.json",
        medical_files_dir=medical_dir / "files",
        migrations_dir=profile_dir / "migrations",
        runtime_dir=runtime_dir,
        runtime_snapshot=runtime_dir / "profile.snapshot.json",
        site_dir=site_dir,
        site_html=site_dir / REPORT_HTML_NAME,
    )


def paths_for(target: date, profile: dict[str, Any]) -> WorkspacePaths:
    records_root = configured_private_path(
        profile, "daily_records_directory", "data/daily"
    )
    local_runtime_root = runtime_root(profile)
    local_site_root = site_root(profile)
    _ensure_disjoint_roots(
        {
            "pipeline.daily_records_directory": records_root,
            "pipeline.profile_records_directory": profile_records_root(profile),
            "pipeline.runtime_directory": local_runtime_root,
            "pipeline.site_directory": local_site_root,
        }
    )

    daily_runtime_root = local_runtime_root / "daily"
    daily_site_root = local_site_root / "daily"
    compact_date = target.strftime("%Y%m%d")
    record_dir = records_root / compact_date
    runtime_day_dir = daily_runtime_root / compact_date
    site_day_dir = daily_site_root / compact_date
    pipeline_dir = runtime_day_dir / PIPELINE_DIR_NAME
    return WorkspacePaths(
        records_root=records_root,
        record_dir=record_dir,
        runtime_root=local_runtime_root,
        site_root=local_site_root,
        daily_runtime_root=daily_runtime_root,
        daily_site_root=daily_site_root,
        runtime_day_dir=runtime_day_dir,
        site_day_dir=site_day_dir,
        pipeline_dir=pipeline_dir,
        preview_dir=site_day_dir / "assets",
        manifest=pipeline_dir / MANIFEST_NAME,
        template=pipeline_dir / "analysis.template.json",
        analysis=record_dir / ANALYSIS_NAME,
        report_md=runtime_day_dir / REPORT_MD_NAME,
        report_html=site_day_dir / REPORT_HTML_NAME,
        daily_index_md=daily_runtime_root / REPORT_MD_NAME,
        daily_index_html=daily_site_root / REPORT_HTML_NAME,
        dashboard=local_site_root / REPORT_HTML_NAME,
    )
