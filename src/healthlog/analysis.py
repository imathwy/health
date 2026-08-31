"""Diet-analysis schema, validation, aggregation, and target comparison."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .nutrition import CORE_NUTRIENTS


CLASSIFICATIONS = {"consumed_food", "possible_food", "unrelated", "unreviewed"}
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
PROFILE_REFERENCE = "config/health_profile.json"


def analysis_template(target: date, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "date": target.isoformat(),
        "profile": PROFILE_REFERENCE,
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


class AnalysisValidator:
    """Validate one analysis while keeping cross-record state explicit."""

    def __init__(self, analysis: dict[str, Any], manifest: dict[str, Any]) -> None:
        self.analysis = analysis
        self.manifest = manifest
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.schema_version = analysis.get("schema_version")
        self.manifest_files = {asset["file"] for asset in manifest.get("assets", [])}
        self.day_context: dict[str, Any] = {}
        self.day_type: Any = None
        self.image_records: dict[str, dict[str, Any]] = {}
        self.meal_records: dict[str, dict[str, Any]] = {}

    def validate(self) -> tuple[list[str], list[str]]:
        self._validate_schema_and_date()
        self._validate_day_context()
        self._validate_images()
        self._validate_manifest_coverage()
        self._validate_meals()
        self._validate_image_meal_links()
        self._validate_assessment_and_metadata()
        self._add_warnings()
        return self.errors, self.warnings

    def _validate_schema_and_date(self) -> None:
        if self.schema_version not in ANALYSIS_SCHEMA_VERSIONS:
            versions = " 或 ".join(
                str(value) for value in sorted(ANALYSIS_SCHEMA_VERSIONS)
            )
            self.errors.append(f"analysis.json schema_version 必须是 {versions}")
        elif self.schema_version == 1:
            self.warnings.append("analysis.json 仍是 schema v1，食物来源元数据不完整")
        if self.analysis.get("date") != self.manifest.get("date"):
            self.errors.append("analysis.json 与 manifest.json 的日期不一致")

    def _validate_day_context(self) -> None:
        value = self.analysis.get("day_context")
        if not isinstance(value, dict):
            self.errors.append("day_context 必须是对象")
            value = {}
        self.day_context = value
        self.day_type = value.get("day_type")
        if self.day_type not in DAY_TYPES:
            self.errors.append(f"day_context.day_type 无效：{self.day_type!r}")

    def _validate_images(self) -> None:
        rows = self.analysis.get("images")
        if not isinstance(rows, list):
            self.errors.append("images 必须是数组")
            rows = []
        for index, row in enumerate(rows):
            self._validate_image(index, row)

    def _validate_image(self, index: int, row: Any) -> None:
        if not isinstance(row, dict):
            self.errors.append(f"images[{index}] 必须是对象")
            return
        filename = row.get("file")
        if not isinstance(filename, str) or not filename:
            self.errors.append(f"images[{index}].file 缺失")
            return
        if filename in self.image_records:
            self.errors.append(f"图片记录重复：{filename}")
        self.image_records[filename] = row
        classification = row.get("classification")
        if classification not in CLASSIFICATIONS:
            self.errors.append(f"{filename} 的 classification 无效：{classification!r}")
        elif classification == "unreviewed":
            self.errors.append(f"{filename} 尚未检查")
        for key in ("observations", "uncertainties"):
            if not isinstance(row.get(key), list):
                self.errors.append(f"{filename}.{key} 必须是数组")

    def _validate_manifest_coverage(self) -> None:
        analysis_files = set(self.image_records)
        missing = sorted(self.manifest_files - analysis_files)
        extra = sorted(analysis_files - self.manifest_files)
        if missing:
            self.errors.append(f"analysis.json 缺少图片记录：{', '.join(missing)}")
        if extra:
            self.errors.append(f"analysis.json 含不存在的图片记录：{', '.join(extra)}")

    def _validate_meals(self) -> None:
        meals = self.analysis.get("meals")
        if not isinstance(meals, list):
            self.errors.append("meals 必须是数组")
            meals = []
        for meal_index, meal in enumerate(meals):
            self._validate_meal(meal_index, meal)

    def _validate_meal(self, meal_index: int, meal: Any) -> None:
        if not isinstance(meal, dict):
            self.errors.append(f"meals[{meal_index}] 必须是对象")
            return
        meal_id = meal.get("id")
        if not isinstance(meal_id, str) or not meal_id:
            self.errors.append(f"meals[{meal_index}].id 缺失")
            return
        if meal_id in self.meal_records:
            self.errors.append(f"餐次 id 重复：{meal_id}")
        self.meal_records[meal_id] = meal
        if not isinstance(meal.get("label"), str) or not meal.get("label"):
            self.errors.append(f"餐次 {meal_id} 缺少 label")
        self._validate_meal_images(meal_id, meal.get("images"))
        items = meal.get("items")
        if not isinstance(items, list) or not items:
            self.errors.append(f"餐次 {meal_id} 至少需要一个食物条目")
            return
        for item_index, item in enumerate(items):
            self._validate_item(meal_id, item_index, item)

    def _validate_meal_images(self, meal_id: str, meal_images: Any) -> None:
        if not isinstance(meal_images, list):
            self.errors.append(f"餐次 {meal_id}.images 必须是数组")
            return
        for filename in meal_images:
            if filename not in self.manifest_files:
                self.errors.append(f"餐次 {meal_id} 引用了不存在的图片：{filename}")

    def _validate_item(self, meal_id: str, item_index: int, item: Any) -> None:
        prefix = f"餐次 {meal_id}.items[{item_index}]"
        if not isinstance(item, dict):
            self.errors.append(f"{prefix} 必须是对象")
            return
        if not isinstance(item.get("name"), str) or not item.get("name"):
            self.errors.append(f"{prefix}.name 缺失")
        if not isinstance(item.get("portion"), str) or not item.get("portion"):
            self.errors.append(f"{prefix}.portion 缺失")
        nutrition = item.get("nutrition")
        if not isinstance(nutrition, dict):
            self.errors.append(f"{prefix}.nutrition 必须是对象")
            return
        for nutrient in NUTRIENT_KEYS:
            validate_range(
                nutrition.get(nutrient),
                f"{prefix}.nutrition.{nutrient}",
                self.errors,
            )
        self._validate_optional_nutrients(prefix, item.get("optional_nutrients", {}))
        confidence = item.get("confidence")
        if confidence not in CONFIDENCE_LEVELS:
            self.errors.append(f"{prefix}.confidence 无效：{confidence!r}")
        self._validate_evidence(prefix, item.get("evidence"))

    def _validate_optional_nutrients(self, prefix: str, nutrients: Any) -> None:
        if not isinstance(nutrients, dict):
            self.errors.append(f"{prefix}.optional_nutrients 必须是对象")
            return
        for nutrient, value in nutrients.items():
            if not isinstance(nutrient, str) or not nutrient:
                self.errors.append(f"{prefix}.optional_nutrients 含无效名称")
                continue
            if nutrient in NUTRIENT_KEYS:
                self.errors.append(
                    f"{prefix}.optional_nutrients.{nutrient} 与核心营养素重复"
                )
                continue
            validate_range(
                value, f"{prefix}.optional_nutrients.{nutrient}", self.errors
            )

    def _validate_evidence(self, prefix: str, evidence: Any) -> None:
        if self.schema_version == 2 and not isinstance(evidence, dict):
            self.errors.append(f"{prefix}.evidence 必须是对象")
        if not isinstance(evidence, dict):
            return
        portion_method = evidence.get("portion_method")
        if portion_method not in PORTION_METHODS:
            self.errors.append(
                f"{prefix}.evidence.portion_method 无效：{portion_method!r}"
            )
        nutrition_source = evidence.get("nutrition_source")
        if nutrition_source not in NUTRITION_SOURCE_TYPES:
            self.errors.append(
                f"{prefix}.evidence.nutrition_source 无效：{nutrition_source!r}"
            )
        self._validate_references(prefix, evidence.get("references", []))
        if not isinstance(evidence.get("notes", []), list):
            self.errors.append(f"{prefix}.evidence.notes 必须是数组")

    def _validate_references(self, prefix: str, references: Any) -> None:
        if not isinstance(references, list):
            self.errors.append(f"{prefix}.evidence.references 必须是数组")
            return
        for index, reference in enumerate(references):
            if not isinstance(reference, dict):
                self.errors.append(f"{prefix}.evidence.references[{index}] 必须是对象")
                continue
            provider = reference.get("provider")
            if not isinstance(provider, str) or not provider:
                self.errors.append(
                    f"{prefix}.evidence.references[{index}].provider 缺失"
                )

    def _validate_image_meal_links(self) -> None:
        for filename, row in self.image_records.items():
            classification = row.get("classification")
            meal_id = row.get("meal_id")
            if classification == "consumed_food":
                if meal_id not in self.meal_records:
                    self.errors.append(
                        f"{filename} 标记为 consumed_food，但 meal_id 无效"
                    )
                elif filename not in self.meal_records[meal_id].get("images", []):
                    self.errors.append(f"{filename} 未列入餐次 {meal_id} 的 images")
            elif meal_id not in {None, ""} and meal_id not in self.meal_records:
                self.errors.append(f"{filename} 的 meal_id 无效：{meal_id}")

    def _validate_assessment_and_metadata(self) -> None:
        assessment = self.analysis.get("assessment")
        if not isinstance(assessment, dict):
            self.errors.append("assessment 必须是对象")
        else:
            for key in ("summary", "strengths", "gaps", "next_actions"):
                if not isinstance(assessment.get(key), list):
                    self.errors.append(f"assessment.{key} 必须是数组")
            if not isinstance(assessment.get("supplement_note"), str):
                self.errors.append("assessment.supplement_note 必须是字符串")
        if not isinstance(self.analysis.get("assumptions"), list):
            self.errors.append("assumptions 必须是数组")
        if self.analysis.get("overall_confidence") not in CONFIDENCE_LEVELS:
            self.errors.append("overall_confidence 必须是 low、medium 或 high")

    def _add_warnings(self) -> None:
        possible_count = sum(
            row.get("classification") == "possible_food"
            for row in self.image_records.values()
        )
        if possible_count:
            self.warnings.append(f"有 {possible_count} 张图片只能确定为可能摄入")
        if self.day_type == "unknown":
            self.warnings.append("当天训练类型未知，热量和碳水使用默认目标")
        if self.day_context.get("photo_coverage") in {
            None,
            "",
            "unknown",
            "partial",
        }:
            self.warnings.append("照片覆盖可能不完整，不能把估算视为完整饮食日志")


def validate_analysis(
    analysis: dict[str, Any], manifest: dict[str, Any]
) -> tuple[list[str], list[str]]:
    return AnalysisValidator(analysis, manifest).validate()


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
