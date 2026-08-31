#!/usr/bin/env python3

"""Prepare, render, and verify the local Apple Photos diet-analysis pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .analysis import (
    NUTRIENT_KEYS,
    analysis_template,
    comparison_rows,
    display_range,
    sum_nutrition,
    validate_analysis,
)
from .fdc import (
    DEFAULT_DATA_TYPES,
    FDCError,
    analysis_item_candidate,
    food_details,
    normalize_food,
    normalize_search,
    search_foods,
)
from .errors import PipelineError
from .media import (
    build_manifest,
    manifest_preview_path,
    media_files,
    run_shortcut,
    sha256_file,
)
from .presentation import (
    audit_static_html,
    render_html,
    render_markdown,
    update_daily_indexes,
    update_dashboard,
)
from .store import NutritionStore
from .summary import json_text as summary_json_text
from .summary import make_summary, render_html as render_summary_html
from .summary import render_markdown as render_summary_markdown
from .workspace import (
    PROFILE_PATH,
    ROOT,
    WorkspacePaths,
    atomic_write_text,
    database_path,
    load_json,
    load_profile,
    nutrition_reports_dir,
    nutrition_site_dir,
    paths_for,
    relative_private_path,
    resolve_date,
    write_json,
)
























































def prepare(args: argparse.Namespace) -> int:
    profile = load_profile()
    target = resolve_date(args.date)
    paths = paths_for(target, profile)
    paths.record_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_day_dir.mkdir(parents=True, exist_ok=True)
    paths.site_day_dir.mkdir(parents=True, exist_ok=True)

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
        shortcut_result = run_shortcut(
            target,
            shortcut_name,
            args.timeout,
            paths.record_dir,
        )

    manifest = build_manifest(target, paths, shortcut_result)
    write_json(paths.manifest, manifest)
    template = analysis_template(target, manifest)
    write_json(paths.template, template)

    if args.reset_analysis or not paths.analysis.exists():
        write_json(paths.analysis, template)
        analysis_state = "created"
    else:
        analysis_state = "preserved"
        existing = load_json(paths.analysis)
        manifest_files = {asset["file"] for asset in manifest["assets"]}
        analysis_files = {
            row.get("file")
            for row in existing.get("images", [])
            if isinstance(row, dict)
        }
        if manifest_files != analysis_files:
            analysis_state = "preserved-needs-sync"

    print(f"DATE={target.isoformat()}")
    print(f"RECORD_DIR={paths.record_dir}")
    print(f"RUNTIME_DIR={paths.runtime_day_dir}")
    print(f"SITE_DIR={paths.site_day_dir}")
    print(f"ASSETS={manifest['asset_count']}")
    print(f"PREVIEWS={manifest['preview_count']}")
    print(f"MANIFEST={paths.manifest}")
    print(f"ANALYSIS={paths.analysis}")
    print(f"ANALYSIS_STATE={analysis_state}")
    if manifest["preview_count"] != manifest["asset_count"]:
        print("WARNING=部分媒体没有预览；查看 manifest.json 的 preview_error")
    return 0








































def load_analysis_bundle(
    target: date, profile: dict[str, Any]
) -> tuple[WorkspacePaths, dict[str, Any], dict[str, Any]]:
    paths = paths_for(target, profile)
    manifest = load_json(paths.manifest)
    analysis = load_json(paths.analysis)
    return paths, manifest, analysis


def sync_analysis_to_store(
    *,
    profile: dict[str, Any],
    paths: WorkspacePaths,
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
            analysis_path=relative_private_path(paths.analysis),
            analysis_sha256=sha256_file(paths.analysis),
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
        target,
        analysis,
        manifest,
        totals,
        comparisons,
        Path(os.path.relpath(paths.analysis, paths.report_md.parent)).as_posix(),
        paths,
    )
    page = render_html(target, analysis, manifest, totals, comparisons, paths)
    atomic_write_text(paths.report_md, markdown)
    atomic_write_text(paths.report_html, page)
    update_daily_indexes(profile)
    dashboard_path = update_dashboard(profile)
    db_path = sync_analysis_to_store(
        profile=profile,
        paths=paths,
        manifest=manifest,
        analysis=analysis,
        totals=totals,
        comparisons=comparisons,
    )

    print(f"DATE={target.isoformat()}")
    print(f"REPORT_MD={paths.report_md}")
    print(f"REPORT_HTML={paths.report_html}")
    print(f"DASHBOARD={dashboard_path}")
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

    record_dir = paths.record_dir
    for asset in manifest.get("assets", []):
        source = record_dir / asset["relative_path"]
        if not source.exists():
            errors.append(f"源媒体缺失：{source.name}")
            continue
        if source.stat().st_size != asset.get("size_bytes"):
            errors.append(f"源媒体大小变化：{source.name}")
        elif sha256_file(source) != asset.get("sha256"):
            errors.append(f"源媒体哈希变化：{source.name}")
        preview = asset.get("preview_path")
        if preview:
            preview_path = manifest_preview_path(asset, manifest, paths)
            if preview_path is None:
                errors.append(f"预览路径无效：{source.name}")
                continue
            if not preview_path.exists() or preview_path.stat().st_size == 0:
                errors.append(f"预览缺失：{source.name}")
        else:
            warnings.append(f"没有预览：{source.name}")

    for report_path in (paths.report_md, paths.report_html):
        if not report_path.exists() or report_path.stat().st_size == 0:
            errors.append(f"报告缺失：{report_path}")
        elif target.isoformat() not in report_path.read_text(encoding="utf-8"):
            errors.append(f"报告没有包含日期：{report_path}")

    for index_path in (
        paths.daily_index_md,
        paths.daily_index_html,
        paths.dashboard,
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
            elif db_state.get("analysis_sha256") != sha256_file(paths.analysis):
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
        paths.report_html,
        paths.daily_index_html,
        paths.dashboard,
    ):
        if not html_path.exists() or html_path.stat().st_size == 0:
            continue
        html_errors, html_warnings, audit = audit_static_html(html_path)
        errors.extend(html_errors)
        warnings.extend(html_warnings)
        if html_path == paths.report_html:
            expected_images = int(manifest.get("preview_count", 0))
            if audit.image_count != expected_images:
                errors.append(
                    f"HTML 图片数不一致：期望 {expected_images}，实际 {audit.image_count}"
                )

    boundary_roots = {
        (ROOT / "data").resolve(),
        paths.records_root.resolve(),
        paths.runtime_root.resolve(),
    }
    for boundary_root in sorted(boundary_roots):
        if boundary_root.exists():
            for misplaced_html in boundary_root.rglob("*.html"):
                errors.append(f"HTML 不应位于 {boundary_root.name}/：{misplaced_html}")

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
    print(f"REPORT_MD={paths.report_md}")
    print(f"REPORT_HTML={paths.report_html}")
    print(f"DASHBOARD={paths.dashboard}")
    print(f"DATABASE={db_path}")
    for warning in sorted(set(warnings)):
        print(f"WARNING={warning}")
    return 0


def status(args: argparse.Namespace) -> int:
    profile = load_profile()
    target = resolve_date(args.date)
    paths = paths_for(target, profile)
    media = media_files(paths.record_dir)
    print(f"DATE={target.isoformat()}")
    print(
        f"RECORD_DIR={'ready' if paths.record_dir.exists() else 'missing'}:"
        f"{paths.record_dir}"
    )
    print(
        f"RUNTIME_DIR={'ready' if paths.runtime_day_dir.exists() else 'missing'}:"
        f"{paths.runtime_day_dir}"
    )
    print(
        f"SITE_DIR={'ready' if paths.site_day_dir.exists() else 'missing'}:"
        f"{paths.site_day_dir}"
    )
    print(f"MEDIA={len(media)}")
    print(f"MANIFEST={'ready' if paths.manifest.exists() else 'missing'}")
    print(f"ANALYSIS={'ready' if paths.analysis.exists() else 'missing'}")
    print(f"REPORT_MD={'ready' if paths.report_md.exists() else 'missing'}")
    print(f"REPORT_HTML={'ready' if paths.report_html.exists() else 'missing'}")
    print(
        f"DASHBOARD={'ready' if paths.dashboard.exists() else 'missing'}"
    )
    db_path = database_path(profile)
    db_status = "missing"
    if db_path.exists():
        try:
            with NutritionStore(db_path) as store:
                state = store.day_state(target.isoformat())
            if state is None:
                db_status = "day-missing"
            elif paths.analysis.exists() and state.get("analysis_sha256") != sha256_file(paths.analysis):
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
    records_root = paths_for(date.today(), profile).records_root
    db_path = database_path(profile)
    with NutritionStore(db_path) as store:
        store.clear_derived_days()

    synced: list[str] = []
    skipped: list[dict[str, Any]] = []
    if records_root.exists():
        day_dirs = sorted(
            path
            for path in records_root.iterdir()
            if path.is_dir() and re.fullmatch(r"\d{8}", path.name)
        )
    else:
        day_dirs = []
    for day_dir in day_dirs:
        try:
            target = datetime.strptime(day_dir.name, "%Y%m%d").date()
            paths = paths_for(target, profile)
            analysis = load_json(paths.analysis)
            manifest = build_manifest(
                target,
                paths,
                {
                    "name": profile["pipeline"]["shortcut_name"],
                    "skipped": True,
                    "reason": "rebuild-db",
                    "stdout": "",
                    "stderr": "",
                    "fields": {},
                },
            )
            write_json(paths.manifest, manifest)
            write_json(paths.template, analysis_template(target, manifest))
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
    html_dir = nutrition_site_dir(profile)
    html_dir.mkdir(parents=True, exist_ok=True)
    basename = f"{end.strftime('%Y%m%d')}-{args.days}d"
    json_path = report_dir / f"{basename}.json"
    md_path = report_dir / f"{basename}.md"
    html_path = html_dir / f"{basename}.html"
    atomic_write_text(json_path, summary_json_text(result))
    atomic_write_text(md_path, render_summary_markdown(result))
    atomic_write_text(html_path, render_summary_html(result))
    dashboard_path = update_dashboard(profile)
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
            "dashboard": str(dashboard_path),
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
        print(f"DASHBOARD={dashboard_path}")
        for warning in html_warnings:
            print(f"WARNING={warning}")
    return 0


def dashboard_command(_: argparse.Namespace) -> int:
    profile = load_profile()
    dashboard_path = update_dashboard(profile)
    errors, warnings, _ = audit_static_html(dashboard_path)
    if errors:
        raise PipelineError("健康门户未通过校验：\n- " + "\n- ".join(errors))
    print(f"DASHBOARD={dashboard_path}")
    print("DASHBOARD_STATUS=ready")
    for warning in warnings:
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
        print(f"RECORDS_DIR={paths.records_root}")
        print(f"RUNTIME_DIR={paths.runtime_root}")
        print(f"SITE_DIR={paths.site_root}")
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

    dashboard_parser = subparsers.add_parser(
        "dashboard", help="重建并校验本地静态健康门户"
    )
    dashboard_parser.set_defaults(func=dashboard_command)

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
        "dashboard",
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
