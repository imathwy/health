"""Longitudinal projections for source-aware health tracking observations."""

from __future__ import annotations

from datetime import date
from typing import Any

from .tracking import (
    BODY_DEFINITIONS,
    OBSERVATION_DEFINITIONS,
    TRACKING_TARGET_SPECS,
)


MEAL_TAG_LABELS = {
    "heme_iron": "确认含血红素铁来源的餐次",
    "oily_fish": "确认含油性鱼的餐次",
}


def _slope(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    denominator = sum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0:
        return 0.0
    numerator = sum(
        (point[0] - mean_x) * (point[1] - mean_y) for point in points
    )
    return numerator / denominator


def _valid_range(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(
            isinstance(number, (int, float)) and not isinstance(number, bool)
            for number in value
        )
    )


def tracking_observation_averages(
    rows: list[dict[str, Any]], targets: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for metric, (label, unit) in OBSERVATION_DEFINITIONS.items():
        available = []
        for row in rows:
            observation = (
                row.get("tracking", {}).get("observations", {}).get(metric, {})
            )
            value = observation.get("range") if isinstance(observation, dict) else None
            if _valid_range(value):
                available.append(observation)
        target_spec = TRACKING_TARGET_SPECS.get(metric)
        target_text = "记录项"
        if target_spec is not None:
            target_key, direction = target_spec
            threshold = float(targets[target_key])
            target_text = (
                f"{'≥' if direction == 'minimum' else '≤'}{threshold:g} {unit}"
            )
        result[metric] = {
            "label": label,
            "unit": unit,
            "low": (
                sum(float(item["range"][0]) for item in available) / len(available)
                if available
                else None
            ),
            "high": (
                sum(float(item["range"][1]) for item in available) / len(available)
                if available
                else None
            ),
            "logged_days": len(available),
            "complete_days": sum(
                item.get("coverage") == "complete" for item in available
            ),
            "target": target_text,
        }
    return result


def _protein_status(value: dict[str, Any], target: list[float]) -> str:
    low, high = float(value["low"]), float(value["high"])
    if high < target[0]:
        return "低于参考"
    if low > target[1]:
        return "高于参考"
    if low >= target[0] and high <= target[1]:
        return "目标内"
    return "区间重叠"


def meal_protein_distribution(
    rows: list[dict[str, Any]], target: list[float]
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    applicable_meals = 0
    for day in rows:
        for meal in day.get("meals", []):
            protein = meal.get("nutrients", {}).get("protein_g")
            if not isinstance(protein, dict):
                continue
            applicable = bool(meal.get("protein_target_applicable", True))
            status = _protein_status(protein, target) if applicable else "观察项"
            if applicable:
                applicable_meals += 1
                counts[status] = counts.get(status, 0) + 1
            entries.append(
                {
                    "date": day["date"],
                    "meal_id": meal.get("meal_id", ""),
                    "label": meal.get("label", meal.get("meal_id", "")),
                    "time": meal.get("meal_time", ""),
                    "low": float(protein["low"]),
                    "high": float(protein["high"]),
                    "unit": protein.get("unit", "g"),
                    "target_applicable": applicable,
                    "status": status,
                }
            )
    return {
        "target_g": list(target),
        "counts": counts,
        "applicable_meals": applicable_meals,
        "observed_meals": len(entries),
        "meals": entries,
    }


def meal_tag_frequency(
    rows: list[dict[str, Any]], requested_days: int
) -> dict[str, dict[str, Any]]:
    result = {
        tag: {
            "label": label,
            "confirmed_meals": 0,
            "confirmed_meals_per_week": 0.0,
            "complete_days": 0,
            "partial_days": 0,
            "unknown_days": 0,
        }
        for tag, label in MEAL_TAG_LABELS.items()
    }
    for day in rows:
        coverage = (
            day.get("tracking", {}).get("meal_tagging", {}).get("coverage", "unknown")
        )
        coverage = coverage if coverage in {"complete", "partial"} else "unknown"
        for tag in result:
            result[tag][f"{coverage}_days"] += 1
        for meal in day.get("meals", []):
            for tag in set(meal.get("tracking_tags", [])):
                if tag in result:
                    result[tag]["confirmed_meals"] += 1
    for frequency in result.values():
        frequency["confirmed_meals_per_week"] = round(
            frequency["confirmed_meals"] * 7 / requested_days, 2
        )
    return result


def body_measurement_changes(
    rows: list[dict[str, Any]], start: date
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for metric, (label, unit) in BODY_DEFINITIONS.items():
        values: list[tuple[date, float, dict[str, Any]]] = []
        for row in rows:
            measurement = (
                row.get("tracking", {}).get("body_measurements", {}).get(metric, {})
            )
            value = measurement.get("value") if isinstance(measurement, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(
                    (date.fromisoformat(row["date"]), float(value), measurement)
                )
        if not values:
            continue
        values.sort(key=lambda item: item[0])
        first, latest = values[0], values[-1]
        points = [((recorded - start).days, value) for recorded, value, _ in values]
        result[metric] = {
            "label": label,
            "unit": unit,
            "measurement_days": len(values),
            "first_date": first[0].isoformat(),
            "first": first[1],
            "latest_date": latest[0].isoformat(),
            "latest": latest[1],
            "change": latest[1] - first[1] if len(values) >= 2 else None,
            "slope_per_day": round(_slope(points), 4) if len(values) >= 5 else None,
            "contexts": sorted(
                {
                    str(measurement.get("context"))
                    for _, _, measurement in values
                    if measurement.get("context")
                }
            ),
        }
    return result


def iron_calcium_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(
            row.get("tracking", {})
            .get("iron_calcium_timing", {})
            .get("status", "unknown")
        )
        counts[status] = counts.get(status, 0) + 1
    return counts


def make_tracking_summary(
    *,
    rows: list[dict[str, Any]],
    start: date,
    requested_days: int,
    targets: dict[str, Any],
) -> dict[str, Any]:
    return {
        "observation_averages": tracking_observation_averages(rows, targets),
        "meal_protein": meal_protein_distribution(
            rows, list(targets["protein_per_meal_g"])
        ),
        "meal_frequencies": meal_tag_frequency(rows, requested_days),
        "iron_calcium_status_counts": iron_calcium_counts(rows),
        "body_changes": body_measurement_changes(rows, start),
    }
