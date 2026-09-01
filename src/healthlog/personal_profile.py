"""Personal-profile and medical-index domain model.

The module is deliberately independent from filesystems and renderers.  It owns
the private document shapes, migration projections, and cross-document
validation used by the application layer.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from pathlib import PurePosixPath
from typing import Any


PERSONAL_PROFILE_SCHEMA_VERSION = 1
MEDICAL_INDEX_SCHEMA_VERSION = 1
PROFILE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
HEALTH_STATUSES = {"active", "monitoring", "resolved", "historical"}
MEDICAL_CATEGORIES = {
    "examination",
    "diagnosis",
    "laboratory",
    "imaging",
    "treatment",
    "prescription",
    "vaccination",
    "other",
}
MEDICAL_STATUSES = {"current", "monitoring", "resolved", "historical"}

DEFAULT_NUTRITION_TARGETS: dict[str, Any] = {
    "energy_kcal": {
        "unknown": [2000, 2400],
        "rest": [1900, 2300],
        "strength": [2100, 2500],
        "swim": [2100, 2500],
        "tennis": [2200, 2700],
        "mixed": [2300, 2800],
    },
    "protein_g": [90, 130],
    "carbohydrate_g": {
        "unknown": [220, 320],
        "rest": [200, 280],
        "strength": [240, 340],
        "swim": [240, 340],
        "tennis": [280, 380],
        "mixed": [300, 420],
    },
    "fat_g": [55, 80],
    "fiber_g": [25, 35],
    "sodium_mg_max": 2000,
    "water_l_base": 1.7,
    "tracking": {
        "protein_per_meal_g": [20, 40],
        "direct_water_ml_base": 1700,
        "calcium_mg_target": 1000,
        "sleep_hours_min": 7,
        "caffeine_mg_max": 400,
        "vegetables_g_min": 300,
        "fruit_g_min": 200,
    },
}


def active_profile_id(settings: dict[str, Any]) -> str:
    """Return the stable local owner ID, including the schema-v1 fallback."""

    raw = settings.get("active_profile_id")
    if raw is None:
        legacy = settings.get("profile", {})
        if isinstance(legacy, dict):
            raw = legacy.get("id") or legacy.get("label")
    value = str(raw or "").strip().lower()
    if not PROFILE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "active_profile_id 必须由小写字母、数字、下划线或连字符组成，长度为 1–64"
        )
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _merged_targets(value: Any) -> dict[str, Any]:
    """Overlay legacy/local target fields without dropping required defaults."""

    result = deepcopy(DEFAULT_NUTRITION_TARGETS)
    if not isinstance(value, dict):
        return result
    for key, configured in value.items():
        if isinstance(configured, dict) and isinstance(result.get(key), dict):
            result[key].update(deepcopy(configured))
        else:
            result[key] = deepcopy(configured)
    return result


def personal_profile_template(
    settings: dict[str, Any], *, reviewed_on: date | None = None
) -> dict[str, Any]:
    """Create an editable private profile, migrating legacy settings when present."""

    profile_id = active_profile_id(settings)
    legacy_profile = settings.get("profile", {})
    if not isinstance(legacy_profile, dict):
        legacy_profile = {}
    activity = legacy_profile.get("activity", {})
    if not isinstance(activity, dict):
        activity = {}
    health_context = settings.get("health_context", {})
    if not isinstance(health_context, dict):
        health_context = {}
    diet_context = settings.get("diet_context", {})
    if not isinstance(diet_context, dict):
        diet_context = {}
    targets = _merged_targets(settings.get("targets"))
    review_date = (reviewed_on or date.today()).isoformat()

    return {
        "schema_version": PERSONAL_PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id,
        "display_name": str(legacy_profile.get("label") or profile_id),
        "updated_at": review_date,
        "demographics": {
            "age_years": legacy_profile.get("age_years"),
            "sex": str(legacy_profile.get("sex", "unspecified")),
            "height_cm": legacy_profile.get("height_cm"),
        },
        "current_status": {
            "weight_kg": legacy_profile.get("weight_kg"),
            "goals": _string_list(legacy_profile.get("goals")),
        },
        "activity": {
            "work_pattern": str(activity.get("work_pattern", "")),
            "strength_sessions_per_week": str(
                activity.get("strength_sessions_per_week", "")
            ),
            "tennis_hours_per_week": activity.get("tennis_hours_per_week", 0),
            "swimming_hours_per_week": activity.get("swimming_hours_per_week", 0),
        },
        "nutrition_targets": targets,
        "diet_context": deepcopy(diet_context),
        "health_status": {
            "conditions": [],
            "symptoms": [],
            "medications": [],
            "allergies": [],
            "context_notes": {
                "digestive": str(health_context.get("digestive", "")),
                "skin": str(health_context.get("skin", "")),
                "sleep": str(health_context.get("sleep", "")),
                "caffeine": str(health_context.get("caffeine", "")),
            },
            "supplement_guardrails": _string_list(
                health_context.get("supplement_guardrails")
            )
            or [
                "Do not infer a deficiency or add a supplement from one day of food photos."
            ],
        },
        "provenance": {
            "source": "migrated_local_config"
            if settings.get("schema_version") == 1
            else "user_reported",
            "last_reviewed": review_date,
            "notes": [],
        },
    }


def medical_index_template(profile_id: str) -> dict[str, Any]:
    """Create an empty medical-record index for one private profile."""

    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError("profile_id 格式无效")
    return {
        "schema_version": MEDICAL_INDEX_SCHEMA_VERSION,
        "profile_id": profile_id,
        "records": [],
    }


def settings_v2_from_legacy(settings: dict[str, Any]) -> dict[str, Any]:
    """Strip personal facts from the operational settings document."""

    profile_id = active_profile_id(settings)
    pipeline = deepcopy(settings.get("pipeline", {}))
    pipeline.setdefault("profile_records_directory", "data/profiles")
    return {
        "schema_version": 2,
        "active_profile_id": profile_id,
        "privacy": deepcopy(
            settings.get("privacy", {"allow_usda_text_queries": False})
        ),
        "pipeline": pipeline,
    }


def runtime_profile_context(
    settings: dict[str, Any], personal_profile: dict[str, Any]
) -> dict[str, Any]:
    """Project the canonical profile into the existing analysis context API."""

    result = deepcopy(settings)
    demographics = personal_profile.get("demographics", {})
    current = personal_profile.get("current_status", {})
    health = personal_profile.get("health_status", {})
    context_notes = health.get("context_notes", {})
    result["profile"] = {
        "id": personal_profile.get("profile_id"),
        "label": personal_profile.get("display_name"),
        "age_years": demographics.get("age_years"),
        "sex": demographics.get("sex", "unspecified"),
        "height_cm": demographics.get("height_cm"),
        "weight_kg": current.get("weight_kg"),
        "goals": deepcopy(current.get("goals", [])),
        "activity": deepcopy(personal_profile.get("activity", {})),
    }
    result["targets"] = deepcopy(personal_profile.get("nutrition_targets", {}))
    result["diet_context"] = deepcopy(personal_profile.get("diet_context", {}))
    result["health_context"] = {
        "digestive": context_notes.get("digestive", ""),
        "skin": context_notes.get("skin", ""),
        "sleep": context_notes.get("sleep", ""),
        "caffeine": context_notes.get("caffeine", ""),
        "supplement_guardrails": deepcopy(health.get("supplement_guardrails", [])),
    }
    result["_personal_profile"] = personal_profile
    return result


def _validate_date(
    value: Any, label: str, errors: list[str], *, allow_empty: bool
) -> None:
    if value in (None, "") and allow_empty:
        return
    if not isinstance(value, str):
        errors.append(f"{label} 必须是 YYYY-MM-DD")
        return
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        errors.append(f"{label} 必须是 YYYY-MM-DD")
        return
    if parsed.isoformat() != value:
        errors.append(f"{label} 必须是 YYYY-MM-DD")


def _validate_optional_positive_number(
    value: Any, label: str, errors: list[str]
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        errors.append(f"{label} 必须是正数或 null")


def _validate_string_array(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"{label} 必须是字符串数组")


def _validate_number_range(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != 2:
        errors.append(f"{label} 必须是 [最小值, 最大值]")
        return
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
    ):
        errors.append(f"{label} 必须包含两个数字")
        return
    if value[0] < 0 or value[1] < 0 or value[0] > value[1]:
        errors.append(f"{label} 范围无效")


def _validate_nutrition_targets(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("nutrition_targets 必须是对象")
        return
    for key in ("energy_kcal", "carbohydrate_g"):
        by_day = value.get(key)
        if not isinstance(by_day, dict):
            errors.append(f"nutrition_targets.{key} 必须是按训练日类型划分的对象")
            continue
        for day_type in ("unknown", "rest", "strength", "swim", "tennis", "mixed"):
            _validate_number_range(
                by_day.get(day_type),
                f"nutrition_targets.{key}.{day_type}",
                errors,
            )
    for key in ("protein_g", "fat_g", "fiber_g"):
        _validate_number_range(value.get(key), f"nutrition_targets.{key}", errors)
    for key in ("sodium_mg_max", "water_l_base"):
        _validate_optional_positive_number(
            value.get(key), f"nutrition_targets.{key}", errors
        )
        if value.get(key) is None:
            errors.append(f"nutrition_targets.{key} 不能为空")

    tracking = value.get("tracking", {})
    if not isinstance(tracking, dict):
        errors.append("nutrition_targets.tracking 必须是对象")
        return
    if "protein_per_meal_g" in tracking:
        _validate_number_range(
            tracking.get("protein_per_meal_g"),
            "nutrition_targets.tracking.protein_per_meal_g",
            errors,
        )
    for key in (
        "direct_water_ml_base",
        "calcium_mg_target",
        "sleep_hours_min",
        "caffeine_mg_max",
        "vegetables_g_min",
        "fruit_g_min",
    ):
        if key in tracking:
            _validate_optional_positive_number(
                tracking.get(key), f"nutrition_targets.tracking.{key}", errors
            )


def _validate_named_rows(
    rows: Any,
    label: str,
    errors: list[str],
    *,
    name_key: str,
    allowed_statuses: set[str] = HEALTH_STATUSES,
) -> set[str]:
    identifiers: set[str] = set()
    if not isinstance(rows, list):
        errors.append(f"{label} 必须是数组")
        return identifiers
    for index, row in enumerate(rows):
        prefix = f"{label}[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{prefix}.id 不能为空")
        elif identifier in identifiers:
            errors.append(f"{label} 的 id 重复：{identifier}")
        else:
            identifiers.add(identifier)
        if not isinstance(row.get(name_key), str) or not row.get(name_key, "").strip():
            errors.append(f"{prefix}.{name_key} 不能为空")
        if row.get("status") not in allowed_statuses:
            errors.append(f"{prefix}.status 无效")
        _validate_string_array(row.get("notes", []), f"{prefix}.notes", errors)
    return identifiers


def validate_personal_profile(
    document: dict[str, Any], *, expected_profile_id: str | None = None
) -> tuple[list[str], list[str]]:
    """Validate a canonical personal-profile document."""

    errors: list[str] = []
    warnings: list[str] = []
    if document.get("schema_version") != PERSONAL_PROFILE_SCHEMA_VERSION:
        errors.append("个人档案 schema_version 必须为 1")
    profile_id = document.get("profile_id")
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        errors.append("profile_id 格式无效")
    elif expected_profile_id and profile_id != expected_profile_id:
        errors.append(
            f"个人档案 profile_id={profile_id} 与活动档案 {expected_profile_id} 不一致"
        )
    if (
        not isinstance(document.get("display_name"), str)
        or not document.get("display_name", "").strip()
    ):
        errors.append("display_name 不能为空")
    _validate_date(document.get("updated_at"), "updated_at", errors, allow_empty=False)

    demographics = document.get("demographics")
    if not isinstance(demographics, dict):
        errors.append("demographics 必须是对象")
    else:
        _validate_optional_positive_number(
            demographics.get("age_years"), "demographics.age_years", errors
        )
        _validate_optional_positive_number(
            demographics.get("height_cm"), "demographics.height_cm", errors
        )
        if not isinstance(demographics.get("sex"), str):
            errors.append("demographics.sex 必须是字符串")

    current = document.get("current_status")
    if not isinstance(current, dict):
        errors.append("current_status 必须是对象")
    else:
        _validate_optional_positive_number(
            current.get("weight_kg"), "current_status.weight_kg", errors
        )
        _validate_string_array(current.get("goals"), "current_status.goals", errors)

    if not isinstance(document.get("activity"), dict):
        errors.append("activity 必须是对象")
    _validate_nutrition_targets(document.get("nutrition_targets"), errors)
    if not isinstance(document.get("diet_context"), dict):
        errors.append("diet_context 必须是对象")

    health = document.get("health_status")
    if not isinstance(health, dict):
        errors.append("health_status 必须是对象")
    else:
        _validate_named_rows(
            health.get("conditions"),
            "health_status.conditions",
            errors,
            name_key="name",
        )
        _validate_named_rows(
            health.get("symptoms"),
            "health_status.symptoms",
            errors,
            name_key="name",
        )
        _validate_named_rows(
            health.get("medications"),
            "health_status.medications",
            errors,
            name_key="name",
        )
        _validate_named_rows(
            health.get("allergies"),
            "health_status.allergies",
            errors,
            name_key="substance",
        )
        context_notes = health.get("context_notes")
        if not isinstance(context_notes, dict) or any(
            not isinstance(context_notes.get(key, ""), str)
            for key in ("digestive", "skin", "sleep", "caffeine")
        ):
            errors.append("health_status.context_notes 必须包含字符串备注")
        _validate_string_array(
            health.get("supplement_guardrails"),
            "health_status.supplement_guardrails",
            errors,
        )

    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance 必须是对象")
    else:
        if not isinstance(provenance.get("source"), str):
            errors.append("provenance.source 必须是字符串")
        _validate_date(
            provenance.get("last_reviewed"),
            "provenance.last_reviewed",
            errors,
            allow_empty=True,
        )
        _validate_string_array(provenance.get("notes", []), "provenance.notes", errors)
        if not provenance.get("last_reviewed"):
            warnings.append("个人档案尚未记录 last_reviewed")
    return errors, warnings


def validate_medical_index(
    document: dict[str, Any], *, expected_profile_id: str
) -> tuple[list[str], list[str]]:
    """Validate metadata without opening or interpreting raw medical files."""

    errors: list[str] = []
    warnings: list[str] = []
    if document.get("schema_version") != MEDICAL_INDEX_SCHEMA_VERSION:
        errors.append("病历索引 schema_version 必须为 1")
    if document.get("profile_id") != expected_profile_id:
        errors.append("病历索引 profile_id 与活动档案不一致")
    records = document.get("records")
    if not isinstance(records, list):
        errors.append("病历索引 records 必须是数组")
        return errors, warnings
    identifiers: set[str] = set()
    for index, record in enumerate(records):
        prefix = f"records[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{prefix}.id 不能为空")
        elif identifier in identifiers:
            errors.append(f"病历记录 id 重复：{identifier}")
        else:
            identifiers.add(identifier)
        if record.get("category") not in MEDICAL_CATEGORIES:
            errors.append(f"{prefix}.category 无效")
        if record.get("status") not in MEDICAL_STATUSES:
            errors.append(f"{prefix}.status 无效")
        if (
            not isinstance(record.get("title"), str)
            or not record.get("title", "").strip()
        ):
            errors.append(f"{prefix}.title 不能为空")
        _validate_date(record.get("date"), f"{prefix}.date", errors, allow_empty=True)
        for key in ("summary", "findings", "tags", "notes"):
            _validate_string_array(record.get(key, []), f"{prefix}.{key}", errors)
        source_files = record.get("source_files")
        if not isinstance(source_files, list):
            errors.append(f"{prefix}.source_files 必须是数组")
        else:
            for source_index, source in enumerate(source_files):
                source_prefix = f"{prefix}.source_files[{source_index}]"
                if isinstance(source, str):
                    raw_path = source
                    warnings.append(f"{source_prefix} 尚未记录 SHA-256")
                elif isinstance(source, dict):
                    raw_path = source.get("path")
                    digest = source.get("sha256")
                    if not isinstance(digest, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", digest
                    ):
                        errors.append(
                            f"{source_prefix}.sha256 必须是 64 位小写十六进制"
                        )
                    if not isinstance(source.get("media_type", ""), str):
                        errors.append(f"{source_prefix}.media_type 必须是字符串")
                else:
                    errors.append(f"{source_prefix} 必须是对象")
                    continue
                if not isinstance(raw_path, str):
                    errors.append(f"{source_prefix}.path 必须是字符串")
                    continue
                path = PurePosixPath(raw_path)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or "\x00" in raw_path
                ):
                    errors.append(f"{source_prefix}.path 必须是安全相对路径")
                elif path.parts[0] != "files":
                    errors.append(f"{source_prefix}.path 必须位于 medical/files/ 下")
        if not record.get("date"):
            warnings.append(f"{prefix} 未记录日期")
    return errors, warnings


def validate_profile_bundle(
    personal_profile: dict[str, Any], medical_index: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Validate both documents and references between them."""

    profile_id = personal_profile.get("profile_id")
    expected = profile_id if isinstance(profile_id, str) else ""
    errors, warnings = validate_personal_profile(
        personal_profile, expected_profile_id=expected or None
    )
    medical_errors, medical_warnings = validate_medical_index(
        medical_index, expected_profile_id=expected
    )
    errors.extend(medical_errors)
    warnings.extend(medical_warnings)

    record_ids = {
        row.get("id")
        for row in medical_index.get("records", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    health = personal_profile.get("health_status", {})
    for group in ("conditions", "symptoms", "medications", "allergies"):
        rows = health.get(group, []) if isinstance(health, dict) else []
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict):
                continue
            references = row.get("record_ids", [])
            _validate_string_array(
                references, f"health_status.{group}[{index}].record_ids", errors
            )
            if isinstance(references, list):
                for reference in references:
                    if isinstance(reference, str) and reference not in record_ids:
                        errors.append(
                            f"health_status.{group}[{index}] 引用不存在的病历："
                            f"{reference}"
                        )
    return errors, warnings
