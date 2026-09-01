"""Apple Shortcut bridge, media discovery, previews, and manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .workspace import ROOT, WorkspacePaths, relative_private_path


PREVIEW_VERSION = 2
MANIFEST_SCHEMA_VERSION = 4
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


def manifest_preview_path(
    asset: dict[str, Any], manifest: dict[str, Any], paths: WorkspacePaths
) -> Path | None:
    """Resolve schema-v3+ site paths and legacy runtime-relative previews."""
    raw_path = asset.get("preview_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    candidate = Path(raw_path)
    if int(manifest.get("schema_version", 0)) >= 3:
        boundary = paths.site_root.resolve()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (ROOT / candidate).resolve()
        )
    else:
        boundary = paths.runtime_day_dir.resolve()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (paths.runtime_day_dir / candidate).resolve()
        )
    try:
        resolved.relative_to(boundary)
    except ValueError as exc:
        raise PipelineError(f"预览路径越过目录边界：{raw_path}") from exc
    return resolved


def run_shortcut(
    target: date,
    shortcut_name: str,
    timeout: int,
    expected_export_dir: Path,
) -> dict[str, Any]:
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

    reported_export_dir = fields.get("EXPORT_DIR")
    if not reported_export_dir:
        raise PipelineError("Shortcut 未返回 EXPORT_DIR，无法确认照片写入边界")
    reported_path = Path(os.path.abspath(os.path.expanduser(reported_export_dir)))
    expected_path = Path(os.path.abspath(expected_export_dir))
    if reported_path != expected_path:
        raise PipelineError(
            "Shortcut 输出目录不匹配："
            f"期望 {expected_path}，实际 {reported_path}。"
            "请导入当前 clone 在 build/shortcuts/ 下生成的 Shortcut；"
            "流水线不会使用根目录兼容链接。"
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


def make_image_preview(
    source: Path, destination: Path
) -> tuple[str | None, str | None]:
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
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0 or temporary.stat().st_size == 0:
            return (
                completed.stderr or completed.stdout or f"{converter} 转换失败"
            ).strip(), converter
        os.replace(temporary, destination)
        os.utime(destination, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
        return None, converter
    finally:
        temporary.unlink(missing_ok=True)


def make_video_preview(
    source: Path, destination: Path
) -> tuple[str | None, str | None]:
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
            os.utime(
                destination, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns)
            )
        converter = f"quicklook+{image_converter}" if image_converter else "quicklook"
        return error, converter


def build_manifest(
    target: date,
    paths: WorkspacePaths,
    shortcut_result: dict[str, Any],
) -> dict[str, Any]:
    record_dir = paths.record_dir
    preview_dir = paths.preview_dir
    preview_dir.mkdir(parents=True, exist_ok=True)

    files = media_files(record_dir)
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
                "relative_path": source.relative_to(record_dir).as_posix(),
                "preview_path": (
                    relative_private_path(preview_path)
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
                "modified_at": datetime.fromtimestamp(stat.st_mtime)
                .astimezone()
                .isoformat(),
                "capture_time": capture_time(source),
                "paired_files": [
                    name for name in stems[source.stem.lower()] if name != source.name
                ],
                "storage_state": "retained",
            }
        )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "date": target.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "root": str(ROOT),
        "record_directory": str(record_dir),
        "runtime_directory": str(paths.runtime_day_dir),
        "site_directory": str(paths.site_day_dir),
        "shortcut": shortcut_result,
        "asset_count": len(assets),
        "retained_asset_count": len(assets),
        "purged_asset_count": 0,
        "known_unrelated_reexports_purged": 0,
        "preview_count": sum(bool(asset["preview_path"]) for asset in assets),
        "assets": assets,
    }
