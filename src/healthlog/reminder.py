"""Domain model for one private, local daily reminder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .personal_profile import PROFILE_ID_PATTERN


REMINDER_SCHEMA_VERSION = 1
DEFAULT_REMINDER_MESSAGE = "花一分钟补充今天的饮食、饮水、训练和身体记录。"
MAX_MESSAGE_LENGTH = 240
TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
WORKSPACE_TOKEN_PATTERN = re.compile(r"[0-9a-f]{12}")


@dataclass(frozen=True, slots=True)
class DailyTime:
    hour: int
    minute: int

    @classmethod
    def parse(cls, value: str) -> DailyTime:
        if not isinstance(value, str) or TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("提醒时间必须使用 24 小时制 HH:MM，例如 21:30")
        hour, minute = (int(part) for part in value.split(":"))
        return cls(hour=hour, minute=minute)

    def display(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True, slots=True)
class ReminderConfig:
    profile_id: str
    agent_label: str
    time: DailyTime
    message: str
    open_dashboard: bool
    updated_at: str

    @classmethod
    def from_document(
        cls, document: dict[str, Any], *, expected_profile_id: str
    ) -> ReminderConfig:
        errors: list[str] = []
        if document.get("schema_version") != REMINDER_SCHEMA_VERSION:
            errors.append("schema_version 必须为 1")
        profile_id = document.get("profile_id")
        if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(
            profile_id
        ):
            errors.append("profile_id 格式无效")
        elif profile_id != expected_profile_id:
            errors.append(
                f"提醒属于 {profile_id}，与活动档案 {expected_profile_id} 不一致"
            )

        launch_agent = document.get("launch_agent")
        agent_label = (
            launch_agent.get("label") if isinstance(launch_agent, dict) else None
        )
        label_prefix = f"io.local-healthlog.reminder.{profile_id}."
        if (
            not isinstance(agent_label, str)
            or not agent_label.startswith(label_prefix)
            or WORKSPACE_TOKEN_PATTERN.fullmatch(agent_label.removeprefix(label_prefix))
            is None
        ):
            errors.append("launch_agent.label 与用户或工作区不匹配")

        schedule = document.get("schedule")
        if not isinstance(schedule, dict) or schedule.get("kind") != "daily_local":
            errors.append("schedule.kind 必须为 daily_local")
            time = None
        else:
            try:
                time = DailyTime.parse(schedule.get("time"))
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
                time = None

        notification = document.get("notification")
        if not isinstance(notification, dict):
            errors.append("notification 必须是对象")
            message = ""
            open_dashboard = False
        else:
            message = notification.get("message")
            open_dashboard = notification.get("open_dashboard")
            if not isinstance(message, str) or not message.strip():
                errors.append("notification.message 不能为空")
            elif "\x00" in message:
                errors.append("notification.message 不能包含 NUL 字符")
            elif len(message) > MAX_MESSAGE_LENGTH:
                errors.append(
                    f"notification.message 不能超过 {MAX_MESSAGE_LENGTH} 个字符"
                )
            if not isinstance(open_dashboard, bool):
                errors.append("notification.open_dashboard 必须是布尔值")

        updated_at = document.get("updated_at")
        if not isinstance(updated_at, str):
            errors.append("updated_at 必须是带时区的 ISO 8601 时间")
        else:
            try:
                parsed = datetime.fromisoformat(updated_at)
            except ValueError:
                errors.append("updated_at 必须是带时区的 ISO 8601 时间")
            else:
                if parsed.tzinfo is None:
                    errors.append("updated_at 必须包含时区")

        if errors or time is None:
            raise ValueError("提醒配置无效：\n- " + "\n- ".join(errors))
        return cls(
            profile_id=profile_id,
            agent_label=agent_label,
            time=time,
            message=message.strip(),
            open_dashboard=open_dashboard,
            updated_at=updated_at,
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": REMINDER_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "launch_agent": {"label": self.agent_label},
            "schedule": {"kind": "daily_local", "time": self.time.display()},
            "notification": {
                "message": self.message,
                "open_dashboard": self.open_dashboard,
            },
            "updated_at": self.updated_at,
        }


def build_reminder_config(
    *,
    profile_id: str,
    agent_label: str,
    time_text: str,
    message: str | None,
    open_dashboard: bool | None,
    previous: ReminderConfig | None = None,
    updated_at: datetime | None = None,
) -> ReminderConfig:
    """Create a validated config while preserving omitted update fields."""

    time = DailyTime.parse(time_text)
    selected_message = (
        message
        if message is not None
        else previous.message
        if previous is not None
        else DEFAULT_REMINDER_MESSAGE
    )
    selected_message = selected_message.strip()
    if not selected_message:
        raise ValueError("提醒内容不能为空")
    if "\x00" in selected_message:
        raise ValueError("提醒内容不能包含 NUL 字符")
    if len(selected_message) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"提醒内容不能超过 {MAX_MESSAGE_LENGTH} 个字符")
    selected_open = (
        open_dashboard
        if open_dashboard is not None
        else previous.open_dashboard
        if previous is not None
        else False
    )
    current = (updated_at or datetime.now().astimezone()).isoformat(timespec="seconds")
    config = ReminderConfig(
        profile_id=profile_id,
        agent_label=agent_label,
        time=time,
        message=selected_message,
        open_dashboard=selected_open,
        updated_at=current,
    )
    ReminderConfig.from_document(config.to_document(), expected_profile_id=profile_id)
    return config


def launch_agent_label(profile_id: str, workspace_token: str) -> str:
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError("profile_id 格式无效")
    if not WORKSPACE_TOKEN_PATTERN.fullmatch(workspace_token):
        raise ValueError("workspace_token 必须是 12 位小写十六进制")
    return f"io.local-healthlog.reminder.{profile_id}.{workspace_token}"
