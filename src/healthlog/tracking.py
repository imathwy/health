"""Daily health observations and derived meal-level tracking metrics."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .nutrition import CORE_NUTRIENTS, nutrient_unit


OBSERVATION_DEFINITIONS: dict[str, tuple[str, str]] = {
    "direct_water_ml": ("直接饮水", "mL"),
    "calcium_mg": ("钙", "mg"),
    "sleep_hours": ("睡眠", "h"),
    "caffeine_mg": ("咖啡因", "mg"),
    "training_minutes": ("训练时长", "min"),
    "session_rpe": ("训练自感强度", "/10"),
    "vegetables_g": ("蔬菜", "g"),
    "fruit_g": ("水果", "g"),
}

BODY_DEFINITIONS: dict[str, tuple[str, str]] = {
    "weight_kg": ("体重", "kg"),
    "waist_cm": ("腰围", "cm"),
    "chest_cm": ("胸围", "cm"),
    "upper_arm_cm": ("上臂围", "cm"),
    "thigh_cm": ("大腿围", "cm"),
}

MEAL_TRACKING_TAGS = {"heme_iron", "oily_fish"}
TRACKING_SOURCES = {
    "user_reported",
    "measured",
    "photo_review",
    "derived_from_items",
    "package_label",
    "wearable",
    "unknown",
}
TRACKING_COVERAGE = {"complete", "partial", "unknown"}
IRON_CALCIUM_STATUSES = {
    "unknown",
    "not_applicable_no_iron_supplement",
    "food_only",
    "supplements_separated",
    "potential_supplement_overlap",
}

DEFAULT_TRACKING_TARGETS: dict[str, Any] = {
    "protein_per_meal_g": [20, 40],
    "direct_water_ml_base": 1700,
    "calcium_mg_target": 1000,
    "sleep_hours_min": 7,
    "caffeine_mg_max": 400,
    "vegetables_g_min": 300,
    "fruit_g_min": 200,
}

TRACKING_TARGET_SPECS: dict[str, tuple[str, str]] = {
    "direct_water_ml": ("direct_water_ml_base", "minimum"),
    "calcium_mg": ("calcium_mg_target", "minimum"),
    "sleep_hours": ("sleep_hours_min", "minimum"),
    "caffeine_mg": ("caffeine_mg_max", "maximum"),
    "vegetables_g": ("vegetables_g_min", "minimum"),
    "fruit_g": ("fruit_g_min", "minimum"),
}

IRON_CALCIUM_LABELS = {
    "unknown": "未记录",
    "not_applicable_no_iron_supplement": "未使用单独铁剂",
    "food_only": "仅普通膳食同餐",
    "supplements_separated": "单独铁剂与钙剂已错开",
    "potential_supplement_overlap": "单独铁剂与钙剂可能同服",
}


def _observation_template() -> dict[str, Any]:
    return {
        "range": None,
        "source": "unknown",
        "coverage": "unknown",
        "notes": [],
    }


def _measurement_template() -> dict[str, Any]:
    return {
        "value": None,
        "source": "unknown",
        "recorded_at": None,
        "context": "",
        "notes": [],
    }


def tracking_template() -> dict[str, Any]:
    """Return a complete schema-v3 tracking block with explicit missingness."""

    return {
        "observations": {
            key: _observation_template() for key in OBSERVATION_DEFINITIONS
        },
        "last_caffeine_time": None,
        "meal_tagging": {
            "source": "unknown",
            "coverage": "unknown",
            "notes": [],
        },
        "iron_calcium_timing": {
            "status": "unknown",
            "source": "unknown",
            "notes": [],
        },
        "body_measurements": {
            key: _measurement_template() for key in BODY_DEFINITIONS
        },
    }


def tracking_targets(profile: dict[str, Any]) -> dict[str, Any]:
    """Merge optional local tracking targets onto conservative public defaults."""

    configured = profile.get("targets", {}).get("tracking", {})
    if not isinstance(configured, dict):
        configured = {}
    return {**DEFAULT_TRACKING_TARGETS, **configured}


def effective_tracking(analysis: dict[str, Any]) -> dict[str, Any]:
    """Return tracking with safe derivations filled, leaving unknowns explicit."""

    raw = analysis.get("tracking")
    tracking = deepcopy(raw) if isinstance(raw, dict) else tracking_template()
    observations = tracking.get("observations", {})
    calcium = observations.get("calcium_mg", {}) if isinstance(observations, dict) else {}
    if isinstance(calcium, dict) and calcium.get("range") is None:
        low = high = 0.0
        covered_items = total_items = 0
        for meal in analysis.get("meals", []):
            for item in meal.get("items", []):
                total_items += 1
                value = item.get("optional_nutrients", {}).get("calcium_mg")
                if _valid_numeric_range(value):
                    low += float(value[0])
                    high += float(value[1])
                    covered_items += 1
        if covered_items:
            photo_complete = (
                analysis.get("day_context", {}).get("photo_coverage") == "complete"
            )
            calcium.update(
                {
                    "range": [low, high],
                    "source": "derived_from_items",
                    "coverage": (
                        "complete"
                        if covered_items == total_items and photo_complete
                        else "partial"
                    ),
                    "notes": [
                        f"由 {covered_items}/{total_items} 个食物条目的 calcium_mg 汇总"
                    ],
                }
            )
    return tracking


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_numeric_range(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(_is_number(number) for number in value)
    )


def _validate_notes(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{label} 必须是字符串数组")


def _validate_range_or_null(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or len(value) != 2:
        errors.append(f"{label} 必须是 null 或 [最小值, 最大值]")
        return
    if not all(_is_number(item) for item in value):
        errors.append(f"{label} 必须包含两个数字")
        return
    if value[0] < 0 or value[1] < 0 or value[0] > value[1]:
        errors.append(f"{label} 范围无效：{value}")


def _validate_source(value: Any, label: str, errors: list[str]) -> None:
    if value not in TRACKING_SOURCES:
        errors.append(f"{label} 无效：{value!r}")


def _validate_coverage(value: Any, label: str, errors: list[str]) -> None:
    if value not in TRACKING_COVERAGE:
        errors.append(f"{label} 无效：{value!r}")


def validate_meal_tracking_tags(
    meal: dict[str, Any], prefix: str, errors: list[str]
) -> None:
    if not isinstance(meal.get("protein_target_applicable"), bool):
        errors.append(f"{prefix}.protein_target_applicable 必须是布尔值")
    tags = meal.get("tracking_tags")
    if not isinstance(tags, list):
        errors.append(f"{prefix}.tracking_tags 必须是数组")
        return
    if not all(isinstance(tag, str) for tag in tags):
        errors.append(f"{prefix}.tracking_tags 只能包含字符串")
        return
    duplicates = sorted({tag for tag in tags if tags.count(tag) > 1})
    if duplicates:
        errors.append(f"{prefix}.tracking_tags 重复：{', '.join(duplicates)}")
    unknown = sorted(set(tags) - MEAL_TRACKING_TAGS)
    if unknown:
        errors.append(f"{prefix}.tracking_tags 无效：{', '.join(unknown)}")


def validate_tracking(
    tracking: Any, errors: list[str], warnings: list[str]
) -> None:
    """Validate the schema-v3 tracking block without treating missing as zero."""

    if not isinstance(tracking, dict):
        errors.append("tracking 必须是对象")
        return

    observations = tracking.get("observations")
    if not isinstance(observations, dict):
        errors.append("tracking.observations 必须是对象")
        observations = {}
    missing_observations = sorted(set(OBSERVATION_DEFINITIONS) - set(observations))
    unknown_observations = sorted(set(observations) - set(OBSERVATION_DEFINITIONS))
    if missing_observations:
        errors.append(
            "tracking.observations 缺少字段：" + ", ".join(missing_observations)
        )
    if unknown_observations:
        errors.append(
            "tracking.observations 含未知字段：" + ", ".join(unknown_observations)
        )

    unknown_count = 0
    for key in OBSERVATION_DEFINITIONS:
        row = observations.get(key)
        prefix = f"tracking.observations.{key}"
        if not isinstance(row, dict):
            if key in observations:
                errors.append(f"{prefix} 必须是对象")
            continue
        _validate_range_or_null(row.get("range"), f"{prefix}.range", errors)
        _validate_source(row.get("source"), f"{prefix}.source", errors)
        _validate_coverage(row.get("coverage"), f"{prefix}.coverage", errors)
        _validate_notes(row.get("notes"), f"{prefix}.notes", errors)
        if row.get("range") is None:
            unknown_count += 1

    rpe = observations.get("session_rpe", {})
    if isinstance(rpe, dict) and isinstance(rpe.get("range"), list):
        if len(rpe["range"]) == 2 and all(_is_number(x) for x in rpe["range"]):
            if rpe["range"][1] > 10:
                errors.append("tracking.observations.session_rpe.range 不能超过 10")
    sleep = observations.get("sleep_hours", {})
    if isinstance(sleep, dict) and isinstance(sleep.get("range"), list):
        if len(sleep["range"]) == 2 and all(_is_number(x) for x in sleep["range"]):
            if sleep["range"][1] > 24:
                errors.append("tracking.observations.sleep_hours.range 不能超过 24")

    last_caffeine = tracking.get("last_caffeine_time")
    if last_caffeine is not None and (
        not isinstance(last_caffeine, str)
        or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", last_caffeine) is None
    ):
        errors.append("tracking.last_caffeine_time 必须是 null 或 HH:MM")

    meal_tagging = tracking.get("meal_tagging")
    if not isinstance(meal_tagging, dict):
        errors.append("tracking.meal_tagging 必须是对象")
    else:
        _validate_source(
            meal_tagging.get("source"), "tracking.meal_tagging.source", errors
        )
        _validate_coverage(
            meal_tagging.get("coverage"), "tracking.meal_tagging.coverage", errors
        )
        _validate_notes(
            meal_tagging.get("notes"), "tracking.meal_tagging.notes", errors
        )

    timing = tracking.get("iron_calcium_timing")
    if not isinstance(timing, dict):
        errors.append("tracking.iron_calcium_timing 必须是对象")
    else:
        if timing.get("status") not in IRON_CALCIUM_STATUSES:
            errors.append(
                "tracking.iron_calcium_timing.status 无效："
                f"{timing.get('status')!r}"
            )
        _validate_source(
            timing.get("source"), "tracking.iron_calcium_timing.source", errors
        )
        _validate_notes(
            timing.get("notes"), "tracking.iron_calcium_timing.notes", errors
        )

    measurements = tracking.get("body_measurements")
    if not isinstance(measurements, dict):
        errors.append("tracking.body_measurements 必须是对象")
        measurements = {}
    missing_measurements = sorted(set(BODY_DEFINITIONS) - set(measurements))
    unknown_measurements = sorted(set(measurements) - set(BODY_DEFINITIONS))
    if missing_measurements:
        errors.append(
            "tracking.body_measurements 缺少字段：" + ", ".join(missing_measurements)
        )
    if unknown_measurements:
        errors.append(
            "tracking.body_measurements 含未知字段：" + ", ".join(unknown_measurements)
        )
    for key in BODY_DEFINITIONS:
        row = measurements.get(key)
        prefix = f"tracking.body_measurements.{key}"
        if not isinstance(row, dict):
            if key in measurements:
                errors.append(f"{prefix} 必须是对象")
            continue
        value = row.get("value")
        if value is not None and (not _is_number(value) or value <= 0):
            errors.append(f"{prefix}.value 必须是正数或 null")
        _validate_source(row.get("source"), f"{prefix}.source", errors)
        recorded_at = row.get("recorded_at")
        if recorded_at is not None and not isinstance(recorded_at, str):
            errors.append(f"{prefix}.recorded_at 必须是字符串或 null")
        if not isinstance(row.get("context"), str):
            errors.append(f"{prefix}.context 必须是字符串")
        _validate_notes(row.get("notes"), f"{prefix}.notes", errors)

    if unknown_count:
        warnings.append(f"扩展追踪指标有 {unknown_count} 项未记录；未知值不会按 0 处理")


def meal_nutrition_totals(meal: dict[str, Any]) -> dict[str, list[float]]:
    totals = {key: [0.0, 0.0] for key in CORE_NUTRIENTS}
    for item in meal.get("items", []):
        nutrition = item.get("nutrition", {})
        for nutrient in CORE_NUTRIENTS:
            value = nutrition.get(nutrient)
            if not isinstance(value, list) or len(value) != 2:
                continue
            totals[nutrient][0] += float(value[0])
            totals[nutrient][1] += float(value[1])
    return totals


def _interval_status(value: list[float], target: list[float]) -> str:
    if value[1] < target[0]:
        return "低于参考"
    if value[0] > target[1]:
        return "高于参考"
    if value[0] >= target[0] and value[1] <= target[1]:
        return "目标内"
    return "区间重叠"


def _threshold_status(
    value: list[float], threshold: float, direction: str, coverage: str
) -> str:
    if coverage != "complete":
        if direction == "minimum" and value[0] >= threshold:
            return "至少达标"
        return "覆盖不全"
    if direction == "minimum":
        if value[1] < threshold:
            return "偏低"
        if value[0] >= threshold:
            return "目标内"
        return "区间重叠"
    if value[0] > threshold:
        return "偏高"
    if value[1] <= threshold:
        return "目标内"
    return "可能偏高"


def daily_observation_rows(
    analysis: dict[str, Any], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    configured_targets = tracking_targets(profile)
    tracking = effective_tracking(analysis)
    observations = tracking.get("observations", {}) if isinstance(tracking, dict) else {}
    result: list[dict[str, Any]] = []
    for key, (label, unit) in OBSERVATION_DEFINITIONS.items():
        row = observations.get(key, {}) if isinstance(observations, dict) else {}
        value = row.get("range") if isinstance(row, dict) else None
        coverage = str(row.get("coverage", "unknown")) if isinstance(row, dict) else "unknown"
        source = str(row.get("source", "unknown")) if isinstance(row, dict) else "unknown"
        target_spec = TRACKING_TARGET_SPECS.get(key)
        if target_spec is None:
            target_text = "记录项"
            status = "已记录" if isinstance(value, list) else "未知"
        else:
            target_key, direction = target_spec
            threshold = float(configured_targets[target_key])
            symbol = "≥" if direction == "minimum" else "≤"
            target_text = f"{symbol}{threshold:g} {unit}"
            status = (
                _threshold_status(value, threshold, direction, coverage)
                if isinstance(value, list) and len(value) == 2
                else "未知"
            )
        result.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "range": value,
                "target": target_text,
                "status": status,
                "coverage": coverage,
                "source": source,
                "notes": list(row.get("notes", [])) if isinstance(row, dict) else [],
            }
        )
    return result


def body_measurement_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    tracking = effective_tracking(analysis)
    measurements = (
        tracking.get("body_measurements", {}) if isinstance(tracking, dict) else {}
    )
    result = []
    for key, (label, unit) in BODY_DEFINITIONS.items():
        row = measurements.get(key, {}) if isinstance(measurements, dict) else {}
        result.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "value": row.get("value") if isinstance(row, dict) else None,
                "source": str(row.get("source", "unknown")) if isinstance(row, dict) else "unknown",
                "recorded_at": row.get("recorded_at") if isinstance(row, dict) else None,
                "context": str(row.get("context", "")) if isinstance(row, dict) else "",
                "notes": list(row.get("notes", [])) if isinstance(row, dict) else [],
            }
        )
    return result


def iron_calcium_row(analysis: dict[str, Any]) -> dict[str, Any]:
    tracking = effective_tracking(analysis)
    timing = (
        tracking.get("iron_calcium_timing", {}) if isinstance(tracking, dict) else {}
    )
    status = str(timing.get("status", "unknown")) if isinstance(timing, dict) else "unknown"
    return {
        "status": status,
        "label": IRON_CALCIUM_LABELS.get(status, status),
        "source": str(timing.get("source", "unknown")) if isinstance(timing, dict) else "unknown",
        "notes": list(timing.get("notes", [])) if isinstance(timing, dict) else [],
    }


def meal_protein_rows(
    analysis: dict[str, Any], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    target = tracking_targets(profile)["protein_per_meal_g"]
    rows = []
    for meal in analysis.get("meals", []):
        protein = meal_nutrition_totals(meal)["protein_g"]
        applicable = bool(meal.get("protein_target_applicable", True))
        rows.append(
            {
                "meal_id": str(meal.get("id", "")),
                "label": str(meal.get("label", meal.get("id", ""))),
                "time": str(meal.get("time") or ""),
                "range": protein,
                "target": list(target),
                "target_applicable": applicable,
                "status": (
                    _interval_status(protein, list(target))
                    if applicable
                    else "观察项"
                ),
            }
        )
    return rows


def known_observation_rows(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tracking = effective_tracking(analysis)
    rows = tracking.get("observations", {}) if isinstance(tracking, dict) else {}
    return {
        key: row
        for key, row in rows.items()
        if key in OBSERVATION_DEFINITIONS
        and isinstance(row, dict)
        and isinstance(row.get("range"), list)
        and len(row["range"]) == 2
    }


def meal_tag_counts(analysis: dict[str, Any]) -> dict[str, int]:
    counts = {tag: 0 for tag in MEAL_TRACKING_TAGS}
    for meal in analysis.get("meals", []):
        for tag in set(meal.get("tracking_tags", [])):
            if tag in counts:
                counts[tag] += 1
    return counts


def metric_unit(metric: str) -> str:
    if metric in OBSERVATION_DEFINITIONS:
        return OBSERVATION_DEFINITIONS[metric][1]
    if metric in BODY_DEFINITIONS:
        return BODY_DEFINITIONS[metric][1]
    return nutrient_unit(metric)
