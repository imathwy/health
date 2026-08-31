"""Private, rebuildable SQLite index for structured nutrition analyses."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .nutrition import CORE_NUTRIENTS, nutrient_unit
from .tracking import effective_tracking, meal_nutrition_totals

SCHEMA_VERSION = 2


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class NutritionStore:
    """Small SQLite store derived from dated ``analysis.json`` files."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.ensure_schema()

    def __enter__(self) -> "NutritionStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.connection.close()

    def ensure_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_logs (
                date TEXT PRIMARY KEY,
                analysis_schema_version INTEGER NOT NULL,
                day_type TEXT NOT NULL,
                training_notes TEXT NOT NULL,
                photo_coverage TEXT NOT NULL,
                overall_confidence TEXT NOT NULL,
                asset_count INTEGER NOT NULL,
                analysis_path TEXT NOT NULL,
                analysis_sha256 TEXT NOT NULL,
                target_snapshot_json TEXT NOT NULL,
                comparison_json TEXT NOT NULL,
                assessment_json TEXT NOT NULL,
                assumptions_json TEXT NOT NULL,
                tracking_json TEXT NOT NULL DEFAULT '{}',
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_nutrients (
                date TEXT NOT NULL REFERENCES daily_logs(date) ON DELETE CASCADE,
                nutrient TEXT NOT NULL,
                low REAL NOT NULL,
                high REAL NOT NULL,
                unit TEXT NOT NULL,
                covered_items INTEGER NOT NULL,
                total_items INTEGER NOT NULL,
                PRIMARY KEY (date, nutrient)
            );

            CREATE TABLE IF NOT EXISTS meals (
                date TEXT NOT NULL REFERENCES daily_logs(date) ON DELETE CASCADE,
                meal_id TEXT NOT NULL,
                label TEXT NOT NULL,
                meal_time TEXT NOT NULL,
                images_json TEXT NOT NULL,
                notes_json TEXT NOT NULL,
                tracking_tags_json TEXT NOT NULL DEFAULT '[]',
                protein_target_applicable INTEGER NOT NULL DEFAULT 1,
                meal_index INTEGER NOT NULL,
                PRIMARY KEY (date, meal_id)
            );

            CREATE TABLE IF NOT EXISTS meal_nutrients (
                date TEXT NOT NULL,
                meal_id TEXT NOT NULL,
                nutrient TEXT NOT NULL,
                low REAL NOT NULL,
                high REAL NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (date, meal_id, nutrient),
                FOREIGN KEY (date, meal_id) REFERENCES meals(date, meal_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS food_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                meal_id TEXT NOT NULL,
                item_index INTEGER NOT NULL,
                name TEXT NOT NULL,
                portion TEXT NOT NULL,
                confidence TEXT NOT NULL,
                portion_method TEXT NOT NULL,
                nutrition_source TEXT NOT NULL,
                references_json TEXT NOT NULL,
                evidence_notes_json TEXT NOT NULL,
                FOREIGN KEY (date, meal_id) REFERENCES meals(date, meal_id)
                    ON DELETE CASCADE,
                UNIQUE (date, meal_id, item_index)
            );

            CREATE TABLE IF NOT EXISTS food_item_nutrients (
                food_item_id INTEGER NOT NULL REFERENCES food_items(id)
                    ON DELETE CASCADE,
                nutrient TEXT NOT NULL,
                low REAL NOT NULL,
                high REAL NOT NULL,
                unit TEXT NOT NULL,
                is_core INTEGER NOT NULL,
                PRIMARY KEY (food_item_id, nutrient)
            );

            CREATE TABLE IF NOT EXISTS images (
                date TEXT NOT NULL REFERENCES daily_logs(date) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                sha256 TEXT,
                classification TEXT NOT NULL,
                meal_id TEXT,
                observations_json TEXT NOT NULL,
                uncertainties_json TEXT NOT NULL,
                PRIMARY KEY (date, filename)
            );

            CREATE TABLE IF NOT EXISTS fdc_cache (
                cache_key TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS food_items_date_idx ON food_items(date);
            CREATE INDEX IF NOT EXISTS meal_nutrients_date_idx
                ON meal_nutrients(date);
            CREATE INDEX IF NOT EXISTS images_classification_idx
                ON images(classification);
            """
        )
        self._ensure_column(
            "daily_logs", "tracking_json", "TEXT NOT NULL DEFAULT '{}'"
        )
        self._ensure_column(
            "meals", "tracking_tags_json", "TEXT NOT NULL DEFAULT '[]'"
        )
        self._ensure_column(
            "meals", "protein_target_applicable", "INTEGER NOT NULL DEFAULT 1"
        )
        current = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if current is not None and int(current["value"]) > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {current['value']} is newer than supported "
                f"schema {SCHEMA_VERSION}"
            )
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def upsert_day(
        self,
        *,
        analysis: dict[str, Any],
        manifest: dict[str, Any],
        analysis_path: str,
        analysis_sha256: str,
        totals: dict[str, list[float]],
        targets: dict[str, Any],
        comparisons: list[dict[str, str]],
    ) -> None:
        target = str(analysis["date"])
        context = analysis.get("day_context", {})
        meals = analysis.get("meals", [])
        total_items = sum(len(meal.get("items", [])) for meal in meals)
        asset_hashes = {
            row.get("file"): row.get("sha256") for row in manifest.get("assets", [])
        }

        optional_totals: dict[str, list[float | int]] = {}
        with self.connection:
            self.connection.execute("DELETE FROM daily_logs WHERE date = ?", (target,))
            self.connection.execute(
                """
                INSERT INTO daily_logs(
                    date, analysis_schema_version, day_type, training_notes,
                    photo_coverage, overall_confidence, asset_count,
                    analysis_path, analysis_sha256, target_snapshot_json,
                    comparison_json, assessment_json, assumptions_json,
                    tracking_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target,
                    int(analysis.get("schema_version", 1)),
                    str(context.get("day_type", "unknown")),
                    str(context.get("training_notes", "")),
                    str(context.get("photo_coverage", "unknown")),
                    str(analysis.get("overall_confidence", "low")),
                    int(manifest.get("asset_count", 0)),
                    analysis_path,
                    analysis_sha256,
                    _json(targets),
                    _json(comparisons),
                    _json(analysis.get("assessment", {})),
                    _json(analysis.get("assumptions", [])),
                    _json(effective_tracking(analysis)),
                    _now(),
                ),
            )

            for nutrient in CORE_NUTRIENTS:
                low, high = totals[nutrient]
                self.connection.execute(
                    """
                    INSERT INTO daily_nutrients(
                        date, nutrient, low, high, unit, covered_items, total_items
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target,
                        nutrient,
                        float(low),
                        float(high),
                        nutrient_unit(nutrient),
                        total_items,
                        total_items,
                    ),
                )

            for meal_index, meal in enumerate(meals):
                meal_id = str(meal["id"])
                self.connection.execute(
                    """
                    INSERT INTO meals(
                        date, meal_id, label, meal_time, images_json,
                        notes_json, tracking_tags_json,
                        protein_target_applicable, meal_index
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target,
                        meal_id,
                        str(meal.get("label", meal_id)),
                        str(meal.get("time") or ""),
                        _json(meal.get("images", [])),
                        _json(meal.get("notes", [])),
                        _json(meal.get("tracking_tags", [])),
                        int(bool(meal.get("protein_target_applicable", True))),
                        meal_index,
                    ),
                )
                for nutrient, value in meal_nutrition_totals(meal).items():
                    self.connection.execute(
                        """
                        INSERT INTO meal_nutrients(
                            date, meal_id, nutrient, low, high, unit
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            target,
                            meal_id,
                            nutrient,
                            float(value[0]),
                            float(value[1]),
                            nutrient_unit(nutrient),
                        ),
                    )
                for item_index, item in enumerate(meal.get("items", [])):
                    evidence = item.get("evidence", {})
                    cursor = self.connection.execute(
                        """
                        INSERT INTO food_items(
                            date, meal_id, item_index, name, portion, confidence,
                            portion_method, nutrition_source, references_json,
                            evidence_notes_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            target,
                            meal_id,
                            item_index,
                            str(item.get("name", "")),
                            str(item.get("portion", "")),
                            str(item.get("confidence", "low")),
                            str(evidence.get("portion_method", "unknown")),
                            str(evidence.get("nutrition_source", "unknown")),
                            _json(evidence.get("references", [])),
                            _json(evidence.get("notes", [])),
                        ),
                    )
                    food_item_id = int(cursor.lastrowid)
                    nutrients = dict(item.get("nutrition", {}))
                    nutrients.update(item.get("optional_nutrients", {}))
                    for nutrient, value in nutrients.items():
                        if (
                            not isinstance(value, list)
                            or len(value) != 2
                            or not all(
                                isinstance(number, (int, float)) for number in value
                            )
                        ):
                            continue
                        low, high = float(value[0]), float(value[1])
                        self.connection.execute(
                            """
                            INSERT INTO food_item_nutrients(
                                food_item_id, nutrient, low, high, unit, is_core
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                food_item_id,
                                nutrient,
                                low,
                                high,
                                nutrient_unit(nutrient),
                                int(nutrient in CORE_NUTRIENTS),
                            ),
                        )
                        if nutrient not in CORE_NUTRIENTS:
                            accumulator = optional_totals.setdefault(
                                nutrient, [0.0, 0.0, 0]
                            )
                            accumulator[0] = float(accumulator[0]) + low
                            accumulator[1] = float(accumulator[1]) + high
                            accumulator[2] = int(accumulator[2]) + 1

            for nutrient, (low, high, covered) in optional_totals.items():
                self.connection.execute(
                    """
                    INSERT INTO daily_nutrients(
                        date, nutrient, low, high, unit, covered_items, total_items
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target,
                        nutrient,
                        float(low),
                        float(high),
                        nutrient_unit(nutrient),
                        int(covered),
                        total_items,
                    ),
                )

            for image in analysis.get("images", []):
                filename = str(image.get("file", ""))
                self.connection.execute(
                    """
                    INSERT INTO images(
                        date, filename, sha256, classification, meal_id,
                        observations_json, uncertainties_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target,
                        filename,
                        asset_hashes.get(filename),
                        str(image.get("classification", "unreviewed")),
                        image.get("meal_id"),
                        _json(image.get("observations", [])),
                        _json(image.get("uncertainties", [])),
                    ),
                )

    def day_state(self, target: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM daily_logs WHERE date = ?", (target,)
        ).fetchone()
        if row is None:
            return None
        nutrients = {
            nutrient["nutrient"]: {
                "low": nutrient["low"],
                "high": nutrient["high"],
                "unit": nutrient["unit"],
                "covered_items": nutrient["covered_items"],
                "total_items": nutrient["total_items"],
            }
            for nutrient in self.connection.execute(
                "SELECT * FROM daily_nutrients WHERE date = ? ORDER BY nutrient",
                (target,),
            )
        }
        result = dict(row)
        result["tracking"] = json.loads(result.pop("tracking_json"))
        result["nutrients"] = nutrients
        result["meal_count"] = self.connection.execute(
            "SELECT COUNT(*) FROM meals WHERE date = ?", (target,)
        ).fetchone()[0]
        result["food_item_count"] = self.connection.execute(
            "SELECT COUNT(*) FROM food_items WHERE date = ?", (target,)
        ).fetchone()[0]
        result["meals"] = self._meals_for_day(target)
        return result

    def _meals_for_day(self, target: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM meals WHERE date = ? ORDER BY meal_index", (target,)
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            meal = dict(row)
            meal["tracking_tags"] = json.loads(meal.pop("tracking_tags_json"))
            meal["protein_target_applicable"] = bool(
                meal["protein_target_applicable"]
            )
            meal["nutrients"] = {
                nutrient["nutrient"]: {
                    "low": float(nutrient["low"]),
                    "high": float(nutrient["high"]),
                    "unit": nutrient["unit"],
                }
                for nutrient in self.connection.execute(
                    """
                    SELECT * FROM meal_nutrients
                    WHERE date = ? AND meal_id = ? ORDER BY nutrient
                    """,
                    (target, row["meal_id"]),
                )
            }
            result.append(meal)
        return result

    def list_days(self, start: date, end: date) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM daily_logs
            WHERE date >= ? AND date <= ?
            ORDER BY date
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            day = dict(row)
            day["tracking"] = json.loads(day.pop("tracking_json"))
            day["nutrients"] = {
                nutrient["nutrient"]: {
                    "low": float(nutrient["low"]),
                    "high": float(nutrient["high"]),
                    "unit": nutrient["unit"],
                    "covered_items": int(nutrient["covered_items"]),
                    "total_items": int(nutrient["total_items"]),
                }
                for nutrient in self.connection.execute(
                    "SELECT * FROM daily_nutrients WHERE date = ? ORDER BY nutrient",
                    (row["date"],),
                )
            }
            day["comparisons"] = json.loads(day.pop("comparison_json"))
            day["meals"] = self._meals_for_day(str(row["date"]))
            results.append(day)
        return results

    def provenance_counts(self, start: date, end: date) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT nutrition_source, COUNT(*) AS count
            FROM food_items
            WHERE date >= ? AND date <= ?
            GROUP BY nutrition_source
            ORDER BY count DESC, nutrition_source
            """,
            (start.isoformat(), end.isoformat()),
        )
        return {str(row["nutrition_source"]): int(row["count"]) for row in rows}

    def database_stats(self) -> dict[str, Any]:
        first_last = self.connection.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM daily_logs"
        ).fetchone()
        return {
            "schema_version": SCHEMA_VERSION,
            "path": str(self.path),
            "day_count": int(first_last[2]),
            "first_date": first_last[0],
            "last_date": first_last[1],
            "meal_count": int(
                self.connection.execute("SELECT COUNT(*) FROM meals").fetchone()[0]
            ),
            "food_item_count": int(
                self.connection.execute("SELECT COUNT(*) FROM food_items").fetchone()[0]
            ),
            "image_count": int(
                self.connection.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            ),
        }

    def cache_get(self, cache_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM fdc_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        if row is None:
            return None
        return {
            "operation": row["operation"],
            "request": json.loads(row["request_json"]),
            "response": json.loads(row["response_json"]),
            "fetched_at": row["fetched_at"],
        }

    def cache_put(
        self,
        cache_key: str,
        operation: str,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> str:
        fetched_at = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO fdc_cache(
                    cache_key, operation, request_json, response_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    operation=excluded.operation,
                    request_json=excluded.request_json,
                    response_json=excluded.response_json,
                    fetched_at=excluded.fetched_at
                """,
                (cache_key, operation, _json(request), _json(response), fetched_at),
            )
        return fetched_at

    def clear_derived_days(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM daily_logs")
