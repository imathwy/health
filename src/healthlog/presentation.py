"""Offline HTML/Markdown rendering and the private site navigation."""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .analysis import (
    FOOD_RELATED_CLASSIFICATIONS,
    display_range,
    image_classification_counts,
    sum_nutrition,
)
from .errors import PipelineError
from .media import manifest_preview_path
from .tracking import (
    body_measurement_rows,
    daily_observation_rows,
    iron_calcium_row,
    meal_protein_rows,
    meal_tag_counts,
)
from .workspace import (
    ANALYSIS_NAME,
    MANIFEST_NAME,
    PIPELINE_DIR_NAME,
    REPORT_HTML_NAME,
    WorkspacePaths,
    atomic_write_text,
    load_json,
    nutrition_site_dir,
    paths_for,
)


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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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
            errors.append(f"HTML 本地引用缺失：{path.name} 的 {tag}[{key}]={raw_value}")
    return errors, warnings, audit


def preview_href(
    asset: dict[str, Any],
    manifest: dict[str, Any],
    paths: WorkspacePaths,
    source_dir: Path,
) -> str | None:
    resolved = manifest_preview_path(asset, manifest, paths)
    if resolved is None:
        return None
    return Path(os.path.relpath(resolved, source_dir)).as_posix()


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
        str(evidence.get("nutrition_source")),
        str(evidence.get("nutrition_source", "未明确")),
    )
    portion_method = portion_labels.get(
        str(evidence.get("portion_method")),
        str(evidence.get("portion_method", "份量未知")),
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


def tracking_source_label(value: Any) -> str:
    return {
        "user_reported": "用户记录",
        "measured": "实测",
        "photo_review": "照片复核",
        "derived_from_items": "食物条目汇总",
        "package_label": "包装标签",
        "wearable": "穿戴设备",
        "unknown": "未记录",
    }.get(str(value), str(value))


def image_classification_label(value: Any) -> str:
    return {
        "consumed_food": "确认摄入相关",
        "possible_food": "可能与摄入相关",
        "unrelated": "无关照片",
        "unreviewed": "尚未检查",
    }.get(str(value), str(value))


def image_classification_class(value: Any) -> str:
    if value == "consumed_food":
        return "ok"
    if value == "possible_food":
        return "warn"
    return "neutral"


def tracking_estimate(row: dict[str, Any]) -> str:
    value = row.get("range")
    if not isinstance(value, list) or len(value) != 2:
        return "未记录"
    suffix = "（覆盖不全）" if row.get("coverage") != "complete" else ""
    return f"{display_range(value, row['unit'])}{suffix}"


def body_value(row: dict[str, Any]) -> str:
    value = row.get("value")
    if value is None:
        return "未记录"
    return f"{value:g} {row['unit']}" if isinstance(value, float) else f"{value} {row['unit']}"


def render_markdown(
    target: date,
    analysis: dict[str, Any],
    manifest: dict[str, Any],
    totals: dict[str, list[float]],
    comparisons: list[dict[str, str]],
    analysis_link: str,
    paths: WorkspacePaths,
    profile: dict[str, Any],
) -> str:
    day_context = analysis["day_context"]
    assessment = analysis["assessment"]
    image_by_file = {row["file"]: row for row in analysis["images"]}
    preview_by_file = {
        asset["file"]: preview_href(asset, manifest, paths, paths.report_md.parent)
        for asset in manifest["assets"]
    }
    observation_rows = daily_observation_rows(analysis, profile)
    measurement_rows = body_measurement_rows(analysis)
    protein_rows = meal_protein_rows(analysis, profile)
    protein_by_meal = {row["meal_id"]: row for row in protein_rows}
    tag_counts = meal_tag_counts(analysis)
    timing = iron_calcium_row(analysis)
    screening_counts = image_classification_counts(analysis)
    food_assets = [
        asset
        for asset in manifest["assets"]
        if image_by_file[asset["file"]]["classification"]
        in FOOD_RELATED_CLASSIFICATIONS
    ]
    unrelated_assets = [
        asset
        for asset in manifest["assets"]
        if image_by_file[asset["file"]]["classification"] == "unrelated"
    ]

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
        (
            f"| 照片筛选 | 共检查 {screening_counts['reviewed']} 张；"
            f"食物相关 {screening_counts['food_related']} 张，"
            f"其中待确认 {screening_counts['possible_food']} 张；"
            f"无关 {screening_counts['unrelated']} 张 |"
        ),
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

    lines.extend(
        [
            "",
            "## 饮水、钙、恢复与训练",
            "",
            "| 指标 | 当日记录 | 参考目标 | 判断 | 来源 |",
            "|---|---:|---:|---|---|",
        ]
    )
    lines.extend(
        f"| {row['label']} | {tracking_estimate(row)} | {row['target']} | "
        f"{row['status']} | {tracking_source_label(row['source'])} |"
        for row in observation_rows
    )
    last_caffeine = analysis.get("tracking", {}).get("last_caffeine_time")
    if last_caffeine:
        lines.append(f"| 最晚咖啡因时间 | {last_caffeine} | 观察项 | 已记录 | 用户记录 |")

    lines.extend(
        [
            "",
            "### 体重与围度",
            "",
            "| 指标 | 测量值 | 测量时间 | 条件 | 来源 |",
            "|---|---:|---|---|---|",
        ]
    )
    for row in measurement_rows:
        lines.append(
            f"| {row['label']} | {body_value(row)} | "
            f"{md_escape(row.get('recorded_at') or '未记录')} | "
            f"{md_escape(row.get('context') or '未记录')} | "
            f"{tracking_source_label(row['source'])} |"
        )
    meal_tagging = analysis.get("tracking", {}).get("meal_tagging", {})
    lines.extend(
        [
            "",
            "### 铁与食物频次",
            "",
            f"- 已确认血红素铁来源餐次：{tag_counts['heme_iron']} 餐。",
            f"- 已确认油性鱼餐次：{tag_counts['oily_fish']} 餐。",
            (
                "- 餐次标注覆盖："
                f"{meal_tagging.get('coverage', 'unknown')}；覆盖不完整时仅表示确认下限。"
            ),
            f"- 铁与钙时序：{timing['label']}。",
        ]
    )

    lines.extend(["", "## 逐餐记录", ""])
    for meal in analysis["meals"]:
        meal_time = meal.get("time") or "时间不确定"
        protein = protein_by_meal[meal["id"]]
        tags = meal.get("tracking_tags", [])
        tag_text = "、".join(tags) if tags else "无"
        lines.extend(
            [
                f"### {meal['label']}（{meal_time}）",
                "",
                f"关联图片：{', '.join(f'`{name}`' for name in meal.get('images', [])) or '无'}",
                (
                    f"本餐蛋白质：{display_range(protein['range'], 'g')}；"
                    f"参考 {display_range(protein['target'], 'g')}；{protein['status']}。"
                ),
                f"追踪标签：{tag_text}。",
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
            "## 食物相关图片核对",
            "",
            "> 只有 `consumed_food` 会进入餐次和营养估算；`possible_food` 只保留待确认线索。",
            "",
            "| 原文件 | 预览 | 分类 | 餐次 | 可见事实 | 不确定性 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for asset in food_assets:
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
                    md_escape(image_classification_label(row["classification"])),
                    md_escape(row.get("meal_id") or "—"),
                    md_escape("；".join(row.get("observations", [])) or "—"),
                    md_escape("；".join(row.get("uncertainties", [])) or "—"),
                ]
            )
            + " |"
        )
    if not food_assets:
        lines.append("| — | — | — | — | 当天未发现食物相关照片 | — |")

    lines.extend(
        [
            "",
            "## 无关图片审计",
            "",
            "> 这些照片已经逐张检查，但不参与餐次重建或营养估算。",
            "",
            "| 原文件 | 预览 | 可见事实 |",
            "|---|---|---|",
        ]
    )
    for asset in unrelated_assets:
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
                    md_escape("；".join(row.get("observations", [])) or "—"),
                ]
            )
            + " |"
        )
    if not unrelated_assets:
        lines.append("| — | — | 未发现无关照片 |")

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
            f"- 结构化分析：[`{ANALYSIS_NAME}`]({analysis_link})",
            f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
            "",
            "本报告用于个人饮食记录，不替代医疗诊断或个体化营养处方。",
            "",
        ]
    )
    return "\n".join(lines)


def html_list(items: Any, empty: str = "暂无") -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="muted">{html.escape(empty)}</p>'
    return (
        "<ul>"
        + "".join(f"<li>{html.escape(str(item))}</li>" for item in items)
        + "</ul>"
    )


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
    paths: WorkspacePaths,
    profile: dict[str, Any],
) -> str:
    assessment = analysis["assessment"]
    image_rows = {row["file"]: row for row in analysis["images"]}
    observation_rows = daily_observation_rows(analysis, profile)
    measurement_rows = body_measurement_rows(analysis)
    protein_rows = meal_protein_rows(analysis, profile)
    protein_by_meal = {row["meal_id"]: row for row in protein_rows}
    tag_counts = meal_tag_counts(analysis)
    timing = iron_calcium_row(analysis)
    screening_counts = image_classification_counts(analysis)
    comparison_html = "".join(
        "<tr>"
        f"<td>{html.escape(row['label'])}</td>"
        f"<td>{html.escape(row['estimate'])}</td>"
        f"<td>{html.escape(row['target'])}</td>"
        f'<td><span class="pill {status_class(row["status"])}">{html.escape(row["status"])}</span></td>'
        "</tr>"
        for row in comparisons
    )
    observation_html = "".join(
        "<tr>"
        f"<td>{html.escape(row['label'])}</td>"
        f"<td>{html.escape(tracking_estimate(row))}</td>"
        f"<td>{html.escape(row['target'])}</td>"
        f'<td><span class="pill {status_class(row["status"])}">{html.escape(row["status"])}</span></td>'
        f"<td>{html.escape(tracking_source_label(row['source']))}</td>"
        "</tr>"
        for row in observation_rows
    )
    last_caffeine = analysis.get("tracking", {}).get("last_caffeine_time")
    if last_caffeine:
        observation_html += (
            "<tr><td>最晚咖啡因时间</td>"
            f"<td>{html.escape(str(last_caffeine))}</td>"
            "<td>观察项</td><td><span class=\"pill uncertain\">已记录</span></td>"
            "<td>用户记录</td></tr>"
        )
    measurement_html = "".join(
        "<tr>"
        f"<td>{html.escape(row['label'])}</td>"
        f"<td>{html.escape(body_value(row))}</td>"
        f"<td>{html.escape(str(row.get('recorded_at') or '未记录'))}</td>"
        f"<td>{html.escape(str(row.get('context') or '未记录'))}</td>"
        f"<td>{html.escape(tracking_source_label(row['source']))}</td>"
        "</tr>"
        for row in measurement_rows
    )

    meal_sections: list[str] = []
    for meal in analysis["meals"]:
        meal_protein = protein_by_meal[meal["id"]]
        meal_tags = "、".join(meal.get("tracking_tags", [])) or "无"
        item_rows = []
        for item in meal["items"]:
            nutrition = item["nutrition"]
            item_rows.append(
                "<tr>"
                f'<td><strong>{html.escape(item["name"])}</strong><br><span class="muted">{html.escape(item["portion"])}</span></td>'
                f"<td>{html.escape(display_range(nutrition['kcal'], 'kcal'))}</td>"
                f"<td>{html.escape(display_range(nutrition['protein_g'], 'g'))}</td>"
                f"<td>{html.escape(display_range(nutrition['carbohydrate_g'], 'g'))}</td>"
                f"<td>{html.escape(display_range(nutrition['fat_g'], 'g'))}</td>"
                f"<td>{html.escape(evidence_label(item))}</td>"
                f"<td>{html.escape(item['confidence'])}</td>"
                "</tr>"
            )
        meal_sections.append(
            '<section class="panel">'
            f'<div class="section-head"><div><p class="eyebrow">{html.escape(meal.get("time") or "时间不确定")}</p><h2>{html.escape(meal["label"])}</h2></div>'
            f'<div><span class="pill {status_class(meal_protein["status"])}">蛋白质 {html.escape(display_range(meal_protein["range"], "g"))} · {html.escape(meal_protein["status"])}</span> '
            f'<span class="pill neutral">{len(meal.get("images", []))} 张图片</span></div></div>'
            f'<p class="muted">每餐参考 {html.escape(display_range(meal_protein["target"], "g"))}；追踪标签：{html.escape(meal_tags)}。</p>'
            '<div class="table-wrap"><table><thead><tr><th>食物与份量</th><th>热量</th><th>蛋白质</th><th>碳水</th><th>脂肪</th><th>证据</th><th>置信度</th></tr></thead>'
            f"<tbody>{''.join(item_rows)}</tbody></table></div>"
            f"{html_list(meal.get('notes', []), '无额外备注')}"
            "</section>"
        )

    food_gallery_cards: list[str] = []
    unrelated_gallery_cards: list[str] = []
    for asset in manifest["assets"]:
        record = image_rows[asset["file"]]
        preview = preview_href(asset, manifest, paths, paths.report_html.parent)
        if preview:
            media = f'<img src="{html.escape(preview, quote=True)}" alt="{html.escape(asset["file"], quote=True)}" loading="lazy">'
        else:
            media = '<div class="no-preview">无预览</div>'
        observations = "；".join(record.get("observations", [])) or "未记录可见事实"
        uncertainties = "；".join(record.get("uncertainties", [])) or "未记录"
        classification = record["classification"]
        card = (
            '<article class="photo-card">'
            f'{media}<div class="photo-body"><div class="photo-meta"><code>{html.escape(asset["file"])}</code>'
            f'<span class="pill {image_classification_class(classification)}">{html.escape(image_classification_label(classification))}</span></div>'
            f'<p>{html.escape(observations)}</p><p class="muted">不确定性：{html.escape(uncertainties)}</p></div></article>'
        )
        if classification in FOOD_RELATED_CLASSIFICATIONS:
            food_gallery_cards.append(card)
        elif classification == "unrelated":
            unrelated_gallery_cards.append(card)

    food_gallery = "".join(food_gallery_cards) or "<p>当天未发现食物相关照片。</p>"
    unrelated_audit = ""
    if unrelated_gallery_cards:
        unrelated_audit = (
            '<details class="panel audit">'
            f'<summary>无关照片审计 · {len(unrelated_gallery_cards)} 张（已检查，不参与营养估算）</summary>'
            '<p class="muted">保留这些分类是为了证明当天全部导出照片均已检查；默认折叠，避免干扰饮食复盘。</p>'
            f'<div class="gallery">{"".join(unrelated_gallery_cards)}</div></details>'
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
    details.audit > summary {{ cursor:pointer; font-weight:750; font-size:18px; }} details.audit[open] > summary {{ margin-bottom:14px; }}
    footer {{ color:var(--muted); text-align:center; padding-top:20px; font-size:13px; }}
    @media (max-width:850px) {{ .metrics,.gallery {{ grid-template-columns:repeat(2,1fr); }} .grid-2 {{ grid-template-columns:1fr; }} }}
    @media (max-width:560px) {{ main {{ width:min(100% - 20px,1120px); padding-top:10px; }} .hero {{ padding:26px 20px; border-radius:20px; }} .metrics,.gallery {{ grid-template-columns:1fr; }} .panel {{ padding:18px; }} }}
  </style>
</head>
<body>
<main>
  <header class="hero">
    <p class="eyebrow">Daily Diet Review · {html.escape(day_context.get("day_type", "unknown"))}</p>
    <h1>{target.isoformat()}</h1>
    <p>先检查当天全部照片并筛出食物相关内容，再进行区间估算。照片不一定覆盖全部摄入，拍到的食物也不自动视为全部吃完。</p>
  </header>
  <section class="metrics">
    <div class="metric"><span>热量</span><b>{html.escape(display_range(totals["kcal"], "kcal"))}</b></div>
    <div class="metric"><span>蛋白质</span><b>{html.escape(display_range(totals["protein_g"], "g"))}</b></div>
    <div class="metric"><span>碳水</span><b>{html.escape(display_range(totals["carbohydrate_g"], "g"))}</b></div>
    <div class="metric"><span>脂肪</span><b>{html.escape(display_range(totals["fat_g"], "g"))}</b></div>
  </section>
  <div class="notice">已检查 {screening_counts['reviewed']} 张照片：食物相关 {screening_counts['food_related']} 张（其中待确认 {screening_counts['possible_food']} 张），无关 {screening_counts['unrelated']} 张。照片覆盖：{html.escape(str(day_context.get("photo_coverage", "unknown")))}；总体置信度：{html.escape(str(analysis.get("overall_confidence")))}。只有确认摄入相关的照片进入营养估算。</div>
  <section class="panel">
    <div class="section-head"><div><p class="eyebrow">Targets</p><h2>营养估算与个人目标</h2></div></div>
    <div class="table-wrap"><table><thead><tr><th>营养素</th><th>照片估算</th><th>个人目标</th><th>判断</th></tr></thead><tbody>{comparison_html}</tbody></table></div>
  </section>
  <section class="panel">
    <div class="section-head"><div><p class="eyebrow">Daily tracking</p><h2>饮水、钙、恢复与训练</h2></div></div>
    <div class="table-wrap"><table><thead><tr><th>指标</th><th>当日记录</th><th>参考目标</th><th>判断</th><th>来源</th></tr></thead><tbody>{observation_html}</tbody></table></div>
  </section>
  <section class="panel">
    <div class="section-head"><div><p class="eyebrow">Body measurements</p><h2>体重与围度</h2></div></div>
    <div class="table-wrap"><table><thead><tr><th>指标</th><th>测量值</th><th>时间</th><th>条件</th><th>来源</th></tr></thead><tbody>{measurement_html}</tbody></table></div>
  </section>
  <section class="panel"><p class="eyebrow">Iron & food frequency</p><h2>铁、钙与食物频次</h2><ul><li>已确认血红素铁来源餐次：{tag_counts['heme_iron']} 餐</li><li>已确认油性鱼餐次：{tag_counts['oily_fish']} 餐</li><li>铁与钙时序：{html.escape(timing['label'])}</li></ul><p class="muted">普通混合膳食不单独判为冲突；餐次覆盖不完整时，频次仅为确认下限。</p></section>
  {"".join(meal_sections)}
  <section class="grid-2">
    <div class="panel"><p class="eyebrow">Assessment</p><h2>主要发现</h2>{html_list(assessment.get("summary"))}<h3>做得好的地方</h3>{html_list(assessment.get("strengths"))}<h3>主要缺口</h3>{html_list(assessment.get("gaps"))}</div>
    <div class="panel"><p class="eyebrow">Next actions</p><h2>下一次怎么吃</h2>{html_list(assessment.get("next_actions"))}<h3>补剂说明</h3><p>{html.escape(assessment.get("supplement_note") or "不根据单日照片新增补剂。")}</p></div>
  </section>
  <section class="panel"><p class="eyebrow">Food photo screening</p><h2>食物相关图片</h2><p class="muted">包装、饮料、营养标签、餐前餐后和重复角度都先判定相关性；可能相关但尚未确认摄入的照片不会进入营养合计。</p><div class="gallery">{food_gallery}</div></section>
  {unrelated_audit}
  <section class="panel"><p class="eyebrow">Limits</p><h2>假设与限制</h2>{html_list(analysis.get("assumptions"))}</section>
  <footer>生成于 {datetime.now().astimezone().isoformat(timespec="seconds")} · 本报告用于个人饮食记录，不替代医疗诊断或营养处方。</footer>
</main>
</body>
</html>
"""


@dataclass(frozen=True, slots=True)
class RenderedEntry:
    """A validated daily report projected for indexes and navigation."""

    record_date: str
    directory: str
    kcal: str
    protein: str
    confidence: str
    summary: str


@dataclass(frozen=True, slots=True)
class DashboardView:
    """One report exposed by the private dashboard."""

    key: str
    group: str
    label: str
    title: str
    description: str
    href: str

    def payload(self) -> dict[str, str]:
        return {
            "key": self.key,
            "group": self.group,
            "label": self.label,
            "title": self.title,
            "description": self.description,
            "href": self.href,
        }


def rendered_entry(paths: WorkspacePaths) -> RenderedEntry | None:
    analysis_path = paths.analysis
    runtime_day_dir = paths.runtime_day_dir
    if not analysis_path.exists() or not paths.report_html.exists():
        return None
    try:
        analysis = load_json(analysis_path)
        totals = sum_nutrition(analysis)
    except (PipelineError, KeyError, TypeError, ValueError):
        return None
    summary = analysis.get("assessment", {}).get("summary", [])
    return RenderedEntry(
        record_date=str(analysis.get("date", runtime_day_dir.name)),
        directory=runtime_day_dir.name,
        kcal=display_range(totals["kcal"], "kcal"),
        protein=display_range(totals["protein_g"], "g"),
        confidence=str(analysis.get("overall_confidence", "unknown")),
        summary=str(summary[0]) if summary else "暂无摘要",
    )


def update_daily_indexes(profile: dict[str, Any]) -> None:
    paths = paths_for(date.today(), profile)
    daily_runtime_root = paths.daily_runtime_root
    daily_site_root = paths.daily_site_root
    daily_runtime_root.mkdir(parents=True, exist_ok=True)
    daily_site_root.mkdir(parents=True, exist_ok=True)
    entries = []
    if daily_site_root.exists():
        for site_day_dir in sorted(daily_site_root.iterdir(), reverse=True):
            if site_day_dir.is_dir() and re.fullmatch(r"\d{8}", site_day_dir.name):
                target = datetime.strptime(site_day_dir.name, "%Y%m%d").date()
                entry = rendered_entry(paths_for(target, profile))
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
            f"| [{entry.record_date}](./{entry.directory}/README.md) | {entry.kcal} | {entry.protein} | {entry.confidence} | {md_escape(entry.summary)} |"
        )
    if not entries:
        md_lines.append("| — | — | — | — | 尚无已完成报告 |")
    md_lines.append("")
    atomic_write_text(daily_runtime_root / "README.md", "\n".join(md_lines))

    cards = (
        "".join(
            f'<a class="card" href="{html.escape(entry.directory)}/index.html"><span>{html.escape(entry.record_date)}</span><strong>{html.escape(entry.kcal)}</strong><em>{html.escape(entry.protein)} 蛋白质</em><p>{html.escape(entry.summary)}</p></a>'
            for entry in entries
        )
        or "<p>尚无已完成报告。</p>"
    )
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>每日饮食记录</title><style>
body{{margin:0;background:#f4f2eb;color:#18211b;font:16px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{width:min(980px,calc(100% - 28px));margin:auto;padding:52px 0}}h1{{font-size:clamp(36px,7vw,70px);margin:0 0 8px;letter-spacing:-.05em}}header p{{color:#667169}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:28px}}.card{{display:flex;flex-direction:column;gap:7px;padding:22px;border:1px solid #dfe4dc;border-radius:20px;background:#fffdf8;color:inherit;text-decoration:none;box-shadow:0 10px 30px rgba(30,50,40,.05)}}.card:hover{{transform:translateY(-2px)}}.card span,.card em{{color:#667169;font-style:normal}}.card strong{{font-size:25px}}.card p{{margin:8px 0 0}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><header><h1>每日饮食记录</h1><p>从 Apple Photos 到结构化营养估算的本地流水线。</p></header><section class="grid">{cards}</section></main></body></html>"""
    atomic_write_text(daily_site_root / "index.html", page)


def relative_href(source_dir: Path, target: Path) -> str:
    return Path(os.path.relpath(target, source_dir)).as_posix()


def newest_report(report_dir: Path, suffix: str) -> Path | None:
    candidates = sorted(report_dir.glob(f"*-{suffix}.html"), reverse=True)
    return candidates[0] if candidates else None


def _dashboard_view(
    site_root: Path,
    key: str,
    group: str,
    label: str,
    title: str,
    description: str,
    target: Path | None,
) -> DashboardView | None:
    if target is None or not target.is_file():
        return None
    return DashboardView(
        key=key,
        group=group,
        label=label,
        title=title,
        description=description,
        href=relative_href(site_root, target),
    )


def _dashboard_views(
    site_root: Path,
    latest_entry: RenderedEntry | None,
    latest_report: Path | None,
    daily_index: Path,
    seven_day: Path | None,
    thirty_day: Path | None,
) -> list[DashboardView]:
    specifications = [
        (
            "health",
            "健康计划",
            "健康与补剂",
            "健康建议与补剂方案",
            "查看营养成分表、补剂取舍、使用时间和风险提示。",
            site_root / "health" / "index.html",
        ),
        (
            "latest",
            "每日饮食",
            "最近一天",
            f"{latest_entry.record_date} 饮食分析"
            if latest_entry
            else "最近一天饮食分析",
            "逐餐估算、目标比较、照片证据与不确定性。",
            latest_report,
        ),
        (
            "daily",
            "每日饮食",
            "全部日期",
            "每日饮食索引",
            "按日期进入已完成的饮食报告。",
            daily_index,
        ),
        (
            "week",
            "长期趋势",
            "7 天",
            "7 天营养汇总",
            "查看记录覆盖、区间均值与短期趋势证据。",
            seven_day,
        ),
        (
            "month",
            "长期趋势",
            "30 天",
            "30 天营养汇总",
            "查看更长窗口的摄入区间与数据缺口。",
            thirty_day,
        ),
    ]
    views = [
        _dashboard_view(site_root, *specification) for specification in specifications
    ]
    return [view for view in views if view is not None]


def _render_dashboard_page(
    latest_entry: RenderedEntry | None,
    daily_count: int,
    views: list[DashboardView],
    generated: str,
) -> str:
    nav_groups: list[str] = []
    for group in dict.fromkeys(view.group for view in views):
        links = "".join(
            (
                f'<a class="nav-link" href="{html.escape(view.href, quote=True)}" '
                f'data-view="{html.escape(view.key, quote=True)}">'
                f'<span>{html.escape(view.label)}</span><b aria-hidden="true">›</b></a>'
            )
            for view in views
            if view.group == group
        )
        nav_groups.append(
            f'<section class="nav-group"><p>{html.escape(group)}</p>{links}</section>'
        )

    latest_date = latest_entry.record_date if latest_entry else "尚无记录"
    latest_kcal = latest_entry.kcal if latest_entry else "—"
    latest_protein = latest_entry.protein if latest_entry else "—"
    view_payload = json.dumps(
        {view.key: view.payload() for view in views},
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Local HealthLog · 健康总览</title>
  <style>
    :root {{ color-scheme:light; --ink:#17221b; --muted:#68736c; --paper:#f2f0e9; --panel:#fffdf8; --line:#dce2da; --green:#185b43; --green2:#287a5c; --soft:#e6f0e9; --amber:#9a6414; }}
    * {{ box-sizing:border-box }} html {{ scroll-behavior:smooth }} body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif }}
    a {{ color:inherit }} .shell {{ min-height:100vh; display:grid; grid-template-columns:280px minmax(0,1fr) }}
    aside {{ position:sticky; top:0; height:100vh; overflow:auto; padding:28px 22px; color:#ecf5ef; background:linear-gradient(165deg,#123d2f,#1d6048 62%,#174936) }}
    .brand {{ display:flex; align-items:center; gap:12px; margin-bottom:28px }} .mark {{ display:grid; place-items:center; width:42px; height:42px; border-radius:14px; background:#f2c76e; color:#173d30; font-weight:900 }}
    .brand strong {{ display:block; font-size:17px }} .brand span {{ display:block; color:#bed6c8; font-size:12px }}
    .nav-home,.nav-link {{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:11px 13px; border-radius:12px; text-decoration:none; transition:.18s ease }}
    .nav-home {{ margin-bottom:18px; background:rgba(255,255,255,.08) }} .nav-group {{ margin:18px 0 }} .nav-group>p {{ margin:0 0 6px 12px; color:#a9c9b8; font-size:11px; font-weight:800; letter-spacing:.12em; text-transform:uppercase }}
    .nav-link {{ color:#dcebe2 }} .nav-link:hover,.nav-link.active,.nav-home.active {{ background:#f4f0df; color:#173d30; transform:translateX(2px) }} .nav-link b {{ font-size:20px }}
    .privacy {{ margin-top:26px; padding:13px; border:1px solid rgba(255,255,255,.14); border-radius:14px; color:#bfd5c8; font-size:12px }}
    main {{ min-width:0; padding:34px clamp(18px,4vw,56px) 60px }} .topbar {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:24px }}
    .eyebrow {{ margin:0 0 5px; color:var(--green2); font-size:12px; font-weight:850; letter-spacing:.13em; text-transform:uppercase }} h1 {{ margin:0; font-size:clamp(34px,5vw,62px); line-height:1.05; letter-spacing:-.045em }}
    .status {{ display:inline-flex; align-items:center; gap:7px; padding:8px 11px; border:1px solid #cbd9cf; border-radius:999px; background:#edf5ef; color:var(--green); white-space:nowrap }} .status:before {{ content:""; width:8px; height:8px; border-radius:50%; background:#37a36f }}
    .hero {{ padding:clamp(24px,4vw,42px); border-radius:28px; background:linear-gradient(135deg,#fffdf8,#edf4ed); border:1px solid var(--line); box-shadow:0 18px 50px rgba(32,55,43,.07) }} .hero p {{ max-width:760px; color:var(--muted); font-size:17px }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-top:26px }} .metric {{ padding:18px; border:1px solid var(--line); border-radius:17px; background:rgba(255,255,255,.72) }} .metric span {{ color:var(--muted); font-size:12px }} .metric strong {{ display:block; margin-top:4px; font-size:21px }}
    .layers {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-top:22px }} .layer {{ padding:21px; border:1px solid var(--line); border-radius:19px; background:var(--panel) }} .layer i {{ display:grid; place-items:center; width:34px; height:34px; border-radius:11px; background:var(--soft); color:var(--green); font-style:normal; font-weight:900 }} .layer h2 {{ margin:13px 0 4px; font-size:18px }} .layer p {{ margin:0; color:var(--muted) }}
    #viewer[hidden],#overview[hidden] {{ display:none }} .viewer-head {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-end; margin-bottom:15px }} .viewer-head h2 {{ margin:0; font-size:30px }} .viewer-head p {{ margin:5px 0 0; color:var(--muted) }} .open-link {{ padding:9px 13px; border:1px solid var(--line); border-radius:12px; background:var(--panel); text-decoration:none; white-space:nowrap }}
    iframe {{ display:block; width:100%; min-height:calc(100vh - 165px); border:1px solid var(--line); border-radius:22px; background:white; box-shadow:0 14px 40px rgba(28,48,38,.06) }} footer {{ margin-top:18px; color:var(--muted); font-size:12px }}
    @media(max-width:980px) {{ .shell {{ grid-template-columns:1fr }} aside {{ position:relative; height:auto; padding:18px }} .brand,.privacy {{ display:none }} nav {{ display:flex; gap:8px; overflow-x:auto; padding-bottom:4px }} .nav-home,.nav-group {{ flex:0 0 auto; margin:0 }} .nav-group {{ display:flex; gap:6px }} .nav-group>p {{ display:none }} .nav-link,.nav-home {{ white-space:nowrap; background:rgba(255,255,255,.08) }} .nav-link b {{ display:none }} main {{ padding-top:24px }} }}
    @media(max-width:700px) {{ .topbar,.viewer-head {{ align-items:flex-start; flex-direction:column }} .metrics,.layers {{ grid-template-columns:1fr 1fr }} iframe {{ min-height:72vh }} }}
    @media(max-width:470px) {{ .metrics,.layers {{ grid-template-columns:1fr }} h1 {{ font-size:38px }} }}
  </style>
</head>
<body>
<div class="shell">
  <aside>
    <div class="brand"><div class="mark">H</div><div><strong>Local HealthLog</strong><span>个人健康工作台</span></div></div>
    <nav aria-label="健康报告导航">
      <a class="nav-home active" href="#overview" data-view="overview"><span>总览</span><b aria-hidden="true">⌂</b></a>
      {"".join(nav_groups)}
    </nav>
    <div class="privacy">仅在本机读取。原始记录位于 data，机器状态位于 runtime，网页与网页图片统一位于 site。</div>
  </aside>
  <main>
    <div class="topbar"><div><p class="eyebrow">Personal health workspace</p><h1>健康总览</h1></div><span class="status">本地运行</span></div>
    <section id="overview">
      <div class="hero">
        <p class="eyebrow">Latest verified record</p>
        <h1>{html.escape(latest_date)}</h1>
        <p>从 Apple Photos 导出、逐图核对、区间估算到静态报告均保留证据来源。切换左侧栏目查看细节；页面不会加载外部脚本、字体或图片。</p>
        <div class="metrics">
          <div class="metric"><span>已完成日期</span><strong>{daily_count} 天</strong></div>
          <div class="metric"><span>最近热量</span><strong>{html.escape(latest_kcal)}</strong></div>
          <div class="metric"><span>最近蛋白质</span><strong>{html.escape(latest_protein)}</strong></div>
          <div class="metric"><span>可用报告</span><strong>{len(views)} 个</strong></div>
        </div>
      </div>
      <div class="layers">
        <article class="layer"><i>1</i><h2>健康计划</h2><p>个人目标、补剂营养表、保留或停用建议及使用方式。</p></article>
        <article class="layer"><i>2</i><h2>每日证据</h2><p>原始照片留在 data；内部清单位于 runtime；网页和预览在 site 展示。</p></article>
        <article class="layer"><i>3</i><h2>长期趋势</h2><p>按有记录日期计算 7/30 天区间，不把缺失日当作零。</p></article>
      </div>
      <footer>更新于 {html.escape(generated)} · 个人记录与建议不替代医疗诊断。</footer>
    </section>
    <section id="viewer" hidden>
      <div class="viewer-head"><div><p class="eyebrow" id="view-group"></p><h2 id="view-title"></h2><p id="view-description"></p></div><a class="open-link" id="open-view" href="#">单独打开 ↗</a></div>
      <iframe id="report-frame" title="健康报告"></iframe>
    </section>
  </main>
</div>
<script>
  const views={view_payload};
  const overview=document.getElementById("overview");
  const viewer=document.getElementById("viewer");
  const frame=document.getElementById("report-frame");
  const links=[...document.querySelectorAll("[data-view]")];
  function selectView(key){{
    links.forEach(link=>link.classList.toggle("active",link.dataset.view===key));
    if(key==="overview"||!views[key]){{
      overview.hidden=false; viewer.hidden=true; frame.removeAttribute("src");
      document.title="Local HealthLog · 健康总览";
      if(location.hash!=="#overview") history.replaceState(null,"","#overview");
      return;
    }}
    const view=views[key]; overview.hidden=true; viewer.hidden=false;
    document.getElementById("view-group").textContent=view.group;
    document.getElementById("view-title").textContent=view.title;
    document.getElementById("view-description").textContent=view.description;
    const open=document.getElementById("open-view"); open.href=view.href;
    if(frame.getAttribute("src")!==view.href) frame.src=view.href;
    document.title=view.title+" · Local HealthLog";
    if(location.hash!=="#"+key) history.replaceState(null,"","#"+key);
  }}
  links.forEach(link=>link.addEventListener("click",event=>{{event.preventDefault();selectView(link.dataset.view)}}));
  window.addEventListener("hashchange",()=>selectView(location.hash.slice(1)||"overview"));
  selectView(location.hash.slice(1)||"overview");
</script>
</body>
</html>
"""
    return page


def update_dashboard(profile: dict[str, Any]) -> Path:
    paths = paths_for(date.today(), profile)
    paths.site_root.mkdir(parents=True, exist_ok=True)
    daily_dirs = sorted(
        (
            path
            for path in paths.daily_site_root.glob("[0-9]" * 8)
            if path.is_dir() and (path / REPORT_HTML_NAME).is_file()
        ),
        reverse=True,
    )
    latest_paths = (
        paths_for(datetime.strptime(daily_dirs[0].name, "%Y%m%d").date(), profile)
        if daily_dirs
        else None
    )
    latest_entry = rendered_entry(latest_paths) if latest_paths else None
    report_dir = nutrition_site_dir(profile)
    views = _dashboard_views(
        paths.site_root,
        latest_entry,
        latest_paths.report_html if latest_paths else None,
        paths.daily_index_html,
        newest_report(report_dir, "7d"),
        newest_report(report_dir, "30d"),
    )
    page = _render_dashboard_page(
        latest_entry,
        len(daily_dirs),
        views,
        datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    atomic_write_text(paths.dashboard, page)
    return paths.dashboard
