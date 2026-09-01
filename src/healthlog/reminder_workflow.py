"""Application workflow and macOS adapters for local daily reminders."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .personal_profile import active_profile_id
from .reminder import (
    ReminderConfig,
    build_reminder_config,
    launch_agent_label,
)
from .workspace import (
    REMINDER_SETTINGS_PATH,
    ROOT,
    load_json,
    load_settings,
    runtime_root,
    site_root,
    write_json,
)


def _launch_agents_dir() -> Path:
    override = os.environ.get("HEALTHLOG_LAUNCH_AGENTS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / "Library" / "LaunchAgents"


def _tool_path(environment_name: str, default: str) -> str:
    return os.environ.get(environment_name, default)


def _workspace_token() -> str:
    return hashlib.sha256(str(ROOT).encode("utf-8")).hexdigest()[:12]


def _current_agent_label(profile_id: str) -> str:
    return launch_agent_label(profile_id, _workspace_token())


def _agent_path(label: str) -> Path:
    return _launch_agents_dir() / f"{label}.plist"


def _service_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def _profile_id(settings: dict[str, Any]) -> str:
    try:
        return active_profile_id(settings)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc


def _load_config(settings: dict[str, Any], *, required: bool) -> ReminderConfig | None:
    profile_id = _profile_id(settings)
    if not REMINDER_SETTINGS_PATH.is_file():
        if required:
            raise PipelineError(
                "尚未设置每日提醒；先运行 diet reminder set --time HH:MM"
            )
        return None
    document = load_json(REMINDER_SETTINGS_PATH)
    try:
        return ReminderConfig.from_document(document, expected_profile_id=profile_id)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc


def _run_launchctl(
    arguments: list[str], *, check: bool
) -> subprocess.CompletedProcess[str]:
    command = [_tool_path("HEALTHLOG_LAUNCHCTL", "/bin/launchctl"), *arguments]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise PipelineError(f"无法运行 launchctl：{exc}") from exc
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise PipelineError(f"launchctl {' '.join(arguments)} 失败：{detail}")
    return completed


def _write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=False)
            temporary = Path(handle.name)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise PipelineError(f"无法写入 LaunchAgent：{path}: {exc}") from exc


def _launch_agent_payload(
    config: ReminderConfig, settings: dict[str, Any]
) -> dict[str, Any]:
    python_executable = Path(sys.executable).resolve()
    source_root = ROOT / "src"
    if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
        raise PipelineError(f"提醒所需 Python 缺失或不可执行：{python_executable}")
    if not (source_root / "healthlog" / "__main__.py").is_file():
        raise PipelineError(f"提醒所需 healthlog 源码缺失：{source_root}")
    log_dir = runtime_root(settings) / "reminders"
    log_dir.mkdir(parents=True, exist_ok=True)
    return {
        "Label": config.agent_label,
        "ProgramArguments": [
            str(python_executable),
            "-m",
            "healthlog",
            "reminder",
            "fire",
        ],
        "EnvironmentVariables": {
            "PYTHONPATH": str(source_root),
            "PYTHONUNBUFFERED": "1",
        },
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {
            "Hour": config.time.hour,
            "Minute": config.time.minute,
        },
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "reminder.stdout.log"),
        "StandardErrorPath": str(log_dir / "reminder.stderr.log"),
    }


def reminder_state(
    settings: dict[str, Any] | None = None, *, inspect_agent: bool = True
) -> dict[str, Any]:
    settings = settings or load_settings()
    profile_id = _profile_id(settings)
    config = _load_config(settings, required=False)
    current_label = _current_agent_label(profile_id)
    if config is None:
        agent_path = _agent_path(current_label)
        loaded = False
        if agent_path.is_file() and inspect_agent and sys.platform == "darwin":
            loaded = (
                _run_launchctl(
                    ["print", _service_target(current_label)], check=False
                ).returncode
                == 0
            )
        orphaned = agent_path.is_file() or loaded
        return {
            "status": "orphaned-agent" if orphaned else "disabled",
            "profile_id": profile_id,
            "config_path": REMINDER_SETTINGS_PATH,
            "agent_path": agent_path,
            "time": None,
            "open_dashboard": False,
            "loaded": loaded,
        }
    agent_path = _agent_path(config.agent_label)
    loaded = False
    if inspect_agent and sys.platform == "darwin":
        loaded = (
            _run_launchctl(
                ["print", _service_target(config.agent_label)], check=False
            ).returncode
            == 0
        )
    if config.agent_label != current_label:
        status = "stale-workspace"
    else:
        status = "active" if loaded else "configured-not-loaded"
    return {
        "status": status,
        "profile_id": profile_id,
        "config_path": REMINDER_SETTINGS_PATH,
        "agent_path": agent_path,
        "time": config.time.display(),
        "open_dashboard": config.open_dashboard,
        "loaded": loaded,
    }


def set_reminder(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        raise PipelineError("每日提醒安装目前只支持 macOS launchd")
    settings = load_settings()
    profile_id = _profile_id(settings)
    previous = _load_config(settings, required=False)
    current_label = _current_agent_label(profile_id)
    try:
        config = build_reminder_config(
            profile_id=profile_id,
            agent_label=current_label,
            time_text=args.time,
            message=args.message,
            open_dashboard=args.open_dashboard,
            previous=previous,
        )
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc

    agent_path = _agent_path(current_label)
    payload = _launch_agent_payload(config, settings)
    if previous is not None and previous.agent_label != current_label:
        _run_launchctl(["bootout", _service_target(previous.agent_label)], check=False)
        try:
            _agent_path(previous.agent_label).unlink(missing_ok=True)
        except OSError as exc:
            raise PipelineError(f"无法移除旧工作区提醒：{exc}") from exc
    _run_launchctl(["bootout", _service_target(current_label)], check=False)
    write_json(REMINDER_SETTINGS_PATH, config.to_document())
    _write_plist(agent_path, payload)
    _run_launchctl(["enable", _service_target(current_label)], check=True)
    _run_launchctl(["bootstrap", _gui_domain(), str(agent_path)], check=True)

    print("REMINDER_STATUS=active")
    print(f"REMINDER_TIME={config.time.display()}")
    print(f"REMINDER_CONFIG={REMINDER_SETTINGS_PATH}")
    print(f"LAUNCH_AGENT={agent_path}")
    print(f"OPEN_DASHBOARD={'yes' if config.open_dashboard else 'no'}")
    print("PRIVACY=提醒文字可能显示在锁屏；默认文案不包含健康详情")
    return 0


def status_reminder(_: argparse.Namespace) -> int:
    state = reminder_state()
    print(f"REMINDER_STATUS={state['status']}")
    print(f"PROFILE_ID={state['profile_id']}")
    print(f"REMINDER_TIME={state['time'] or ''}")
    print(f"REMINDER_CONFIG={state['config_path']}")
    print(f"LAUNCH_AGENT={state['agent_path']}")
    print(f"OPEN_DASHBOARD={'yes' if state['open_dashboard'] else 'no'}")
    return 0 if state["status"] in {"active", "disabled"} else 1


def _deliver_notification(config: ReminderConfig, settings: dict[str, Any]) -> None:
    environment = dict(os.environ)
    environment["HEALTHLOG_REMINDER_TITLE"] = "Local HealthLog"
    environment["HEALTHLOG_REMINDER_MESSAGE"] = " ".join(config.message.splitlines())
    script = (
        'display notification (system attribute "HEALTHLOG_REMINDER_MESSAGE") '
        'with title (system attribute "HEALTHLOG_REMINDER_TITLE")'
    )
    command = [_tool_path("HEALTHLOG_OSASCRIPT", "/usr/bin/osascript"), "-e", script]
    try:
        completed = subprocess.run(
            command,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise PipelineError(f"无法发送 macOS 通知：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise PipelineError(f"macOS 通知发送失败：{detail}")

    if config.open_dashboard:
        dashboard = site_root(settings) / "index.html"
        if dashboard.is_file():
            open_command = [
                _tool_path("HEALTHLOG_OPEN", "/usr/bin/open"),
                str(dashboard),
            ]
            try:
                subprocess.run(open_command, check=False, capture_output=True)
            except OSError as exc:
                raise PipelineError(f"通知已发送，但无法打开健康门户：{exc}") from exc

    last_fire = runtime_root(settings) / "reminders" / "last_fire.json"
    write_json(
        last_fire,
        {
            "schema_version": 1,
            "profile_id": config.profile_id,
            "fired_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dashboard_requested": config.open_dashboard,
        },
    )


def fire_reminder(_: argparse.Namespace) -> int:
    settings = load_settings()
    config = _load_config(settings, required=False)
    if config is None:
        print("REMINDER_FIRED=skipped-disabled")
        return 0
    _deliver_notification(config, settings)
    print("REMINDER_FIRED=yes")
    print(f"REMINDER_TIME={config.time.display()}")
    return 0


def test_reminder(_: argparse.Namespace) -> int:
    settings = load_settings()
    config = _load_config(settings, required=True)
    if config is None:  # defensive: required=True already rejects this state
        raise PipelineError("提醒配置缺失")
    _deliver_notification(config, settings)
    print("REMINDER_TEST=passed")
    print(f"REMINDER_TIME={config.time.display()}")
    return 0


def remove_reminder(_: argparse.Namespace) -> int:
    settings = load_settings()
    profile_id = _profile_id(settings)
    labels = {_current_agent_label(profile_id)}
    if REMINDER_SETTINGS_PATH.is_file():
        document = load_json(REMINDER_SETTINGS_PATH)
        configured_profile_id = document.get("profile_id")
        if not isinstance(configured_profile_id, str):
            raise PipelineError("提醒配置损坏，无法安全定位旧任务：profile_id 无效")
        try:
            configured = ReminderConfig.from_document(
                document, expected_profile_id=configured_profile_id
            )
        except ValueError as exc:
            raise PipelineError(f"提醒配置损坏，无法安全定位旧任务：{exc}") from exc
        labels.add(configured.agent_label)
    if sys.platform == "darwin":
        for label in sorted(labels):
            _run_launchctl(["bootout", _service_target(label)], check=False)
    try:
        for label in sorted(labels):
            _agent_path(label).unlink(missing_ok=True)
        REMINDER_SETTINGS_PATH.unlink(missing_ok=True)
    except OSError as exc:
        raise PipelineError(f"无法移除每日提醒：{exc}") from exc
    print("REMINDER_STATUS=disabled")
    print(f"REMOVED_CONFIG={REMINDER_SETTINGS_PATH}")
    print(f"REMOVED_LAUNCH_AGENTS={len(labels)}")
    return 0
