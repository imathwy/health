"""Longitudinal nutrition summaries that preserve estimate intervals."""

from __future__ import annotations

import html
import json
from datetime import date, timedelta
from typing import Any

from .nutrition import CORE_NUTRIENTS, average_ranges, confidence_counts


NUTRIENT_LABELS = {
    "kcal": "热量",
    "protein_g": "蛋白质",
    "carbohydrate_g": "碳水化合物",
    "fat_g": "脂肪",
    "fiber_g": "膳食纤维",
    "sodium_mg": "钠",
}


def _clean(value: float) -> int | float:
    rounded = round(float(value), 1)
    return int(rounded) if rounded.is_integer() else rounded


def display_interval(low: float, high: float, unit: str) -> str:
    clean_low, clean_high = _clean(low), _clean(high)
    if clean_low == clean_high:
        return f"{clean_low} {unit}"
    return f"{clean_low}–{clean_high} {unit}"


def _slope(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    denominator = sum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0:
        return 0.0
    return (
        sum((point[0] - mean_x) * (point[1] - mean_y) for point in points) / denominator
    )


def interval_trend(
    rows: list[dict[str, Any]], nutrient: str, start: date
) -> dict[str, Any]:
    usable = [row for row in rows if nutrient in row.get("nutrients", {})]
    if len(usable) < 5:
        return {
            "status": "insufficient_data",
            "label": "数据不足",
            "logged_days": len(usable),
            "method": "至少需要 5 个有记录日期",
        }
    low_points: list[tuple[float, float]] = []
    high_points: list[tuple[float, float]] = []
    for row in usable:
        x = float((date.fromisoformat(row["date"]) - start).days)
        value = row["nutrients"][nutrient]
        low_points.append((x, float(value["low"])))
        high_points.append((x, float(value["high"])))
    low_slope = _slope(low_points)
    high_slope = _slope(high_points)
    average_high = sum(point[1] for point in high_points) / len(high_points)
    tolerance = max(average_high * 0.005, 0.05)
    if low_slope > tolerance and high_slope > tolerance:
        status, label = "increasing", "区间上下界均上升"
    elif low_slope < -tolerance and high_slope < -tolerance:
        status, label = "decreasing", "区间上下界均下降"
    elif abs(low_slope) <= tolerance and abs(high_slope) <= tolerance:
        status, label = "stable", "区间上下界大致稳定"
    else:
        status, label = "uncertain", "上下界方向不一致"
    return {
        "status": status,
        "label": label,
        "logged_days": len(usable),
        "low_slope_per_day": round(low_slope, 3),
        "high_slope_per_day": round(high_slope, 3),
        "unit": usable[0]["nutrients"][nutrient]["unit"],
        "method": "分别对估算区间的下界和上界做线性趋势；没有把区间中点当成实测值",
    }


def make_summary(
    *,
    rows: list[dict[str, Any]],
    start: date,
    end: date,
    requested_days: int,
    provenance: dict[str, int],
) -> dict[str, Any]:
    expected_dates = [
        (start + timedelta(days=offset)).isoformat() for offset in range(requested_days)
    ]
    logged_dates = {row["date"] for row in rows}
    missing_dates = [value for value in expected_dates if value not in logged_dates]
    status_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        for comparison in row.get("comparisons", []):
            label = str(comparison.get("label", "unknown"))
            status = str(comparison.get("status", "unknown"))
            status_counts.setdefault(label, {})[status] = (
                status_counts.setdefault(label, {}).get(status, 0) + 1
            )
    averages = average_ranges(rows)
    trends = {
        nutrient: interval_trend(rows, nutrient, start) for nutrient in CORE_NUTRIENTS
    }
    return {
        "schema_version": 1,
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "requested_days": requested_days,
            "logged_days": len(rows),
            "coverage_ratio": round(len(rows) / requested_days, 4),
            "missing_dates": missing_dates,
        },
        "average_daily_ranges": averages,
        "target_status_counts": status_counts,
        "confidence_counts": confidence_counts(rows),
        "nutrition_source_counts": provenance,
        "trends": trends,
        "days": [
            {
                "date": row["date"],
                "day_type": row["day_type"],
                "photo_coverage": row["photo_coverage"],
                "overall_confidence": row["overall_confidence"],
                "nutrients": {
                    key: value
                    for key, value in row["nutrients"].items()
                    if key in CORE_NUTRIENTS
                },
            }
            for row in rows
        ],
        "interpretation_limits": [
            "平均值按有记录日期计算，不会把缺失日期当作零摄入。",
            "每日数据来自照片和份量区间；趋势分别使用下界和上界。",
            "照片覆盖不完整时，汇总仍可能低估全天摄入。",
            "少于 5 个有记录日期时不判断趋势方向。",
        ],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    period = summary["period"]
    lines = [
        f"# {period['end']} · {period['requested_days']} 天营养汇总",
        "",
        (
            f"> 范围 {period['start']} 至 {period['end']}；"
            f"记录 {period['logged_days']}/{period['requested_days']} 天。"
            "平均值只按有记录日期计算。"
        ),
        "",
        "## 每日平均估算区间",
        "",
        "| 营养素 | 日均估算 | 有记录天数 | 区间趋势 |",
        "|---|---:|---:|---|",
    ]
    averages = summary["average_daily_ranges"]
    for nutrient in CORE_NUTRIENTS:
        average = averages.get(nutrient)
        if not average:
            continue
        trend = summary["trends"][nutrient]
        lines.append(
            f"| {NUTRIENT_LABELS[nutrient]} | "
            f"{display_interval(average['low'], average['high'], average['unit'])} | "
            f"{average['logged_days']} | {trend['label']} |"
        )

    lines.extend(
        [
            "",
            "## 每日目标判断次数",
            "",
            "| 营养素 | 目标内 | 区间重叠 | 偏低 | 偏高 | 可能偏高 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    target_counts = summary.get("target_status_counts", {})
    for label, counts in target_counts.items():
        lines.append(
            f"| {label} | {counts.get('目标内', 0)} | "
            f"{counts.get('区间重叠', 0)} | {counts.get('偏低', 0)} | "
            f"{counts.get('偏高', 0)} | {counts.get('可能偏高', 0)} |"
        )
    if not target_counts:
        lines.append("| — | 0 | 0 | 0 | 0 | 0 |")

    lines.extend(
        [
            "",
            "## 每日记录",
            "",
            "| 日期 | 日型 | 热量 | 蛋白质 | 照片覆盖 | 置信度 |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    for day in summary["days"]:
        kcal = day["nutrients"].get("kcal")
        protein = day["nutrients"].get("protein_g")
        lines.append(
            f"| {day['date']} | {day['day_type']} | "
            f"{display_interval(kcal['low'], kcal['high'], kcal['unit']) if kcal else '—'} | "
            f"{display_interval(protein['low'], protein['high'], protein['unit']) if protein else '—'} | "
            f"{day['photo_coverage']} | {day['overall_confidence']} |"
        )
    if not summary["days"]:
        lines.append("| — | — | — | — | — | 尚无记录 |")

    lines.extend(["", "## 数据来源", ""])
    sources = summary.get("nutrition_source_counts", {})
    if sources:
        lines.extend(
            f"- `{source}`：{count} 个食物条目" for source, count in sources.items()
        )
    else:
        lines.append("- 尚无食物条目")

    missing = period.get("missing_dates", [])
    lines.extend(["", "## 覆盖与限制", ""])
    lines.append(f"- 缺失 {len(missing)} 天：{', '.join(missing) if missing else '无'}")
    lines.extend(f"- {item}" for item in summary["interpretation_limits"])
    lines.extend(["", "本汇总用于个人饮食记录，不替代医疗诊断或营养处方。", ""])
    return "\n".join(lines)


def render_html(summary: dict[str, Any]) -> str:
    period = summary["period"]
    averages = summary["average_daily_ranges"]
    average_rows = []
    for nutrient in CORE_NUTRIENTS:
        average = averages.get(nutrient)
        if not average:
            continue
        trend = summary["trends"][nutrient]
        average_rows.append(
            "<tr>"
            f"<td>{html.escape(NUTRIENT_LABELS[nutrient])}</td>"
            f"<td>{html.escape(display_interval(average['low'], average['high'], average['unit']))}</td>"
            f"<td>{average['logged_days']}</td>"
            f"<td>{html.escape(trend['label'])}</td>"
            "</tr>"
        )
    day_rows = []
    for day in summary["days"]:
        kcal = day["nutrients"].get("kcal")
        protein = day["nutrients"].get("protein_g")
        kcal_text = (
            display_interval(kcal["low"], kcal["high"], kcal["unit"]) if kcal else "—"
        )
        protein_text = (
            display_interval(protein["low"], protein["high"], protein["unit"])
            if protein
            else "—"
        )
        day_rows.append(
            "<tr>"
            f"<td>{html.escape(day['date'])}</td><td>{html.escape(day['day_type'])}</td>"
            f"<td>{html.escape(kcal_text)}</td><td>{html.escape(protein_text)}</td>"
            f"<td>{html.escape(day['photo_coverage'])}</td>"
            f"<td>{html.escape(day['overall_confidence'])}</td>"
            "</tr>"
        )
    if not day_rows:
        day_rows.append('<tr><td colspan="6">尚无记录</td></tr>')
    target_rows = []
    for label, counts in summary.get("target_status_counts", {}).items():
        target_rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{counts.get('目标内', 0)}</td>"
            f"<td>{counts.get('区间重叠', 0)}</td>"
            f"<td>{counts.get('偏低', 0)}</td>"
            f"<td>{counts.get('偏高', 0)}</td>"
            f"<td>{counts.get('可能偏高', 0)}</td>"
            "</tr>"
        )
    if not target_rows:
        target_rows.append('<tr><td colspan="6">尚无目标判断</td></tr>')
    source_cards = (
        "".join(
            f"<li><code>{html.escape(source)}</code><strong>{count}</strong></li>"
            for source, count in summary.get("nutrition_source_counts", {}).items()
        )
        or "<li>尚无食物条目</li>"
    )
    limits = "".join(
        f"<li>{html.escape(item)}</li>" for item in summary["interpretation_limits"]
    )
    missing = period.get("missing_dates", [])
    missing_text = ", ".join(missing) if missing else "无"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{period["end"]} · {period["requested_days"]} 天营养汇总</title>
<style>
:root{{--ink:#17221b;--muted:#647067;--paper:#f3f1e9;--panel:#fffdf8;--line:#dce2da;--green:#205f48;--soft:#e5f0e9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{width:min(1050px,calc(100% - 28px));margin:auto;padding:42px 0 70px}}header{{padding:32px;border-radius:26px;background:#1d4f3d;color:white}}h1{{font-size:clamp(32px,6vw,58px);line-height:1.08;margin:5px 0}}header p{{color:#dcebe3;margin:0}}.eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.14em;opacity:.75}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}}.metric,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:19px}}.metric{{padding:18px}}.metric strong{{display:block;font-size:28px}}.metric span{{color:var(--muted)}}.panel{{padding:24px;margin:18px 0}}h2{{margin-top:0}}table{{width:100%;border-collapse:collapse;min-width:650px}}th,td{{padding:11px 9px;border-bottom:1px solid var(--line);text-align:left}}th{{color:var(--muted);font-size:13px}}.scroll{{overflow:auto}}.sources{{display:grid;gap:8px;padding:0;list-style:none}}.sources li{{display:flex;justify-content:space-between;padding:10px 12px;background:var(--soft);border-radius:10px}}.muted{{color:var(--muted)}}@media(max-width:680px){{.metrics{{grid-template-columns:1fr}}.panel{{padding:18px}}}}
</style></head><body><main>
<header><div class="eyebrow">Longitudinal nutrition</div><h1>{period["requested_days"]} 天营养汇总</h1><p>{period["start"]} 至 {period["end"]} · 区间估算，不把中点当成实测值</p></header>
<section class="metrics"><div class="metric"><span>已记录</span><strong>{period["logged_days"]} / {period["requested_days"]} 天</strong></div><div class="metric"><span>覆盖率</span><strong>{round(period["coverage_ratio"] * 100, 1)}%</strong></div><div class="metric"><span>缺失日期</span><strong>{len(missing)} 天</strong></div></section>
<section class="panel"><h2>每日平均估算区间</h2><div class="scroll"><table><thead><tr><th>营养素</th><th>日均估算</th><th>有记录天数</th><th>区间趋势</th></tr></thead><tbody>{"".join(average_rows)}</tbody></table></div></section>
<section class="panel"><h2>每日目标判断次数</h2><div class="scroll"><table><thead><tr><th>营养素</th><th>目标内</th><th>区间重叠</th><th>偏低</th><th>偏高</th><th>可能偏高</th></tr></thead><tbody>{"".join(target_rows)}</tbody></table></div></section>
<section class="panel"><h2>每日记录</h2><div class="scroll"><table><thead><tr><th>日期</th><th>日型</th><th>热量</th><th>蛋白质</th><th>照片覆盖</th><th>置信度</th></tr></thead><tbody>{"".join(day_rows)}</tbody></table></div></section>
<section class="panel"><h2>营养估算来源</h2><ul class="sources">{source_cards}</ul></section>
<section class="panel"><h2>覆盖与限制</h2><p class="muted">缺失日期：{html.escape(missing_text)}</p><ul>{limits}</ul></section>
<p class="muted">本汇总用于个人饮食记录，不替代医疗诊断或营养处方。</p>
</main></body></html>"""


def json_text(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
