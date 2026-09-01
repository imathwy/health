#!/usr/bin/env python3

"""Reject private data, binary media, secrets, and home paths from Git."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_PUBLIC_FILE_BYTES = 1_000_000
ALLOWED_DATA_FILES = {"data/README.md"}
ALLOWED_RUNTIME_FILES = {"runtime/README.md"}
ALLOWED_SITE_FILES = {"site/README.md"}
FORBIDDEN_EXACT = {
    ".env",
    "config/health_profile.json",
    "config/reminder.local.json",
}
FORBIDDEN_PREFIXES = (
    "build/",
    "daily/",
    "tmp/",
    "检查/",
    "补剂/",
)
FORBIDDEN_SUFFIXES = {
    ".avif",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".m4v",
    ".mov",
    ".mp4",
    ".pdf",
    ".png",
    ".shortcut",
    ".tif",
    ".tiff",
    ".webp",
}
SENSITIVE_TEXT = {
    "absolute home path": re.compile(rb"/(?:Users|home)/[^/\s\"']+"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "generic credential": re.compile(
        rb"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
        rb"\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{16,}"
    ),
}


def git_bytes(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def listed_paths(staged: bool) -> list[str]:
    if staged:
        raw = git_bytes(
            "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"
        )
    else:
        raw = git_bytes("ls-files", "-z")
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def blob(path: str, staged: bool) -> bytes:
    if staged:
        return git_bytes("show", f":{path}")
    return (ROOT / path).read_bytes()


def audit_path(path: str, payload: bytes) -> list[str]:
    errors: list[str] = []
    normalized = PurePosixPath(path).as_posix()
    suffix = PurePosixPath(normalized).suffix.lower()

    if normalized in FORBIDDEN_EXACT:
        errors.append("private local file")
    if normalized.startswith("data/") and normalized not in ALLOWED_DATA_FILES:
        errors.append("private data directory")
    if (
        normalized.startswith("runtime/")
        and normalized not in ALLOWED_RUNTIME_FILES
    ):
        errors.append("private runtime directory")
    if normalized.startswith("site/") and normalized not in ALLOWED_SITE_FILES:
        errors.append("private display directory")
    if normalized.startswith(FORBIDDEN_PREFIXES):
        errors.append("private or generated directory")
    if suffix in FORBIDDEN_SUFFIXES:
        errors.append(f"binary media type {suffix}")
    if len(payload) > MAX_PUBLIC_FILE_BYTES:
        errors.append(f"file exceeds {MAX_PUBLIC_FILE_BYTES} bytes")
    if b"\x00" in payload:
        errors.append("binary content")
    for label, pattern in SENSITIVE_TEXT.items():
        if pattern.search(payload):
            errors.append(label)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--staged", action="store_true", help="Audit the staged snapshot"
    )
    args = parser.parse_args()

    failures: list[tuple[str, list[str]]] = []
    for path in listed_paths(args.staged):
        try:
            payload = blob(path, args.staged)
        except (OSError, subprocess.CalledProcessError) as exc:
            failures.append((path, [f"cannot read: {exc}"]))
            continue
        reasons = audit_path(path, payload)
        if reasons:
            failures.append((path, reasons))

    if failures:
        print("Privacy check failed:", file=sys.stderr)
        for path, reasons in failures:
            print(f"- {path}: {', '.join(reasons)}", file=sys.stderr)
        return 1

    print(f"Privacy check passed ({len(listed_paths(args.staged))} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
