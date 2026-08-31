"""Shared nutrition vocabulary and interval aggregation."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


CORE_NUTRIENTS = (
    "kcal",
    "protein_g",
    "carbohydrate_g",
    "fat_g",
    "fiber_g",
    "sodium_mg",
)

NUTRIENT_UNITS = {
    "kcal": "kcal",
    "protein_g": "g",
    "carbohydrate_g": "g",
    "fat_g": "g",
    "fiber_g": "g",
    "sodium_mg": "mg",
    "sugar_g": "g",
    "added_sugar_g": "g",
    "saturated_fat_g": "g",
    "calcium_mg": "mg",
    "iron_mg": "mg",
    "magnesium_mg": "mg",
    "potassium_mg": "mg",
    "vitamin_c_mg": "mg",
    "vitamin_d_mcg": "mcg",
}


def nutrient_unit(name: str) -> str:
    if name in NUTRIENT_UNITS:
        return NUTRIENT_UNITS[name]
    if name.endswith("_mcg"):
        return "mcg"
    if name.endswith("_mg"):
        return "mg"
    if name.endswith("_g"):
        return "g"
    return "unknown"


def average_ranges(
    days: Iterable[dict[str, Any]], nutrients: Iterable[str] = CORE_NUTRIENTS
) -> dict[str, dict[str, Any]]:
    rows = list(days)
    result: dict[str, dict[str, Any]] = {}
    for nutrient in nutrients:
        values = [day["nutrients"].get(nutrient) for day in rows]
        available = [value for value in values if value is not None]
        if not available:
            continue
        result[nutrient] = {
            "low": sum(float(value["low"]) for value in available) / len(available),
            "high": sum(float(value["high"]) for value in available) / len(available),
            "unit": available[0]["unit"],
            "logged_days": len(available),
        }
    return result


def confidence_counts(days: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(day.get("overall_confidence", "unknown")) for day in days)
    return dict(sorted(counts.items()))
