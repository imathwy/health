#!/usr/bin/env python3

"""Build the unsigned XML plist for the CLI-first daily Photos shortcut."""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
OBJECT_REPLACEMENT = "\uFFFC"

NORMALIZE_UUID = "A990B073-65E5-49E3-B49D-7E62D02437BF"
START_DATE_UUID = "41B8D99A-AC8A-4A85-A722-16A2EBBE8F0D"
FIND_PHOTOS_UUID = "525A71EA-3636-49F4-870D-BC81BBC07083"
SAVE_FILES_UUID = "A77F3FFA-EE58-4880-A510-9383BD5DB587"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and optionally sign the daily Photos Shortcut"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Health-log repository root (default: inferred from this script)",
    )
    parser.add_argument(
        "--daily-directory",
        default="data/daily",
        help="Photo output directory, relative to --root unless absolute",
    )
    parser.add_argument("--shortcut-name", default="导出每日照片 CLI")
    parser.add_argument("--output", type=Path, help="Unsigned XML output path")
    parser.add_argument("--signed-output", type=Path, help="Signed output path")
    parser.add_argument("--sign", action="store_true", help="Sign with shortcuts CLI")
    parser.add_argument(
        "--signing-mode",
        choices=("anyone", "people-who-know-me"),
        default="anyone",
    )
    return parser.parse_args()


ARGS = parse_args()
PROJECT_ROOT = ARGS.root.expanduser().resolve()
DAILY_ROOT = (PROJECT_ROOT / ARGS.daily_directory).expanduser().resolve()
OUTPUT_PATH = (
    ARGS.output.expanduser().resolve()
    if ARGS.output
    else PROJECT_ROOT / "build" / "shortcuts" / "daily_photos_cli.xml"
)
SIGNED_OUTPUT_PATH = (
    ARGS.signed_output.expanduser().resolve()
    if ARGS.signed_output
    else PROJECT_ROOT / "build" / "shortcuts" / f"{ARGS.shortcut_name}.shortcut"
)


def action_output(output_uuid: str, output_name: str) -> dict[str, str]:
    return {
        "OutputName": output_name,
        "OutputUUID": output_uuid,
        "Type": "ActionOutput",
    }


def attachment(value: dict[str, str]) -> dict[str, object]:
    return {
        "Value": value,
        "WFSerializationType": "WFTextTokenAttachment",
    }


def token_string(text: str, references: list[dict[str, str]]) -> dict[str, object]:
    positions = [index for index, char in enumerate(text) if char == OBJECT_REPLACEMENT]
    if len(positions) != len(references):
        raise ValueError("Token placeholder and reference counts do not match")

    return {
        "Value": {
            "string": text,
            "attachmentsByRange": {
                f"{{{position}, 1}}": reference
                for position, reference in zip(positions, references, strict=True)
            },
        },
        "WFSerializationType": "WFTextTokenString",
    }


def comment(text: str) -> dict[str, object]:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.comment",
        "WFWorkflowActionParameters": {"WFCommentActionText": text},
    }


def signing_command(input_path: Path, output_path: Path) -> list[str]:
    return [
        "shortcuts",
        "sign",
        "--mode",
        ARGS.signing_mode,
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]


def sign_workflow() -> None:
    """Sign XML, retrying with a binary plist when macOS rejects XML input."""

    SIGNED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="healthlog-shortcut-", dir=SIGNED_OUTPUT_PATH.parent
    ) as raw_temp_dir:
        temp_dir = Path(raw_temp_dir)
        xml_output = temp_dir / "signed-from-xml.shortcut"
        direct = subprocess.run(
            signing_command(OUTPUT_PATH, xml_output),
            text=True,
            capture_output=True,
            check=False,
        )
        if direct.returncode == 0 and xml_output.exists():
            os.replace(xml_output, SIGNED_OUTPUT_PATH)
            return

        binary_input = temp_dir / "unsigned-binary.shortcut"
        binary_output = temp_dir / "signed-from-binary.shortcut"
        shutil.copyfile(OUTPUT_PATH, binary_input)
        subprocess.run(
            ["plutil", "-convert", "binary1", str(binary_input)], check=True
        )
        fallback = subprocess.run(
            signing_command(binary_input, binary_output),
            text=True,
            capture_output=True,
            check=False,
        )
        if fallback.returncode != 0 or not binary_output.exists():
            direct_detail = (direct.stderr or direct.stdout).strip()
            fallback_detail = (fallback.stderr or fallback.stdout).strip()
            raise RuntimeError(
                "Shortcut signing failed for XML and binary plist inputs:\n"
                f"XML: {direct_detail or 'no details'}\n"
                f"Binary: {fallback_detail or 'no details'}"
            )
        os.replace(binary_output, SIGNED_OUTPUT_PATH)
        print("Signing used the binary plist fallback.")


normalize_script = r'''set -euo pipefail

raw_date=$(cat)
raw_date=$(printf '%s' "$raw_date" | /usr/bin/tr -d '[:space:]')

case "$raw_date" in
  ""|today)
    raw_date=$(/bin/date +%F)
    ;;
  yesterday)
    raw_date=$(/bin/date -v-1d +%F)
    ;;
esac

if [[ ! "$raw_date" =~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' ]]; then
  print -u2 -- "Date must be an ISO calendar date, today, or yesterday"
  exit 64
fi

parsed_date=$(/bin/date -j -f '%Y-%m-%d' "$raw_date" +%F 2>/dev/null) || {
  print -u2 -- "Invalid calendar date: $raw_date"
  exit 64
}

if [[ "$parsed_date" != "$raw_date" ]]; then
  print -u2 -- "Invalid calendar date: $raw_date"
  exit 64
fi

/usr/bin/printf '%s' "$raw_date"'''

save_script = f'''set -euo pipefail

target_date='{OBJECT_REPLACEMENT}'
compact_date=${{target_date//-/}}
daily_root={shlex.quote(str(DAILY_ROOT))}
destination="$daily_root/$compact_date"
/bin/mkdir -p "$destination"

copied=0
skipped=0

for source_path in "$@"; do
  [[ -f "$source_path" ]] || continue
  source_name=${{source_path:t}}
  target_path="$destination/$source_name"

  if [[ -e "$target_path" ]]; then
    (( skipped += 1 ))
  else
    /bin/cp -p "$source_path" "$target_path"
    (( copied += 1 ))
  fi
done

/usr/bin/printf 'DATE=%s\\nCOUNT=%s\\nCOPIED_FILES=%s\\nSKIPPED_FILES=%s\\nEXPORT_DIR=%s\\n' \\
  "$target_date" "$#" "$copied" "$skipped" "$destination"'''


actions: list[dict[str, object]] = [
    comment(
        "CLI-first daily photo export\n"
        "- Accepts an ISO calendar date, today, or yesterday as Shortcut Input\n"
        "- Empty input uses today's local date\n"
        "- Runs without date or save-location dialogs"
    ),
    comment(
        "Shortcuts generated by Shortcuts Playground. May contain mistakes. "
        "Always check the shortcut's actions first.\n\n"
        "This shortcut was created via the following user prompt:\n\n"
        "> 重新生成、校验并签名无弹窗的 Photos Shortcut"
    ),
    {
        "WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
        "WFWorkflowActionParameters": {
            "UUID": NORMALIZE_UUID,
            "Input": attachment({"Type": "ExtensionInput"}),
            "InputMode": "to stdin",
            "RunAsRoot": False,
            "Script": normalize_script,
            "Shell": "/bin/zsh",
        },
    },
    comment(
        "Build the local calendar date\n"
        "- Start Date uses the validated date above\n"
        "- Photos compares Date Taken with this calendar date"
    ),
    {
        "WFWorkflowActionIdentifier": "is.workflow.actions.detect.date",
        "WFWorkflowActionParameters": {
            "UUID": START_DATE_UUID,
            "CustomOutputName": "Start Date",
            "WFInput": attachment(
                action_output(NORMALIZE_UUID, "Shell Script Result")
            ),
        },
    },
    comment(
        "Find relevant Photos items\n"
        "- Date Taken matches Start Date as a calendar day\n"
        "- Screenshots and ordinary videos are excluded\n"
        "- Live Photos remain eligible"
    ),
    {
        "WFWorkflowActionIdentifier": "is.workflow.actions.filter.photos",
        "WFWorkflowActionParameters": {
            "UUID": FIND_PHOTOS_UUID,
            "WFContentItemFilter": {
                "Value": {
                    "WFActionParameterFilterPrefix": 1,
                    "WFContentPredicateBoundedDate": False,
                    "WFActionParameterFilterTemplates": [
                        {
                            "Operator": 4,
                            "Property": "Is a Screenshot",
                            "Removable": True,
                            "Values": {"Bool": False, "Unit": 4},
                        },
                        {
                            "Operator": 5,
                            "Property": "Media Type",
                            "Removable": True,
                            "Values": {
                                "Unit": 4,
                                "Enumeration": {
                                    "Value": "Video",
                                    "WFSerializationType": "WFStringSubstitutableState",
                                },
                            },
                        },
                        {
                            "Operator": 4,
                            "Property": "Date Taken",
                            "Removable": True,
                            "Values": {
                                "Date": attachment(
                                    action_output(START_DATE_UUID, "Start Date")
                                ),
                                "Unit": 4,
                            },
                        },
                    ],
                },
                "WFSerializationType": "WFContentPredicateTableTemplate",
            },
            "WFContentItemLimitEnabled": False,
            "WFContentItemSortOrder": "Oldest First",
            "WFContentItemSortProperty": "Date Taken",
        },
    },
    comment(
        "Save without asking for a location\n"
        "- Input uses the Photos found above\n"
        "- Existing files are preserved and skipped\n"
        "- Output reports the date, counts, and destination"
    ),
    {
        "WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
        "WFWorkflowActionParameters": {
            "UUID": SAVE_FILES_UUID,
            "Input": attachment(action_output(FIND_PHOTOS_UUID, "Photos")),
            "InputMode": "as arguments",
            "RunAsRoot": False,
            "Script": token_string(
                save_script,
                [action_output(NORMALIZE_UUID, "Shell Script Result")],
            ),
            "Shell": "/bin/zsh",
        },
    },
]


workflow: dict[str, object] = {
    "WFWorkflowActions": actions,
    "WFWorkflowClientVersion": "2700.0.4",
    "WFWorkflowHasOutputFallback": False,
    "WFWorkflowIcon": {
        "WFWorkflowIconGlyphNumber": 61459,
        "WFWorkflowIconStartColor": 4292093695,
    },
    "WFWorkflowImportQuestions": [],
    "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
    "WFWorkflowMinimumClientVersion": 900,
    "WFWorkflowMinimumClientVersionString": "900",
    "WFWorkflowName": ARGS.shortcut_name,
    "WFWorkflowOutputContentItemClasses": ["WFStringContentItem"],
    "WFWorkflowTypes": [],
}


OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
with OUTPUT_PATH.open("wb") as output_file:
    plistlib.dump(workflow, output_file, fmt=plistlib.FMT_XML, sort_keys=False)

print(OUTPUT_PATH)

if ARGS.sign:
    sign_workflow()
    print(SIGNED_OUTPUT_PATH)
