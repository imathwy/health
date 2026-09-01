#!/usr/bin/env python3

"""Prepare, render, and verify the local Apple Photos diet-analysis pipeline."""

from __future__ import annotations

import argparse
import sqlite3
import sys

from .commands import (
    dashboard_command,
    database_status,
    doctor,
    fdc_food_command,
    fdc_search_command,
    nutrition_summary,
    prepare,
    rebuild_database,
    render,
    status,
    verify,
)
from .errors import PipelineError
from .profile_workflow import initialize_personal_profile, personal_profile_command


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

    profile_init_parser = subparsers.add_parser(
        "profile-init", help="初始化活动用户的私有个人档案与病历索引"
    )
    profile_init_parser.add_argument(
        "--migrate-config",
        action="store_true",
        help="备份 schema-v1 配置，并把个人信息迁入 durable profile",
    )
    profile_init_parser.set_defaults(func=initialize_personal_profile)

    profile_parser = subparsers.add_parser(
        "profile", help="校验个人档案并生成私有静态简介页"
    )
    profile_parser.set_defaults(func=personal_profile_command)

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
        "profile-init",
        "profile",
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
