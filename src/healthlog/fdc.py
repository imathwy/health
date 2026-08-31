"""Minimal USDA FoodData Central client with normalized nutrition output."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api.nal.usda.gov/fdc/v1"
DEFAULT_DATA_TYPES = ("Foundation", "SR Legacy", "Survey (FNDDS)")
CORE_NUTRIENTS = (
    "kcal",
    "protein_g",
    "carbohydrate_g",
    "fat_g",
    "fiber_g",
    "sodium_mg",
)

# FoodData Central nutrient IDs. Names are fallbacks for older/abridged payloads.
NUTRIENT_SPECS: dict[str, dict[str, Any]] = {
    "kcal": {
        "ids": (1008, 2047, 2048),
        "names": ("Energy", "Metabolizable Energy (Atwater General Factor)",
                  "Metabolizable Energy (Atwater Specific Factor)"),
        "unit": "kcal",
    },
    "protein_g": {"ids": (1003,), "names": ("Protein",), "unit": "g"},
    "carbohydrate_g": {
        "ids": (1005,),
        "names": ("Carbohydrate, by difference",),
        "unit": "g",
    },
    "fat_g": {
        "ids": (1004,),
        "names": ("Total lipid (fat)",),
        "unit": "g",
    },
    "fiber_g": {
        "ids": (1079,),
        "names": ("Fiber, total dietary",),
        "unit": "g",
    },
    "sodium_mg": {"ids": (1093,), "names": ("Sodium, Na",), "unit": "mg"},
    "sugar_g": {
        "ids": (2000,),
        "names": ("Sugars, total including NLEA", "Sugars, total"),
        "unit": "g",
    },
    "added_sugar_g": {
        "ids": (1235,),
        "names": ("Sugars, added",),
        "unit": "g",
    },
    "saturated_fat_g": {
        "ids": (1258,),
        "names": ("Fatty acids, total saturated",),
        "unit": "g",
    },
    "calcium_mg": {"ids": (1087,), "names": ("Calcium, Ca",), "unit": "mg"},
    "iron_mg": {"ids": (1089,), "names": ("Iron, Fe",), "unit": "mg"},
    "magnesium_mg": {
        "ids": (1090,),
        "names": ("Magnesium, Mg",),
        "unit": "mg",
    },
    "potassium_mg": {
        "ids": (1092,),
        "names": ("Potassium, K",),
        "unit": "mg",
    },
    "vitamin_c_mg": {
        "ids": (1162,),
        "names": ("Vitamin C, total ascorbic acid",),
        "unit": "mg",
    },
    "vitamin_d_mcg": {
        "ids": (1114,),
        "names": ("Vitamin D (D2 + D3)",),
        "unit": "mcg",
    },
}


class FDCError(RuntimeError):
    """A sanitized FoodData Central request or parsing error."""


@dataclass(frozen=True)
class FDCResponse:
    operation: str
    request: dict[str, Any]
    payload: dict[str, Any]


def _request_json(
    method: str,
    path: str,
    *,
    api_key: str,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    url = f"{API_ROOT}/{path.lstrip('/')}?{urlencode({'api_key': api_key})}"
    data = None
    headers = {"Accept": "application/json", "User-Agent": "local-healthlog/0.2"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        try:
            detail = exc.read(800).decode("utf-8", errors="replace")
        except OSError:
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise FDCError(f"USDA FoodData Central HTTP {exc.code}{suffix}") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", "network error")
        raise FDCError(f"无法连接 USDA FoodData Central：{reason}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FDCError("USDA FoodData Central 返回了无效 JSON") from exc
    if not isinstance(value, dict):
        raise FDCError("USDA FoodData Central 响应顶层不是对象")
    return value


def search_foods(
    query: str,
    *,
    api_key: str,
    page_size: int = 5,
    data_types: tuple[str, ...] = DEFAULT_DATA_TYPES,
    timeout: int = 30,
) -> FDCResponse:
    cleaned = query.strip()
    if not cleaned:
        raise FDCError("食物搜索词不能为空")
    body = {
        "query": cleaned,
        "pageSize": max(1, min(int(page_size), 25)),
        "dataType": list(data_types),
    }
    payload = _request_json(
        "POST", "foods/search", api_key=api_key, body=body, timeout=timeout
    )
    return FDCResponse("search", body, payload)


def food_details(fdc_id: int, *, api_key: str, timeout: int = 30) -> FDCResponse:
    if fdc_id <= 0:
        raise FDCError("FDC ID 必须是正整数")
    payload = _request_json(
        "GET", f"food/{fdc_id}", api_key=api_key, timeout=timeout
    )
    return FDCResponse("food", {"fdc_id": fdc_id}, payload)


def _raw_nutrients(food: dict[str, Any]) -> list[dict[str, Any]]:
    rows = food.get("foodNutrients", [])
    return rows if isinstance(rows, list) else []


def _nutrient_fields(row: dict[str, Any]) -> tuple[int | None, str, str, float | None]:
    nested = row.get("nutrient") if isinstance(row.get("nutrient"), dict) else {}
    raw_id = row.get("nutrientId", nested.get("id"))
    try:
        nutrient_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        nutrient_id = None
    name = str(row.get("nutrientName", nested.get("name", "")))
    unit = str(row.get("unitName", nested.get("unitName", "")))
    raw_amount = row.get("value", row.get("amount"))
    try:
        amount = float(raw_amount) if raw_amount is not None else None
    except (TypeError, ValueError):
        amount = None
    return nutrient_id, name, unit, amount


def extract_nutrients(food: dict[str, Any]) -> dict[str, float]:
    parsed = [_nutrient_fields(row) for row in _raw_nutrients(food)]
    result: dict[str, float] = {}
    for key, spec in NUTRIENT_SPECS.items():
        match: tuple[int | None, str, str, float | None] | None = None
        for desired_id in spec["ids"]:
            match = next((row for row in parsed if row[0] == desired_id), None)
            if match is not None:
                break
        if match is None:
            match = next((row for row in parsed if row[1] in spec["names"]), None)
        if match is None or match[3] is None:
            continue
        amount = float(match[3])
        raw_unit = match[2].strip().lower()
        desired_unit = str(spec["unit"])
        if key == "kcal" and raw_unit in {"kj", "kilojoule", "kilojoules"}:
            amount /= 4.184
        elif desired_unit == "mg" and raw_unit in {"g", "gram", "grams"}:
            amount *= 1000
        elif desired_unit == "mcg" and raw_unit in {"mg", "milligram", "milligrams"}:
            amount *= 1000
        if math.isfinite(amount) and amount >= 0:
            result[key] = round(amount, 6)
    return result


def normalize_food(food: dict[str, Any]) -> dict[str, Any]:
    raw_id = food.get("fdcId")
    try:
        fdc_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise FDCError("USDA 食物记录缺少有效 FDC ID") from exc
    normalized: dict[str, Any] = {
        "fdc_id": fdc_id,
        "description": str(food.get("description", "")),
        "data_type": str(food.get("dataType", "")),
        "publication_date": food.get("publicationDate"),
        "brand_owner": food.get("brandOwner"),
        "brand_name": food.get("brandName"),
        "basis_grams": 100,
        "nutrients_per_100g": extract_nutrients(food),
        "source": {
            "provider": "USDA FoodData Central",
            "id": str(fdc_id),
            "url": f"https://fdc.nal.usda.gov/food-details/{fdc_id}/nutrients",
        },
    }
    if food.get("servingSize") is not None:
        normalized["serving"] = {
            "size": food.get("servingSize"),
            "unit": food.get("servingSizeUnit"),
            "household_text": food.get("householdServingFullText"),
        }
    return normalized


def normalize_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    foods = payload.get("foods", [])
    if not isinstance(foods, list):
        raise FDCError("USDA 搜索响应缺少 foods 数组")
    results: list[dict[str, Any]] = []
    for food in foods:
        if not isinstance(food, dict):
            continue
        try:
            results.append(normalize_food(food))
        except FDCError:
            continue
    return results


def scale_nutrients(
    food: dict[str, Any], grams_low: float, grams_high: float
) -> dict[str, list[float]]:
    if grams_low < 0 or grams_high < grams_low:
        raise FDCError("克数范围无效")
    nutrients = food.get("nutrients_per_100g", {})
    if not isinstance(nutrients, dict):
        raise FDCError("食物记录缺少每 100 g 营养数据")
    scaled: dict[str, list[float]] = {}
    for nutrient, raw_value in nutrients.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        low = value * grams_low / 100
        high = value * grams_high / 100
        scaled[nutrient] = [round(low, 2), round(high, 2)]
    return scaled


def analysis_item_candidate(
    food: dict[str, Any], grams_low: float, grams_high: float
) -> dict[str, Any]:
    scaled = scale_nutrients(food, grams_low, grams_high)
    missing = [key for key in CORE_NUTRIENTS if key not in scaled]
    reference = dict(food.get("source", {}))
    reference["description"] = food.get("description", "")
    reference["basis"] = "nutrient values per 100 g; scaled to stated gram range"
    return {
        "name": food.get("description", ""),
        "portion": (
            f"{grams_low:g} g" if grams_low == grams_high
            else f"{grams_low:g}–{grams_high:g} g"
        ),
        "nutrition": {key: scaled[key] for key in CORE_NUTRIENTS if key in scaled},
        "optional_nutrients": {
            key: value for key, value in scaled.items() if key not in CORE_NUTRIENTS
        },
        "confidence": "medium",
        "evidence": {
            "portion_method": (
                "manual_weight" if grams_low == grams_high else "manual_range"
            ),
            "nutrition_source": "usda_fdc",
            "references": [reference],
            "notes": [
                "USDA matching confirms composition only; confirm that the selected food and preparation match the photo."
            ],
        },
        "missing_core_nutrients": missing,
    }
