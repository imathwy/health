#!/usr/bin/env python3

"""Prepare, render, and verify the local Apple Photos diet-analysis pipeline."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from .fdc import (
    DEFAULT_DATA_TYPES,
    FDCError,
    analysis_item_candidate,
    food_details,
    normalize_food,
    normalize_search,
    search_foods,
)
from .store import CORE_NUTRIENTS, NutritionStore
from .summary import json_text as summary_json_text
from .summary import make_summary, render_html as render_summary_html
from .summary import render_markdown as render_summary_markdown


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("HEALTHLOG_ROOT", str(DEFAULT_ROOT))).expanduser().resolve()
PROFILE_PATH = ROOT / "config" / "health_profile.json"
PIPELINE_DIR_NAME = ".diet-pipeline"
MANIFEST_NAME = "manifest.json"
ANALYSIS_NAME = "analysis.json"
REPORT_MD_NAME = "README.md"
REPORT_HTML_NAME = "index.html"
PREVIEW_VERSION = 2

IMAGE_EXTENSIONS = {
    ".avif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
VIDEO_EXTENSIONS = {".m4v", ".mov", ".mp4"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

CLASSIFICATIONS = {"consumed_food", "possible_food", "unrelated", "unreviewed"}
FINAL_CLASSIFICATIONS = CLASSIFICATIONS - {"unreviewed"}
DAY_TYPES = {"unknown", "rest", "strength", "swim", "tennis", "mixed"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
NUTRIENT_KEYS = CORE_NUTRIENTS
ANALYSIS_SCHEMA_VERSIONS = {1, 2}
PORTION_METHODS = {
    "manual_weight",
    "manual_range",
    "manual_serving",
    "package_serving",
    "visual_estimate",
    "unknown",
}
NUTRITION_SOURCE_TYPES = {
    "package_label",
    "usda_fdc",
    "chinese_food_composition",
    "recipe_estimate",
    "manual",
    "unknown",
}


class PipelineError(RuntimeError):
    """A user-facing pipeline error."""


class StaticHTMLAudit(HTMLParser):
    """Collect enough structure to verify an offline report and its assets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_doctype = False
        self.image_count = 0
        self.references: list[tuple[str, str, str]] = []

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() == "doctype html":
            self.has_doctype = True

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if tag == "img":
            self.image_count += 1
        for key in ("src", "href"):
            value = values.get(key)
            if value:
                self.references.append((tag, key, value))


def audit_static_html(path: Path) -> tuple[list[str], list[str], StaticHTMLAudit]:
    errors: list[str] = []
    warnings: list[str] = []
    audit = StaticHTMLAudit()
    try:
        audit.feed(path.read_text(encoding="utf-8"))
        audit.close()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        errors.append(f"HTML 无法解析：{path}: {exc}")
        return errors, warnings, audit

    if not audit.has_doctype:
        errors.append(f"HTML 缺少 <!doctype html>：{path}")

    for tag, key, raw_value in audit.references:
        parsed = urlsplit(raw_value)
        if parsed.scheme in {"http", "https"} or raw_value.startswith("//"):
            if key == "src":
                errors.append(f"静态报告含外部资源：{path}: {raw_value}")
            continue
        if parsed.scheme in {"data", "mailto", "tel"} or raw_value.startswith("#"):
            continue
        if parsed.scheme:
            warnings.append(f"未检查的链接协议：{path}: {raw_value}")
            continue
        relative = unquote(parsed.path)
        if not relative:
            continue
        referenced = (path.parent / relative).resolve()
        if not referenced.exists():
            errors.append(
                f"HTML 本地引用缺失：{path.name} 的 {tag}[{key}]={raw_value}"
            )
    return errors, warnings, audit


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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, payload)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_profile() -> dict[str, Any]:
    profile = load_json(PROFILE_PATH)
    if profile.get("schema_version") != 1:
        raise PipelineError(f"不支持的健康档案版本：{PROFILE_PATH}")
    return profile


def configured_private_path(
    profile: dict[str, Any], key: str, default: str
) -> Path:
    raw = str(profile.get("pipeline", {}).get(key, default))
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (ROOT / candidate).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise PipelineError(
            f"pipeline.{key} 必须位于仓库目录内：{resolved}"
        ) from exc
    return resolved


def database_path(profile: dict[str, Any]) -> Path:
    return configured_private_path(
        profile, "database_path", "data/state/healthlog.sqlite3"
    )


def nutrition_reports_dir(profile: dict[str, Any]) -> Path:
    return configured_private_path(
        profile, "nutrition_reports_directory", "data/reports/nutrition"
    )


def relative_private_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def paths_for(target: date, profile: dict[str, Any]) -> dict[str, Path]:
    daily_root = configured_private_path(profile, "daily_directory", "data/daily")
    day_dir = daily_root / target.strftime("%Y%m%d")
    pipeline_dir = day_dir / PIPELINE_DIR_NAME
    return {
        "daily_root": daily_root,
        "day_dir": day_dir,
        "pipeline_dir": pipeline_dir,
        "preview_dir": pipeline_dir / "previews",
        "manifest": pipeline_dir / MANIFEST_NAME,
        "template": pipeline_dir / "analysis.template.json",
        "analysis": day_dir / ANALYSIS_NAME,
        "report_md": day_dir / REPORT_MD_NAME,
        "report_html": day_dir / REPORT_HTML_NAME,
    }


def run_shortcut(target: date, shortcut_name: str, timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["shortcuts", "run", shortcut_name],
            input=target.isoformat() + "\n",
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PipelineError("找不到 macOS shortcuts 命令") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"Shortcut 在 {timeout} 秒后仍未完成") from exc

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        detail = stderr or stdout or "没有错误详情"
        raise PipelineError(f"Shortcut 运行失败（{completed.returncode}）：{detail}")

    fields: dict[str, str] = {}
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if "=" in line:
            key, value = line.split("=", 1)
            if re.fullmatch(r"[A-Z_]+", key):
                fields[key] = value

    reported_date = fields.get("DATE")
    if reported_date and reported_date != target.isoformat():
        raise PipelineError(
            f"Shortcut 返回了错误日期：期望 {target.isoformat()}，实际 {reported_date}"
        )

    return {
        "name": shortcut_name,
        "command": f"printf '{target.isoformat()}\\n' | shortcuts run {json.dumps(shortcut_name, ensure_ascii=False)}",
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "fields": fields,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_time(path: Path) -> str | None:
    mdls = shutil.which("mdls")
    if mdls:
        completed = subprocess.run(
            [mdls, "-raw", "-name", "kMDItemContentCreationDate", str(path)],
            text=True,
            capture_output=True,
            check=False,
        )
        value = completed.stdout.strip()
        if completed.returncode == 0 and value not in {"", "(null)"}:
            return value
    return None


def media_files(day_dir: Path) -> list[Path]:
    if not day_dir.exists():
        return []
    return sorted(
        (
            path
            for path in day_dir.iterdir()
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )


def make_image_preview(source: Path, destination: Path) -> tuple[str | None, str | None]:
    magick = shutil.which("magick")
    sips = shutil.which("sips")
    if not magick and not sips:
        return "找不到 magick 或 sips", None

    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        destination.exists()
        and destination.stat().st_size > 0
        and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns
    ):
        return None, "cached"

    fd, raw_temporary = tempfile.mkstemp(suffix=".jpg", dir=destination.parent)
    os.close(fd)
    temporary = Path(raw_temporary)
    try:
        if magick:
            command = [
                magick,
                str(source),
                "-auto-orient",
                "-resize",
                "1800x1800>",
                "-quality",
                "82",
                str(temporary),
            ]
            converter = "imagemagick"
        else:
            command = [
                sips,
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                "82",
                "-Z",
                "1800",
                str(source),
                "--out",
                str(temporary),
            ]
            converter = "sips"
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        if completed.returncode != 0 or temporary.stat().st_size == 0:
            return (
                completed.stderr or completed.stdout or f"{converter} 转换失败"
            ).strip(), converter
        os.replace(temporary, destination)
        os.utime(destination, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
        return None, converter
    finally:
        temporary.unlink(missing_ok=True)


def make_video_preview(source: Path, destination: Path) -> tuple[str | None, str | None]:
    qlmanage = shutil.which("qlmanage")
    if not qlmanage:
        return "找不到 qlmanage", None
    if (
        destination.exists()
        and destination.stat().st_size > 0
        and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns
    ):
        return None, "cached"

    with tempfile.TemporaryDirectory(prefix="diet-video-preview-") as raw_dir:
        temp_dir = Path(raw_dir)
        completed = subprocess.run(
            [qlmanage, "-t", "-s", "1800", "-o", str(temp_dir), str(source)],
            text=True,
            capture_output=True,
            check=False,
        )
        candidates = sorted(temp_dir.glob("*.png")) + sorted(temp_dir.glob("*.jpg"))
        if completed.returncode != 0 or not candidates:
            return (
                completed.stderr or completed.stdout or "视频缩略图生成失败"
            ).strip(), "quicklook"
        error, image_converter = make_image_preview(candidates[0], destination)
        if error is None:
            os.utime(destination, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
        converter = f"quicklook+{image_converter}" if image_converter else "quicklook"
        return error, converter


def build_manifest(
    target: date,
    paths: dict[str, Path],
    shortcut_result: dict[str, Any],
) -> dict[str, Any]:
    day_dir = paths["day_dir"]
    preview_dir = paths["preview_dir"]
    preview_dir.mkdir(parents=True, exist_ok=True)

    files = media_files(day_dir)
    stems: dict[str, list[str]] = {}
    for path in files:
        stems.setdefault(path.stem.lower(), []).append(path.name)

    assets: list[dict[str, Any]] = []
    for source in files:
        digest = sha256_file(source)
        preview_name = f"{source.stem}-{digest[:10]}-v{PREVIEW_VERSION}.jpg"
        preview_path = preview_dir / preview_name
        if source.suffix.lower() in IMAGE_EXTENSIONS:
            preview_error, preview_converter = make_image_preview(source, preview_path)
            media_type = "image"
        else:
            preview_error, preview_converter = make_video_preview(source, preview_path)
            media_type = "video"

        stat = source.stat()
        assets.append(
            {
                "file": source.name,
                "relative_path": source.relative_to(day_dir).as_posix(),
                "preview_path": (
                    preview_path.relative_to(day_dir).as_posix()
                    if preview_error is None and preview_path.exists()
                    else None
                ),
                "preview_error": preview_error,
                "preview_converter": preview_converter,
                "preview_version": PREVIEW_VERSION,
                "media_type": media_type,
                "extension": source.suffix.lower(),
                "size_bytes": stat.st_size,
                "sha256": digest,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                "capture_time": capture_time(source),
                "paired_files": [
                    name for name in stems[source.stem.lower()] if name != source.name
                ],
            }
        )

    return {
        "schema_version": 1,
        "date": target.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "root": str(ROOT),
        "day_directory": str(day_dir),
        "shortcut": shortcut_result,
        "asset_count": len(assets),
        "preview_count": sum(bool(asset["preview_path"]) for asset in assets),
        "assets": assets,
    }


def analysis_template(target: date, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "date": target.isoformat(),
        "profile": PROFILE_PATH.relative_to(ROOT).as_posix(),
        "day_context": {
            "day_type": "unknown",
            "training_notes": "",
            "photo_coverage": "unknown",
            "notes": [],
        },
        "images": [
            {
                "file": asset["file"],
                "classification": "unreviewed",
                "meal_id": None,
                "observations": [],
                "uncertainties": [],
            }
            for asset in manifest.get("assets", [])
        ],
        "meals": [],
        "assessment": {
            "summary": [],
            "strengths": [],
            "gaps": [],
            "next_actions": [],
            "supplement_note": "",
        },
        "assumptions": [],
        "overall_confidence": "low",
    }


def prepare(args: argparse.Namespace) -> int:
    profile = load_profile()
    target = resolve_date(args.date)
    paths = paths_for(target, profile)
    paths["day_dir"].mkdir(parents=True, exist_ok=True)

    shortcut_name = args.shortcut or profile["pipeline"]["shortcut_name"]
    if args.skip_export:
        shortcut_result = {
            "name": shortcut_name,
            "skipped": True,
            "reason": "--skip-export",
            "stdout": "",
            "stderr": "",
            "fields": {},
        }
    else:
        shortcut_result = run_shortcut(target, shortcut_name, args.timeout)

    manifest = build_manifest(target, paths, shortcut_result)
    write_json(paths["manifest"], manifest)
    template = analysis_template(target, manifest)
    write_json(paths["template"], template)

    if args.reset_analysis or not paths["analysis"].exists():
        write_json(paths["analysis"], template)
        analysis_state = "created"
    else:
        analysis_state = "preserved"
        existing = load_json(paths["analysis"])
        manifest_files = {asset["file"] for asset in manifest["assets"]}
        analysis_files = {
            row.get("file")
            for row in existing.get("images", [])
            if isinstance(row, dict)
        }
        if manifest_files != analysis_files:
            analysis_state = "preserved-needs-sync"

    print(f"DATE={target.isoformat()}")
    print(f"DAY_DIR={paths['day_dir']}")
    print(f"ASSETS={manifest['asset_count']}")
    print(f"PREVIEWS={manifest['preview_count']}")
    print(f"MANIFEST={paths['manifest']}")
    print(f"ANALYSIS={paths['analysis']}")
    print(f"ANALYSIS_STATE={analysis_state}")
    if manifest["preview_count"] != manifest["asset_count"]:
        print("WARNING=部分媒体没有预览；查看 manifest.json 的 preview_error")
    return 0


def ensure_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_range(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != 2:
        errors.append(f"{label} 必须是 [最小值, 最大值]")
        return
    if not all(ensure_number(item) for item in value):
        errors.append(f"{label} 必须包含两个数字")
        return
    if value[0] < 0 or value[1] < 0 or value[0] > value[1]:
        errors.append(f"{label} 范围无效：{value}")


def validate_analysis(
    analysis: dict[str, Any], manifest: dict[str, Any]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    schema_version = analysis.get("schema_version")
    if schema_version not in ANALYSIS_SCHEMA_VERSIONS:
        errors.append(
            "analysis.json schema_version 必须是 "
            + " 或 ".join(str(value) for value in sorted(ANALYSIS_SCHEMA_VERSIONS))
        )
    elif schema_version == 1:
        warnings.append("analysis.json 仍是 schema v1，食物来源元数据不完整")
    if analysis.get("date") != manifest.get("date"):
        errors.append("analysis.json 与 manifest.json 的日期不一致")

    day_context = analysis.get("day_context")
    if not isinstance(day_context, dict):
        errors.append("day_context 必须是对象")
        day_context = {}
    day_type = day_context.get("day_type")
    if day_type not in DAY_TYPES:
        errors.append(f"day_context.day_type 无效：{day_type!r}")

    rows = analysis.get("images")
    if not isinstance(rows, list):
        errors.append("images 必须是数组")
        rows = []
    image_records: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"images[{index}] 必须是对象")
            continue
        filename = row.get("file")
        if not isinstance(filename, str) or not filename:
            errors.append(f"images[{index}].file 缺失")
            continue
        if filename in image_records:
            errors.append(f"图片记录重复：{filename}")
        image_records[filename] = row
        classification = row.get("classification")
        if classification not in CLASSIFICATIONS:
            errors.append(f"{filename} 的 classification 无效：{classification!r}")
        elif classification == "unreviewed":
            errors.append(f"{filename} 尚未检查")
        for key in ("observations", "uncertainties"):
            if not isinstance(row.get(key), list):
                errors.append(f"{filename}.{key} 必须是数组")

    manifest_files = {asset["file"] for asset in manifest.get("assets", [])}
    analysis_files = set(image_records)
    missing = sorted(manifest_files - analysis_files)
    extra = sorted(analysis_files - manifest_files)
    if missing:
        errors.append(f"analysis.json 缺少图片记录：{', '.join(missing)}")
    if extra:
        errors.append(f"analysis.json 含不存在的图片记录：{', '.join(extra)}")

    meals = analysis.get("meals")
    if not isinstance(meals, list):
        errors.append("meals 必须是数组")
        meals = []
    meal_records: dict[str, dict[str, Any]] = {}
    for meal_index, meal in enumerate(meals):
        if not isinstance(meal, dict):
            errors.append(f"meals[{meal_index}] 必须是对象")
            continue
        meal_id = meal.get("id")
        if not isinstance(meal_id, str) or not meal_id:
            errors.append(f"meals[{meal_index}].id 缺失")
            continue
        if meal_id in meal_records:
            errors.append(f"餐次 id 重复：{meal_id}")
        meal_records[meal_id] = meal
        if not isinstance(meal.get("label"), str) or not meal.get("label"):
            errors.append(f"餐次 {meal_id} 缺少 label")
        meal_images = meal.get("images")
        if not isinstance(meal_images, list):
            errors.append(f"餐次 {meal_id}.images 必须是数组")
            meal_images = []
        for filename in meal_images:
            if filename not in manifest_files:
                errors.append(f"餐次 {meal_id} 引用了不存在的图片：{filename}")
        items = meal.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"餐次 {meal_id} 至少需要一个食物条目")
            continue
        for item_index, item in enumerate(items):
            prefix = f"餐次 {meal_id}.items[{item_index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix} 必须是对象")
                continue
            if not isinstance(item.get("name"), str) or not item.get("name"):
                errors.append(f"{prefix}.name 缺失")
            if not isinstance(item.get("portion"), str) or not item.get("portion"):
                errors.append(f"{prefix}.portion 缺失")
            nutrition = item.get("nutrition")
            if not isinstance(nutrition, dict):
                errors.append(f"{prefix}.nutrition 必须是对象")
                continue
            for nutrient in NUTRIENT_KEYS:
                validate_range(
                    nutrition.get(nutrient), f"{prefix}.nutrition.{nutrient}", errors
                )
            optional_nutrients = item.get("optional_nutrients", {})
            if not isinstance(optional_nutrients, dict):
                errors.append(f"{prefix}.optional_nutrients 必须是对象")
            else:
                for nutrient, value in optional_nutrients.items():
                    if not isinstance(nutrient, str) or not nutrient:
                        errors.append(f"{prefix}.optional_nutrients 含无效名称")
                        continue
                    if nutrient in NUTRIENT_KEYS:
                        errors.append(
                            f"{prefix}.optional_nutrients.{nutrient} 与核心营养素重复"
                        )
                        continue
                    validate_range(
                        value, f"{prefix}.optional_nutrients.{nutrient}", errors
                    )
            confidence = item.get("confidence")
            if confidence not in CONFIDENCE_LEVELS:
                errors.append(f"{prefix}.confidence 无效：{confidence!r}")
            evidence = item.get("evidence")
            if schema_version == 2 and not isinstance(evidence, dict):
                errors.append(f"{prefix}.evidence 必须是对象")
            if isinstance(evidence, dict):
                portion_method = evidence.get("portion_method")
                if portion_method not in PORTION_METHODS:
                    errors.append(
                        f"{prefix}.evidence.portion_method 无效：{portion_method!r}"
                    )
                nutrition_source = evidence.get("nutrition_source")
                if nutrition_source not in NUTRITION_SOURCE_TYPES:
                    errors.append(
                        f"{prefix}.evidence.nutrition_source 无效：{nutrition_source!r}"
                    )
                references = evidence.get("references", [])
                if not isinstance(references, list):
                    errors.append(f"{prefix}.evidence.references 必须是数组")
                else:
                    for reference_index, reference in enumerate(references):
                        if not isinstance(reference, dict):
                            errors.append(
                                f"{prefix}.evidence.references[{reference_index}] 必须是对象"
                            )
                            continue
                        if not isinstance(reference.get("provider"), str) or not reference.get("provider"):
                            errors.append(
                                f"{prefix}.evidence.references[{reference_index}].provider 缺失"
                            )
                if not isinstance(evidence.get("notes", []), list):
                    errors.append(f"{prefix}.evidence.notes 必须是数组")

    for filename, row in image_records.items():
        classification = row.get("classification")
        meal_id = row.get("meal_id")
        if classification == "consumed_food":
            if meal_id not in meal_records:
                errors.append(f"{filename} 标记为 consumed_food，但 meal_id 无效")
            elif filename not in meal_records[meal_id].get("images", []):
                errors.append(f"{filename} 未列入餐次 {meal_id} 的 images")
        elif meal_id not in {None, ""} and meal_id not in meal_records:
            errors.append(f"{filename} 的 meal_id 无效：{meal_id}")

    assessment = analysis.get("assessment")
    if not isinstance(assessment, dict):
        errors.append("assessment 必须是对象")
    else:
        for key in ("summary", "strengths", "gaps", "next_actions"):
            if not isinstance(assessment.get(key), list):
                errors.append(f"assessment.{key} 必须是数组")
        if not isinstance(assessment.get("supplement_note"), str):
            errors.append("assessment.supplement_note 必须是字符串")

    if not isinstance(analysis.get("assumptions"), list):
        errors.append("assumptions 必须是数组")
    if analysis.get("overall_confidence") not in CONFIDENCE_LEVELS:
        errors.append("overall_confidence 必须是 low、medium 或 high")

    possible = [
        filename
        for filename, row in image_records.items()
        if row.get("classification") == "possible_food"
    ]
    if possible:
        warnings.append(f"有 {len(possible)} 张图片只能确定为可能摄入")
    if day_type == "unknown":
        warnings.append("当天训练类型未知，热量和碳水使用默认目标")
    if day_context.get("photo_coverage") in {None, "", "unknown", "partial"}:
        warnings.append("照片覆盖可能不完整，不能把估算视为完整饮食日志")
    return errors, warnings


def sum_nutrition(analysis: dict[str, Any]) -> dict[str, list[float]]:
    totals = {key: [0.0, 0.0] for key in NUTRIENT_KEYS}
    for meal in analysis.get("meals", []):
        for item in meal.get("items", []):
            nutrition = item.get("nutrition", {})
            for key in NUTRIENT_KEYS:
                value = nutrition.get(key, [0, 0])
                totals[key][0] += float(value[0])
                totals[key][1] += float(value[1])
    return totals


def clean_number(value: float) -> int | float:
    rounded = round(value, 1)
    return int(rounded) if rounded.is_integer() else rounded


def display_range(value: Iterable[float], unit: str) -> str:
    low, high = (clean_number(float(number)) for number in value)
    if low == high:
        return f"{low} {unit}"
    return f"{low}–{high} {unit}"


def comparison_rows(
    totals: dict[str, list[float]], profile: dict[str, Any], day_type: str
) -> list[dict[str, str]]:
    targets = profile["targets"]
    energy = targets["energy_kcal"].get(day_type, targets["energy_kcal"]["unknown"])
    carbohydrate = targets["carbohydrate_g"].get(
        day_type, targets["carbohydrate_g"]["unknown"]
    )
    definitions = [
        ("热量", "kcal", "kcal", energy, None),
        ("蛋白质", "protein_g", "g", targets["protein_g"], None),
        ("碳水化合物", "carbohydrate_g", "g", carbohydrate, None),
        ("脂肪", "fat_g", "g", targets["fat_g"], None),
        ("膳食纤维", "fiber_g", "g", targets["fiber_g"], None),
        ("钠", "sodium_mg", "mg", None, targets["sodium_mg_max"]),
    ]
    rows: list[dict[str, str]] = []
    for label, key, unit, target_range, target_max in definitions:
        estimate = totals[key]
        if target_range is not None:
            target_text = display_range(target_range, unit)
            if estimate[1] < target_range[0]:
                status = "偏低"
            elif estimate[0] > target_range[1]:
                status = "偏高"
            elif estimate[0] >= target_range[0] and estimate[1] <= target_range[1]:
                status = "目标内"
            else:
                status = "区间重叠"
        else:
            target_text = f"≤{target_max} {unit}"
            if estimate[0] > target_max:
                status = "偏高"
            elif estimate[1] <= target_max:
                status = "目标内"
            else:
                status = "可能偏高"
        rows.append(
            {
                "label": label,
                "estimate": display_range(estimate, unit),
                "target": target_text,
                "status": status,
            }
        )
    return rows


def md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def bullet_lines(items: Any, fallback: str = "暂无") -> str:
    if not isinstance(items, list) or not items:
        return f"- {fallback}"
    return "\n".join(f"- {item}" for item in items)


def evidence_label(item: dict[str, Any]) -> str:
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return "旧版/未记录"
    source_labels = {
        "package_label": "包装标签",
        "usda_fdc": "USDA FDC",
        "chinese_food_composition": "中国食物成分资料",
        "recipe_estimate": "配方/常见做法估算",
        "manual": "人工录入",
        "unknown": "未明确",
    }
    portion_labels = {
        "manual_weight": "称重",
        "manual_range": "手工范围",
        "manual_serving": "用户份量",
        "package_serving": "包装份量",
        "visual_estimate": "照片估份",
        "unknown": "份量未知",
    }
    nutrition_source = source_labels.get(
        str(evidence.get("nutrition_source")), str(evidence.get("nutrition_source", "未明确"))
    )
    portion_method = portion_labels.get(
        str(evidence.get("portion_method")), str(evidence.get("portion_method", "份量未知"))
    )
    references = evidence.get("references", [])
    reference_ids = []
    if isinstance(references, list):
        for reference in references:
            if not isinstance(reference, dict):
                continue
            provider = reference.get("provider")
            identifier = reference.get("id")
            if provider and identifier:
                reference_ids.append(f"{provider} {identifier}")
    suffix = f"（{', '.join(reference_ids)}）" if reference_ids else ""
    return f"{nutrition_source} + {portion_method}{suffix}"


def render_markdown(
    target: date,
    analysis: dict[str, Any],
    manifest: dict[str, Any],
    profile: dict[str, Any],
    totals: dict[str, list[float]],
    comparisons: list[dict[str, str]],
) -> str:
    day_context = analysis["day_context"]
    assessment = analysis["assessment"]
    image_by_file = {row["file"]: row for row in analysis["images"]}
    preview_by_file = {
        asset["file"]: asset.get("preview_path") for asset in manifest["assets"]
    }

    lines = [
        f"# {target.isoformat()} 饮食分析",
        "",
        "> 由当天照片估算。照片不一定覆盖全部饮食，也不代表拍到的食物全部吃完；数值使用区间表达不确定性。",
        "",
        "## 当天背景",
        "",
        "| 项目 | 记录 |",
        "|---|---|",
        f"| 日型 | {md_escape(day_context.get('day_type', 'unknown'))} |",
        f"| 训练 | {md_escape(day_context.get('training_notes') or '未记录')} |",
        f"| 照片覆盖 | {md_escape(day_context.get('photo_coverage') or 'unknown')} |",
        f"| 总体置信度 | {md_escape(analysis.get('overall_confidence'))} |",
        "",
        "## 营养估算与目标",
        "",
        "| 营养素 | 照片估算 | 个人目标 | 判断 |",
        "|---|---:|---:|---|",
    ]
    lines.extend(
        f"| {row['label']} | {row['estimate']} | {row['target']} | {row['status']} |"
        for row in comparisons
    )

    lines.extend(["", "## 逐餐记录", ""])
    for meal in analysis["meals"]:
        meal_time = meal.get("time") or "时间不确定"
        lines.extend(
            [
                f"### {meal['label']}（{meal_time}）",
                "",
                f"关联图片：{', '.join(f'`{name}`' for name in meal.get('images', [])) or '无'}",
                "",
                "| 食物 | 估计份量 | 热量 | 蛋白质 | 碳水 | 脂肪 | 纤维 | 钠 | 证据 | 置信度 |",
                "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        for item in meal["items"]:
            nutrition = item["nutrition"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        md_escape(item["name"]),
                        md_escape(item["portion"]),
                        display_range(nutrition["kcal"], "kcal"),
                        display_range(nutrition["protein_g"], "g"),
                        display_range(nutrition["carbohydrate_g"], "g"),
                        display_range(nutrition["fat_g"], "g"),
                        display_range(nutrition["fiber_g"], "g"),
                        display_range(nutrition["sodium_mg"], "mg"),
                        md_escape(evidence_label(item)),
                        md_escape(item["confidence"]),
                    ]
                )
                + " |"
            )
        notes = meal.get("notes", [])
        if notes:
            lines.extend(["", "餐次备注：", bullet_lines(notes)])
        lines.append("")

    lines.extend(
        [
            "## 图片核对",
            "",
            "| 原文件 | 预览 | 分类 | 餐次 | 可见事实 | 不确定性 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for asset in manifest["assets"]:
        filename = asset["file"]
        row = image_by_file[filename]
        preview = preview_by_file.get(filename)
        preview_link = f"[查看]({preview})" if preview else "无预览"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{md_escape(filename)}`",
                    preview_link,
                    md_escape(row["classification"]),
                    md_escape(row.get("meal_id") or "—"),
                    md_escape("；".join(row.get("observations", [])) or "—"),
                    md_escape("；".join(row.get("uncertainties", [])) or "—"),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 评价与下一步",
            "",
            "### 总结",
            "",
            bullet_lines(assessment.get("summary")),
            "",
            "### 做得好的地方",
            "",
            bullet_lines(assessment.get("strengths")),
            "",
            "### 主要缺口",
            "",
            bullet_lines(assessment.get("gaps")),
            "",
            "### 下一次怎么吃",
            "",
            bullet_lines(assessment.get("next_actions")),
            "",
            "### 补剂说明",
            "",
            assessment.get("supplement_note") or "不根据单日照片新增补剂。",
            "",
            "## 假设与限制",
            "",
            bullet_lines(analysis.get("assumptions")),
            "",
            "## 流水线记录",
            "",
            f"- Shortcut：`{manifest.get('shortcut', {}).get('name', '未记录')}`",
            f"- 清单：[`{PIPELINE_DIR_NAME}/{MANIFEST_NAME}`](./{PIPELINE_DIR_NAME}/{MANIFEST_NAME})",
            f"- 结构化分析：[`{ANALYSIS_NAME}`](./{ANALYSIS_NAME})",
            f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
            "",
            "本报告用于个人饮食记录，不替代医疗诊断或个体化营养处方。",
            "",
        ]
    )
    return "\n".join(lines)


def html_list(items: Any, empty: str = "暂无") -> str:
    if not isinstance(items, list) or not items:
        return f"<p class=\"muted\">{html.escape(empty)}</p>"
    return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"


def status_class(status: str) -> str:
    if status == "目标内":
        return "ok"
    if status in {"偏高", "偏低"}:
        return "warn"
    return "uncertain"


def render_html(
    target: date,
    analysis: dict[str, Any],
    manifest: dict[str, Any],
    totals: dict[str, list[float]],
    comparisons: list[dict[str, str]],
) -> str:
    assessment = analysis["assessment"]
    image_rows = {row["file"]: row for row in analysis["images"]}
    comparison_html = "".join(
        "<tr>"
        f"<td>{html.escape(row['label'])}</td>"
        f"<td>{html.escape(row['estimate'])}</td>"
        f"<td>{html.escape(row['target'])}</td>"
        f"<td><span class=\"pill {status_class(row['status'])}\">{html.escape(row['status'])}</span></td>"
        "</tr>"
        for row in comparisons
    )

    meal_sections: list[str] = []
    for meal in analysis["meals"]:
        item_rows = []
        for item in meal["items"]:
            nutrition = item["nutrition"]
            item_rows.append(
                "<tr>"
                f"<td><strong>{html.escape(item['name'])}</strong><br><span class=\"muted\">{html.escape(item['portion'])}</span></td>"
                f"<td>{html.escape(display_range(nutrition['kcal'], 'kcal'))}</td>"
                f"<td>{html.escape(display_range(nutrition['protein_g'], 'g'))}</td>"
                f"<td>{html.escape(display_range(nutrition['carbohydrate_g'], 'g'))}</td>"
                f"<td>{html.escape(display_range(nutrition['fat_g'], 'g'))}</td>"
                f"<td>{html.escape(evidence_label(item))}</td>"
                f"<td>{html.escape(item['confidence'])}</td>"
                "</tr>"
            )
        meal_sections.append(
            "<section class=\"panel\">"
            f"<div class=\"section-head\"><div><p class=\"eyebrow\">{html.escape(meal.get('time') or '时间不确定')}</p><h2>{html.escape(meal['label'])}</h2></div>"
            f"<span class=\"pill neutral\">{len(meal.get('images', []))} 张图片</span></div>"
            "<div class=\"table-wrap\"><table><thead><tr><th>食物与份量</th><th>热量</th><th>蛋白质</th><th>碳水</th><th>脂肪</th><th>证据</th><th>置信度</th></tr></thead>"
            f"<tbody>{''.join(item_rows)}</tbody></table></div>"
            f"{html_list(meal.get('notes', []), '无额外备注')}"
            "</section>"
        )

    gallery_cards: list[str] = []
    for asset in manifest["assets"]:
        record = image_rows[asset["file"]]
        preview = asset.get("preview_path")
        if preview:
            media = f"<img src=\"{html.escape(preview, quote=True)}\" alt=\"{html.escape(asset['file'], quote=True)}\" loading=\"lazy\">"
        else:
            media = "<div class=\"no-preview\">无预览</div>"
        observations = "；".join(record.get("observations", [])) or "未记录可见事实"
        uncertainties = "；".join(record.get("uncertainties", [])) or "未记录"
        gallery_cards.append(
            "<article class=\"photo-card\">"
            f"{media}<div class=\"photo-body\"><div class=\"photo-meta\"><code>{html.escape(asset['file'])}</code>"
            f"<span class=\"pill neutral\">{html.escape(record['classification'])}</span></div>"
            f"<p>{html.escape(observations)}</p><p class=\"muted\">不确定性：{html.escape(uncertainties)}</p></div></article>"
        )

    day_context = analysis["day_context"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{target.isoformat()} 饮食分析</title>
  <style>
    :root {{ color-scheme: light; --ink:#18211b; --muted:#667169; --paper:#f4f2eb; --panel:#fffdf8; --line:#dfe4dc; --green:#1f6b4f; --green-soft:#e5f1ea; --amber:#946200; --amber-soft:#fff2cc; --blue:#315c7d; --blue-soft:#e8f0f6; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--paper); color:var(--ink); font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",sans-serif; }}
    main {{ width:min(1120px,calc(100% - 32px)); margin:0 auto; padding:42px 0 72px; }}
    .hero {{ padding:38px; border:1px solid var(--line); border-radius:28px; background:linear-gradient(135deg,#173f31,#2a7256); color:white; box-shadow:0 18px 50px rgba(25,49,38,.14); }}
    .hero h1 {{ margin:4px 0 8px; font-size:clamp(32px,7vw,64px); line-height:1.05; letter-spacing:-.04em; }}
    .hero p {{ max-width:760px; margin:0; color:#dcebe3; }}
    .eyebrow {{ margin:0; text-transform:uppercase; letter-spacing:.13em; font-size:12px; font-weight:750; opacity:.78; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:18px 0; }}
    .metric,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:20px; }}
    .metric {{ padding:18px; }} .metric b {{ display:block; font-size:24px; line-height:1.25; }} .metric span,.muted {{ color:var(--muted); }}
    .panel {{ padding:24px; margin:18px 0; }}
    .section-head {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:14px; }}
    h2 {{ margin:0; font-size:24px; letter-spacing:-.02em; }} h3 {{ margin:0 0 8px; }}
    .table-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; min-width:620px; }} th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }} th {{ color:var(--muted); font-size:13px; }}
    .pill {{ display:inline-flex; align-items:center; white-space:nowrap; padding:4px 9px; border-radius:999px; font-size:12px; font-weight:700; }}
    .pill.ok {{ background:var(--green-soft); color:var(--green); }} .pill.warn {{ background:var(--amber-soft); color:var(--amber); }} .pill.uncertain,.pill.neutral {{ background:var(--blue-soft); color:var(--blue); }}
    .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
    .gallery {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
    .photo-card {{ overflow:hidden; background:var(--panel); border:1px solid var(--line); border-radius:18px; }} .photo-card img,.no-preview {{ display:block; width:100%; aspect-ratio:4/3; object-fit:cover; background:#e7e8e3; }} .no-preview {{ display:grid; place-items:center; color:var(--muted); }} .photo-body {{ padding:14px; }} .photo-body p {{ margin:8px 0 0; }} .photo-meta {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }} code {{ font-size:12px; overflow-wrap:anywhere; }}
    .notice {{ margin:18px 0; padding:14px 18px; border-left:4px solid var(--amber); background:var(--amber-soft); border-radius:8px 16px 16px 8px; }}
    footer {{ color:var(--muted); text-align:center; padding-top:20px; font-size:13px; }}
    @media (max-width:850px) {{ .metrics,.gallery {{ grid-template-columns:repeat(2,1fr); }} .grid-2 {{ grid-template-columns:1fr; }} }}
    @media (max-width:560px) {{ main {{ width:min(100% - 20px,1120px); padding-top:10px; }} .hero {{ padding:26px 20px; border-radius:20px; }} .metrics,.gallery {{ grid-template-columns:1fr; }} .panel {{ padding:18px; }} }}
  </style>
</head>
<body>
<main>
  <header class="hero">
    <p class="eyebrow">Daily Diet Review · {html.escape(day_context.get('day_type', 'unknown'))}</p>
    <h1>{target.isoformat()}</h1>
    <p>根据当天照片进行区间估算。照片不一定覆盖全部摄入，拍到的食物也不自动视为全部吃完。</p>
  </header>
  <section class="metrics">
    <div class="metric"><span>热量</span><b>{html.escape(display_range(totals['kcal'], 'kcal'))}</b></div>
    <div class="metric"><span>蛋白质</span><b>{html.escape(display_range(totals['protein_g'], 'g'))}</b></div>
    <div class="metric"><span>碳水</span><b>{html.escape(display_range(totals['carbohydrate_g'], 'g'))}</b></div>
    <div class="metric"><span>脂肪</span><b>{html.escape(display_range(totals['fat_g'], 'g'))}</b></div>
  </section>
  <div class="notice">照片覆盖：{html.escape(str(day_context.get('photo_coverage', 'unknown')))}；总体置信度：{html.escape(str(analysis.get('overall_confidence')))}。请优先看区间和方向，不要把中点当成精确值。</div>
  <section class="panel">
    <div class="section-head"><div><p class="eyebrow">Targets</p><h2>营养估算与个人目标</h2></div></div>
    <div class="table-wrap"><table><thead><tr><th>营养素</th><th>照片估算</th><th>个人目标</th><th>判断</th></tr></thead><tbody>{comparison_html}</tbody></table></div>
  </section>
  {''.join(meal_sections)}
  <section class="grid-2">
    <div class="panel"><p class="eyebrow">Assessment</p><h2>主要发现</h2>{html_list(assessment.get('summary'))}<h3>做得好的地方</h3>{html_list(assessment.get('strengths'))}<h3>主要缺口</h3>{html_list(assessment.get('gaps'))}</div>
    <div class="panel"><p class="eyebrow">Next actions</p><h2>下一次怎么吃</h2>{html_list(assessment.get('next_actions'))}<h3>补剂说明</h3><p>{html.escape(assessment.get('supplement_note') or '不根据单日照片新增补剂。')}</p></div>
  </section>
  <section class="panel"><p class="eyebrow">Evidence</p><h2>图片核对</h2><div class="gallery">{''.join(gallery_cards)}</div></section>
  <section class="panel"><p class="eyebrow">Limits</p><h2>假设与限制</h2>{html_list(analysis.get('assumptions'))}</section>
  <footer>生成于 {datetime.now().astimezone().isoformat(timespec='seconds')} · 本报告用于个人饮食记录，不替代医疗诊断或营养处方。</footer>
</main>
</body>
</html>
"""


def rendered_entry(day_dir: Path) -> dict[str, Any] | None:
    analysis_path = day_dir / ANALYSIS_NAME
    if not analysis_path.exists() or not (day_dir / REPORT_HTML_NAME).exists():
        return None
    try:
        analysis = load_json(analysis_path)
        totals = sum_nutrition(analysis)
    except (PipelineError, KeyError, TypeError, ValueError):
        return None
    summary = analysis.get("assessment", {}).get("summary", [])
    return {
        "date": analysis.get("date", day_dir.name),
        "dir": day_dir.name,
        "kcal": display_range(totals["kcal"], "kcal"),
        "protein": display_range(totals["protein_g"], "g"),
        "confidence": analysis.get("overall_confidence", "unknown"),
        "summary": summary[0] if summary else "暂无摘要",
    }


def update_daily_indexes(daily_root: Path) -> None:
    entries = []
    if daily_root.exists():
        for day_dir in sorted(daily_root.iterdir(), reverse=True):
            if day_dir.is_dir() and re.fullmatch(r"\d{8}", day_dir.name):
                entry = rendered_entry(day_dir)
                if entry:
                    entries.append(entry)

    md_lines = [
        "# 每日饮食记录",
        "",
        "> 由 `diet` 流水线根据照片生成。所有数值均为估算区间。",
        "",
        "| 日期 | 热量估算 | 蛋白质估算 | 置信度 | 摘要 |",
        "|---|---:|---:|---|---|",
    ]
    for entry in entries:
        md_lines.append(
            f"| [{entry['date']}](./{entry['dir']}/README.md) | {entry['kcal']} | {entry['protein']} | {entry['confidence']} | {md_escape(entry['summary'])} |"
        )
    if not entries:
        md_lines.append("| — | — | — | — | 尚无已完成报告 |")
    md_lines.append("")
    atomic_write_text(daily_root / "README.md", "\n".join(md_lines))

    cards = "".join(
        f"<a class=\"card\" href=\"{html.escape(entry['dir'])}/index.html\"><span>{html.escape(entry['date'])}</span><strong>{html.escape(entry['kcal'])}</strong><em>{html.escape(entry['protein'])} 蛋白质</em><p>{html.escape(entry['summary'])}</p></a>"
        for entry in entries
    ) or "<p>尚无已完成报告。</p>"
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>每日饮食记录</title><style>
body{{margin:0;background:#f4f2eb;color:#18211b;font:16px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{width:min(980px,calc(100% - 28px));margin:auto;padding:52px 0}}h1{{font-size:clamp(36px,7vw,70px);margin:0 0 8px;letter-spacing:-.05em}}header p{{color:#667169}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:28px}}.card{{display:flex;flex-direction:column;gap:7px;padding:22px;border:1px solid #dfe4dc;border-radius:20px;background:#fffdf8;color:inherit;text-decoration:none;box-shadow:0 10px 30px rgba(30,50,40,.05)}}.card:hover{{transform:translateY(-2px)}}.card span,.card em{{color:#667169;font-style:normal}}.card strong{{font-size:25px}}.card p{{margin:8px 0 0}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><header><h1>每日饮食记录</h1><p>从 Apple Photos 到结构化营养估算的本地流水线。</p></header><section class="grid">{cards}</section></main></body></html>"""
    atomic_write_text(daily_root / "index.html", page)


def load_analysis_bundle(
    target: date, profile: dict[str, Any]
) -> tuple[dict[str, Path], dict[str, Any], dict[str, Any]]:
    paths = paths_for(target, profile)
    manifest = load_json(paths["manifest"])
    analysis = load_json(paths["analysis"])
    return paths, manifest, analysis


def sync_analysis_to_store(
    *,
    profile: dict[str, Any],
    paths: dict[str, Path],
    manifest: dict[str, Any],
    analysis: dict[str, Any],
    totals: dict[str, list[float]],
    comparisons: list[dict[str, str]],
) -> Path:
    db_path = database_path(profile)
    with NutritionStore(db_path) as store:
        store.upsert_day(
            analysis=analysis,
            manifest=manifest,
            analysis_path=relative_private_path(paths["analysis"]),
            analysis_sha256=sha256_file(paths["analysis"]),
            totals=totals,
            targets=profile.get("targets", {}),
            comparisons=comparisons,
        )
    return db_path


def render(args: argparse.Namespace) -> int:
    profile = load_profile()
    target = resolve_date(args.date)
    paths, manifest, analysis = load_analysis_bundle(target, profile)
    errors, warnings = validate_analysis(analysis, manifest)
    if errors:
        raise PipelineError("分析数据未通过校验：\n- " + "\n- ".join(errors))

    totals = sum_nutrition(analysis)
    day_type = analysis["day_context"]["day_type"]
    comparisons = comparison_rows(totals, profile, day_type)
    markdown = render_markdown(
        target, analysis, manifest, profile, totals, comparisons
    )
    page = render_html(target, analysis, manifest, totals, comparisons)
    atomic_write_text(paths["report_md"], markdown)
    atomic_write_text(paths["report_html"], page)
    update_daily_indexes(paths["daily_root"])
    db_path = sync_analysis_to_store(
        profile=profile,
        paths=paths,
        manifest=manifest,
        analysis=analysis,
        totals=totals,
        comparisons=comparisons,
    )

    print(f"DATE={target.isoformat()}")
    print(f"REPORT_MD={paths['report_md']}")
    print(f"REPORT_HTML={paths['report_html']}")
    print(f"DATABASE={db_path}")
    print("DATABASE_STATUS=synced")
    print(f"TOTAL_KCAL={display_range(totals['kcal'], 'kcal')}")
    print(f"TOTAL_PROTEIN={display_range(totals['protein_g'], 'g')}")
    for warning in warnings:
        print(f"WARNING={warning}")
    return 0


def verify(args: argparse.Namespace) -> int:
    profile = load_profile()
    target = resolve_date(args.date)
    paths, manifest, analysis = load_analysis_bundle(target, profile)
    errors, warnings = validate_analysis(analysis, manifest)

    day_dir = paths["day_dir"]
    for asset in manifest.get("assets", []):
        source = day_dir / asset["relative_path"]
        if not source.exists():
            errors.append(f"源媒体缺失：{source.name}")
            continue
        if source.stat().st_size != asset.get("size_bytes"):
            errors.append(f"源媒体大小变化：{source.name}")
        elif sha256_file(source) != asset.get("sha256"):
            errors.append(f"源媒体哈希变化：{source.name}")
        preview = asset.get("preview_path")
        if preview:
            preview_path = day_dir / preview
            if not preview_path.exists() or preview_path.stat().st_size == 0:
                errors.append(f"预览缺失：{source.name}")
        else:
            warnings.append(f"没有预览：{source.name}")

    for report_path in (paths["report_md"], paths["report_html"]):
        if not report_path.exists() or report_path.stat().st_size == 0:
            errors.append(f"报告缺失：{report_path}")
        elif target.isoformat() not in report_path.read_text(encoding="utf-8"):
            errors.append(f"报告没有包含日期：{report_path}")

    for index_path in (
        paths["daily_root"] / "README.md",
        paths["daily_root"] / "index.html",
    ):
        if not index_path.exists() or index_path.stat().st_size == 0:
            errors.append(f"每日索引缺失：{index_path}")

    db_path = database_path(profile)
    if not db_path.exists():
        errors.append(f"营养数据库缺失：{db_path}")
    else:
        try:
            with NutritionStore(db_path) as store:
                db_state = store.day_state(target.isoformat())
            if db_state is None:
                errors.append(f"营养数据库没有 {target.isoformat()} 记录")
            elif db_state.get("analysis_sha256") != sha256_file(paths["analysis"]):
                errors.append("营养数据库记录已过期；重新运行 diet render")
            else:
                expected_totals = sum_nutrition(analysis)
                for nutrient in NUTRIENT_KEYS:
                    stored = db_state.get("nutrients", {}).get(nutrient)
                    if stored is None:
                        errors.append(f"营养数据库缺少 {nutrient}")
                        continue
                    if any(
                        abs(float(stored[key]) - float(expected_totals[nutrient][index]))
                        > 1e-6
                        for index, key in enumerate(("low", "high"))
                    ):
                        errors.append(f"营养数据库 {nutrient} 合计与 analysis.json 不一致")
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            errors.append(f"营养数据库无法读取：{exc}")

    for html_path in (
        paths["report_html"],
        paths["daily_root"] / "index.html",
    ):
        if not html_path.exists() or html_path.stat().st_size == 0:
            continue
        html_errors, html_warnings, audit = audit_static_html(html_path)
        errors.extend(html_errors)
        warnings.extend(html_warnings)
        if html_path == paths["report_html"]:
            expected_images = int(manifest.get("preview_count", 0))
            if audit.image_count != expected_images:
                errors.append(
                    f"HTML 图片数不一致：期望 {expected_images}，实际 {audit.image_count}"
                )

    if errors:
        print("VERIFY=failed")
        for error in errors:
            print(f"ERROR={error}")
        for warning in sorted(set(warnings)):
            print(f"WARNING={warning}")
        return 1

    print("VERIFY=passed")
    print(f"DATE={target.isoformat()}")
    print(f"ASSETS={manifest.get('asset_count', 0)}")
    print(f"PREVIEWS={manifest.get('preview_count', 0)}")
    print(f"REPORT_MD={paths['report_md']}")
    print(f"REPORT_HTML={paths['report_html']}")
    print(f"DATABASE={db_path}")
    for warning in sorted(set(warnings)):
        print(f"WARNING={warning}")
    return 0


def status(args: argparse.Namespace) -> int:
    profile = load_profile()
    target = resolve_date(args.date)
    paths = paths_for(target, profile)
    media = media_files(paths["day_dir"])
    print(f"DATE={target.isoformat()}")
    print(f"DAY_DIR={'ready' if paths['day_dir'].exists() else 'missing'}")
    print(f"MEDIA={len(media)}")
    print(f"MANIFEST={'ready' if paths['manifest'].exists() else 'missing'}")
    print(f"ANALYSIS={'ready' if paths['analysis'].exists() else 'missing'}")
    print(f"REPORT_MD={'ready' if paths['report_md'].exists() else 'missing'}")
    print(f"REPORT_HTML={'ready' if paths['report_html'].exists() else 'missing'}")
    db_path = database_path(profile)
    db_status = "missing"
    if db_path.exists():
        try:
            with NutritionStore(db_path) as store:
                state = store.day_state(target.isoformat())
            if state is None:
                db_status = "day-missing"
            elif paths["analysis"].exists() and state.get("analysis_sha256") != sha256_file(paths["analysis"]):
                db_status = "stale"
            else:
                db_status = "ready"
        except (OSError, RuntimeError, sqlite3.Error):
            db_status = "error"
    print(f"DATABASE={db_status}:{db_path}")
    return 0


def database_status(args: argparse.Namespace) -> int:
    profile = load_profile()
    db_path = database_path(profile)
    with NutritionStore(db_path) as store:
        stats = store.database_stats()
    envelope = {"meta": {"source": "local", "database": str(db_path)}, "results": stats}
    if args.agent:
        print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"DATABASE={db_path}")
        print(f"SCHEMA_VERSION={stats['schema_version']}")
        print(f"DAYS={stats['day_count']}")
        print(f"FIRST_DATE={stats['first_date'] or ''}")
        print(f"LAST_DATE={stats['last_date'] or ''}")
        print(f"MEALS={stats['meal_count']}")
        print(f"FOOD_ITEMS={stats['food_item_count']}")
        print(f"IMAGES={stats['image_count']}")
    return 0


def rebuild_database(args: argparse.Namespace) -> int:
    profile = load_profile()
    daily_root = paths_for(date.today(), profile)["daily_root"]
    db_path = database_path(profile)
    with NutritionStore(db_path) as store:
        store.clear_derived_days()

    synced: list[str] = []
    skipped: list[dict[str, Any]] = []
    if daily_root.exists():
        day_dirs = sorted(
            path
            for path in daily_root.iterdir()
            if path.is_dir() and re.fullmatch(r"\d{8}", path.name)
        )
    else:
        day_dirs = []
    for day_dir in day_dirs:
        try:
            target = datetime.strptime(day_dir.name, "%Y%m%d").date()
            paths, manifest, analysis = load_analysis_bundle(target, profile)
            errors, _ = validate_analysis(analysis, manifest)
            if errors:
                skipped.append({"date": target.isoformat(), "reason": "; ".join(errors)})
                continue
            totals = sum_nutrition(analysis)
            comparisons = comparison_rows(
                totals, profile, analysis["day_context"]["day_type"]
            )
            sync_analysis_to_store(
                profile=profile,
                paths=paths,
                manifest=manifest,
                analysis=analysis,
                totals=totals,
                comparisons=comparisons,
            )
            synced.append(target.isoformat())
        except (PipelineError, KeyError, TypeError, ValueError) as exc:
            skipped.append({"date": day_dir.name, "reason": str(exc)})

    results = {"synced_dates": synced, "skipped": skipped}
    if args.agent:
        print(
            json.dumps(
                {"meta": {"source": "local", "database": str(db_path)}, "results": results},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        print(f"DATABASE={db_path}")
        print(f"SYNCED={len(synced)}")
        print(f"SKIPPED={len(skipped)}")
        for row in skipped:
            print(f"WARNING={row['date']}: {row['reason']}")
    return 0 if not skipped else 1


def nutrition_summary(args: argparse.Namespace) -> int:
    profile = load_profile()
    if args.days < 1 or args.days > 3660:
        raise PipelineError("--days 必须在 1 到 3660 之间")
    end = resolve_date(args.end)
    start = end - timedelta(days=args.days - 1)
    db_path = database_path(profile)
    with NutritionStore(db_path) as store:
        rows = store.list_days(start, end)
        provenance = store.provenance_counts(start, end)
    result = make_summary(
        rows=rows,
        start=start,
        end=end,
        requested_days=args.days,
        provenance=provenance,
    )
    report_dir = nutrition_reports_dir(profile)
    report_dir.mkdir(parents=True, exist_ok=True)
    basename = f"{end.strftime('%Y%m%d')}-{args.days}d"
    json_path = report_dir / f"{basename}.json"
    md_path = report_dir / f"{basename}.md"
    html_path = report_dir / f"{basename}.html"
    atomic_write_text(json_path, summary_json_text(result))
    atomic_write_text(md_path, render_summary_markdown(result))
    atomic_write_text(html_path, render_summary_html(result))
    html_errors, html_warnings, _ = audit_static_html(html_path)
    if html_errors:
        raise PipelineError("汇总 HTML 未通过校验：\n- " + "\n- ".join(html_errors))

    meta = {
        "source": "local",
        "database": str(db_path),
        "reports": {
            "json": str(json_path),
            "markdown": str(md_path),
            "html": str(html_path),
        },
    }
    if args.agent:
        print(
            json.dumps(
                {"meta": meta, "results": result},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        period = result["period"]
        print(f"PERIOD={period['start']}..{period['end']}")
        print(f"LOGGED_DAYS={period['logged_days']}/{period['requested_days']}")
        print(f"REPORT_JSON={json_path}")
        print(f"REPORT_MD={md_path}")
        print(f"REPORT_HTML={html_path}")
        for warning in html_warnings:
            print(f"WARNING={warning}")
    return 0


def fdc_api_key() -> tuple[str, str]:
    if os.environ.get("FDC_API_KEY"):
        return str(os.environ["FDC_API_KEY"]), "FDC_API_KEY"
    if os.environ.get("USDA_API_KEY"):
        return str(os.environ["USDA_API_KEY"]), "USDA_API_KEY"
    return "DEMO_KEY", "DEMO_KEY"


def fdc_cache_key(operation: str, request: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": operation, "request": request},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cached_fdc_payload(
    *,
    store: NutritionStore,
    cache_key: str,
    max_age_days: int,
) -> tuple[dict[str, Any] | None, str | None]:
    cached = store.cache_get(cache_key)
    if cached is None:
        return None, None
    try:
        fetched_at = datetime.fromisoformat(str(cached["fetched_at"]))
        age = datetime.now().astimezone() - fetched_at
    except (TypeError, ValueError):
        return None, None
    if age > timedelta(days=max_age_days):
        return None, None
    return cached["response"], str(cached["fetched_at"])


def fdc_search_command(args: argparse.Namespace) -> int:
    profile = load_profile()
    if args.limit < 1 or args.limit > 25:
        raise PipelineError("--limit 必须在 1 到 25 之间")
    if args.cache_days < 0 or args.timeout < 1:
        raise PipelineError("--cache-days 不能为负，--timeout 必须为正数")
    data_types = list(DEFAULT_DATA_TYPES)
    if args.include_branded:
        data_types.append("Branded")
    request_data = {
        "query": args.query.strip(),
        "pageSize": args.limit,
        "dataType": data_types,
    }
    if not request_data["query"]:
        raise PipelineError("食物搜索词不能为空")
    cache_key = fdc_cache_key("search", request_data)
    db_path = database_path(profile)
    source = "cache"
    fetched_at: str | None = None
    with NutritionStore(db_path) as store:
        payload = None
        if not args.refresh:
            payload, fetched_at = cached_fdc_payload(
                store=store, cache_key=cache_key, max_age_days=args.cache_days
            )
        if payload is None:
            if args.offline:
                raise PipelineError("离线模式下没有可用的 USDA 缓存")
            key, key_source = fdc_api_key()
            try:
                response = search_foods(
                    args.query,
                    api_key=key,
                    page_size=args.limit,
                    data_types=tuple(data_types),
                    timeout=args.timeout,
                )
            except FDCError as exc:
                raise PipelineError(str(exc)) from exc
            payload = response.payload
            fetched_at = store.cache_put(
                cache_key, response.operation, response.request, response.payload
            )
            source = "live"
        else:
            key_source = "cache"
    try:
        foods = normalize_search(payload)
    except FDCError as exc:
        raise PipelineError(str(exc)) from exc
    envelope = {
        "meta": {
            "source": source,
            "provider": "USDA FoodData Central",
            "fetched_at": fetched_at,
            "api_key_source": key_source,
            "query_sent": request_data["query"] if source == "live" else None,
            "privacy": "Only the text search query is sent to USDA; photos remain local.",
        },
        "results": foods,
    }
    if args.agent:
        print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"SOURCE={source}")
        print(f"RESULTS={len(foods)}")
        for food in foods:
            nutrients = food["nutrients_per_100g"]
            print(
                f"FDC_ID={food['fdc_id']} | {food['description']} | "
                f"{nutrients.get('kcal', '?')} kcal | "
                f"protein={nutrients.get('protein_g', '?')} g/100g | "
                f"type={food['data_type']}"
            )
    return 0


def parse_grams(value: str) -> tuple[float, float]:
    if value.strip().startswith("-"):
        raise PipelineError("--grams 不能为负数")
    text = value.strip().replace("–", ":").replace("—", ":").replace("-", ":")
    parts = [part.strip() for part in text.split(":") if part.strip()]
    if len(parts) not in {1, 2}:
        raise PipelineError("--grams 使用 150 或 100:150")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise PipelineError("--grams 必须是数字或数字范围") from exc
    low = numbers[0]
    high = numbers[-1]
    if low < 0 or high < low:
        raise PipelineError("--grams 范围无效")
    return low, high


def fdc_food_command(args: argparse.Namespace) -> int:
    profile = load_profile()
    if args.fdc_id <= 0:
        raise PipelineError("FDC ID 必须是正整数")
    if args.cache_days < 0 or args.timeout < 1:
        raise PipelineError("--cache-days 不能为负，--timeout 必须为正数")
    request_data = {"fdc_id": args.fdc_id}
    cache_key = fdc_cache_key("food", request_data)
    db_path = database_path(profile)
    source = "cache"
    fetched_at: str | None = None
    with NutritionStore(db_path) as store:
        payload = None
        if not args.refresh:
            payload, fetched_at = cached_fdc_payload(
                store=store, cache_key=cache_key, max_age_days=args.cache_days
            )
        if payload is None:
            if args.offline:
                raise PipelineError("离线模式下没有可用的 USDA 缓存")
            key, key_source = fdc_api_key()
            try:
                response = food_details(
                    args.fdc_id, api_key=key, timeout=args.timeout
                )
            except FDCError as exc:
                raise PipelineError(str(exc)) from exc
            payload = response.payload
            fetched_at = store.cache_put(
                cache_key, response.operation, response.request, response.payload
            )
            source = "live"
        else:
            key_source = "cache"
    try:
        food = normalize_food(payload)
        if args.grams:
            low, high = parse_grams(args.grams)
            result: dict[str, Any] = analysis_item_candidate(food, low, high)
        else:
            result = food
    except FDCError as exc:
        raise PipelineError(str(exc)) from exc
    envelope = {
        "meta": {
            "source": source,
            "provider": "USDA FoodData Central",
            "fetched_at": fetched_at,
            "api_key_source": key_source,
            "privacy": "Only the numeric FDC ID is sent to USDA; photos remain local.",
        },
        "results": result,
    }
    if args.agent:
        print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0


def doctor(_: argparse.Namespace) -> int:
    failures = 0
    print(f"ROOT={ROOT}")
    print(f"PROFILE={'ok' if PROFILE_PATH.exists() else 'missing'}:{PROFILE_PATH}")
    if not PROFILE_PATH.exists():
        failures += 1
    print(f"PYTHON={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    if sys.version_info < (3, 10):
        failures += 1
    if sys.platform != "darwin":
        print(f"PLATFORM=unsupported:{sys.platform}")
        failures += 1
    else:
        print("PLATFORM=macOS")
    for command in ("shortcuts", "mdls"):
        path = shutil.which(command)
        print(f"COMMAND_{command.upper()}={path or 'missing'}")
        if not path:
            failures += 1
    magick = shutil.which("magick")
    sips = shutil.which("sips")
    print(f"COMMAND_MAGICK={magick or 'missing'}")
    print(f"COMMAND_SIPS={sips or 'missing'}")
    if not magick and not sips:
        failures += 1
    try:
        profile = load_profile()
        shortcut_name = profile["pipeline"]["shortcut_name"]
        paths = paths_for(date.today(), profile)
        print(f"SHORTCUT_NAME={shortcut_name}")
        print(f"DATA_DIR={paths['daily_root']}")
        db_path = database_path(profile)
        with NutritionStore(db_path) as store:
            stats = store.database_stats()
        print(f"DATABASE={db_path}")
        print(f"DATABASE_SCHEMA={stats['schema_version']}")
        print(
            "FDC_AUTO_TEXT_QUERIES="
            + (
                "enabled"
                if profile.get("privacy", {}).get("allow_usda_text_queries") is True
                else "disabled"
            )
        )
        api_key_source = (
            "FDC_API_KEY" if os.environ.get("FDC_API_KEY")
            else "USDA_API_KEY" if os.environ.get("USDA_API_KEY")
            else "DEMO_KEY"
        )
        print(f"FDC_API_KEY_SOURCE={api_key_source}")
    except (PipelineError, KeyError, RuntimeError, sqlite3.Error) as exc:
        print(f"ERROR={exc}")
        failures += 1
    print(f"DOCTOR={'passed' if failures == 0 else 'failed'}")
    return 0 if failures == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apple Photos → 饮食分析 → Markdown/HTML 本地流水线"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare", help="运行 Shortcut、生成媒体清单和 JPEG 预览"
    )
    prepare_parser.add_argument("date", help="YYYY-MM-DD、today 或 yesterday")
    prepare_parser.add_argument("--skip-export", action="store_true")
    prepare_parser.add_argument("--reset-analysis", action="store_true")
    prepare_parser.add_argument("--shortcut", help="覆盖健康档案中的 Shortcut 名称")
    prepare_parser.add_argument("--timeout", type=int, default=180)
    prepare_parser.set_defaults(func=prepare)

    render_parser = subparsers.add_parser(
        "render", help="校验 analysis.json 并生成 Markdown/HTML 报告"
    )
    render_parser.add_argument("date")
    render_parser.set_defaults(func=render)

    verify_parser = subparsers.add_parser(
        "verify", help="验证媒体哈希、预览、分析数据和报告"
    )
    verify_parser.add_argument("date")
    verify_parser.set_defaults(func=verify)

    status_parser = subparsers.add_parser("status", help="显示某日流水线状态")
    status_parser.add_argument("date")
    status_parser.set_defaults(func=status)

    doctor_parser = subparsers.add_parser("doctor", help="检查本机依赖和配置")
    doctor_parser.set_defaults(func=doctor)

    db_status_parser = subparsers.add_parser(
        "db-status", help="显示本地 SQLite 营养索引状态"
    )
    db_status_parser.add_argument(
        "--agent", action="store_true", help="输出紧凑的机器可读 JSON"
    )
    db_status_parser.set_defaults(func=database_status)

    rebuild_parser = subparsers.add_parser(
        "rebuild-db", help="从全部 analysis.json 重建 SQLite 营养索引"
    )
    rebuild_parser.add_argument(
        "--agent", action="store_true", help="输出紧凑的机器可读 JSON"
    )
    rebuild_parser.set_defaults(func=rebuild_database)

    summary_parser = subparsers.add_parser(
        "summary", help="生成一个时间窗口的 Markdown/HTML/JSON 营养汇总"
    )
    summary_parser.add_argument("--days", type=int, default=7)
    summary_parser.add_argument("--end", default="today")
    summary_parser.add_argument(
        "--agent", action="store_true", help="输出紧凑的机器可读 JSON"
    )
    summary_parser.set_defaults(func=nutrition_summary)

    fdc_search_parser = subparsers.add_parser(
        "fdc-search", help="显式查询 USDA FoodData Central 食物数据"
    )
    fdc_search_parser.add_argument("query")
    fdc_search_parser.add_argument("--limit", type=int, default=5)
    fdc_search_parser.add_argument("--include-branded", action="store_true")
    fdc_search_parser.add_argument("--offline", action="store_true")
    fdc_search_parser.add_argument("--refresh", action="store_true")
    fdc_search_parser.add_argument("--cache-days", type=int, default=30)
    fdc_search_parser.add_argument("--timeout", type=int, default=30)
    fdc_search_parser.add_argument("--agent", action="store_true")
    fdc_search_parser.set_defaults(func=fdc_search_command)

    fdc_food_parser = subparsers.add_parser(
        "fdc-food", help="读取 FDC ID，可按克数生成 analysis.json 条目候选"
    )
    fdc_food_parser.add_argument("fdc_id", type=int)
    fdc_food_parser.add_argument("--grams", help="单值 150 或范围 100:150")
    fdc_food_parser.add_argument("--offline", action="store_true")
    fdc_food_parser.add_argument("--refresh", action="store_true")
    fdc_food_parser.add_argument("--cache-days", type=int, default=30)
    fdc_food_parser.add_argument("--timeout", type=int, default=30)
    fdc_food_parser.add_argument("--agent", action="store_true")
    fdc_food_parser.set_defaults(func=fdc_food_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "prepare",
        "render",
        "verify",
        "status",
        "doctor",
        "db-status",
        "rebuild-db",
        "summary",
        "fdc-search",
        "fdc-food",
        "-h",
        "--help",
    }
    if argv and argv[0] not in commands:
        argv.insert(0, "prepare")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (PipelineError, sqlite3.Error, RuntimeError) as exc:
        print(f"ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
