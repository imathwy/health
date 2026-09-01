#!/bin/zsh
set -euo pipefail

project_root=${0:A:h:h}
install_links=true
build_shortcut=true
open_shortcut=false
signed_shortcut=""
reminder_time=""
reminder_open_dashboard=false

while (( $# )); do
  case "$1" in
    --no-install)
      install_links=false
      ;;
    --skip-shortcut)
      build_shortcut=false
      ;;
    --open-shortcut)
      open_shortcut=true
      ;;
    --reminder-time)
      if (( $# < 2 )); then
        print -u2 -- "--reminder-time requires HH:MM"
        exit 64
      fi
      reminder_time=$2
      shift
      ;;
    --reminder-open-dashboard)
      reminder_open_dashboard=true
      ;;
    -h|--help)
      cat <<'HELP_EOF'
Usage: ./scripts/setup.sh [--no-install] [--skip-shortcut] [--open-shortcut]
                          [--reminder-time HH:MM] [--reminder-open-dashboard]

  --no-install      Do not create ~/.local/bin or ~/.codex/skills links
  --skip-shortcut   Do not build and sign the Apple Photos Shortcut
  --open-shortcut   Open the signed Shortcut for one-time import
  --reminder-time   Install a local daily reminder at HH:MM
  --reminder-open-dashboard
                    Open the local portal when the reminder fires
HELP_EOF
      exit 0
      ;;
    *)
      print -u2 -- "Unknown option: $1"
      exit 64
      ;;
  esac
  shift
done

if $reminder_open_dashboard && [[ -z "$reminder_time" ]]; then
  print -u2 -- "--reminder-open-dashboard requires --reminder-time HH:MM"
  exit 64
fi

if [[ $(uname -s) != Darwin ]]; then
  print -u2 -- "This Apple Photos workflow requires macOS."
  exit 1
fi

if ! /usr/bin/env python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  print -u2 -- "Python 3.10 or newer is required."
  exit 1
fi

/bin/mkdir -p \
  "$project_root/config" \
  "$project_root/data/daily" \
  "$project_root/data/profiles" \
  "$project_root/data/supplements" \
  "$project_root/runtime/daily" \
  "$project_root/runtime/profile" \
  "$project_root/runtime/reports/nutrition" \
  "$project_root/runtime/state" \
  "$project_root/site/daily" \
  "$project_root/site/health/assets" \
  "$project_root/site/nutrition" \
  "$project_root/site/profile" \
  "$project_root/build/shortcuts"

if [[ ! -f "$project_root/config/health_profile.json" ]]; then
  /bin/cp "$project_root/config/health_profile.example.json" \
    "$project_root/config/health_profile.json"
  print -- "Created private operational settings at config/health_profile.json."
else
  print -- "Kept existing config/health_profile.json."
fi

"$project_root/bin/diet" profile-init
"$project_root/bin/diet" profile

if [[ -d "$project_root/.git" ]]; then
  git -C "$project_root" config core.hooksPath .githooks
fi

link_if_available() {
  local source_path=$1
  local destination_path=$2

  if [[ -L "$destination_path" ]]; then
    local current_target
    current_target=$(/usr/bin/readlink "$destination_path")
    if [[ "$current_target" == "$source_path" ]]; then
      print -- "Link already installed: $destination_path"
    else
      print -u2 -- "Skipped existing link: $destination_path -> $current_target"
    fi
  elif [[ -e "$destination_path" ]]; then
    print -u2 -- "Skipped existing path: $destination_path"
  else
    /bin/ln -s "$source_path" "$destination_path"
    print -- "Installed link: $destination_path"
  fi
}

if $install_links; then
  /bin/mkdir -p "$HOME/.local/bin" "$HOME/.codex/skills"
  link_if_available "$project_root/bin/diet" "$HOME/.local/bin/diet"
  link_if_available \
    "$project_root/skills/daily-diet-pipeline" \
    "$HOME/.codex/skills/daily-diet-pipeline"
fi

if $build_shortcut; then
  shortcut_build_output=$(
    /usr/bin/env python3 "$project_root/scripts/build_shortcut.py" --sign
  )
  print -- "$shortcut_build_output"
  signed_shortcut=${shortcut_build_output##*$'\n'}
  /usr/bin/plutil -lint "$project_root/build/shortcuts/daily_photos_cli.xml"
  if $open_shortcut; then
    /usr/bin/open "$signed_shortcut"
  fi
fi

if [[ -n "$reminder_time" ]]; then
  reminder_args=(reminder set --time "$reminder_time")
  if $reminder_open_dashboard; then
    reminder_args+=(--open-dashboard)
  fi
  "$project_root/bin/diet" "${reminder_args[@]}"
fi

"$project_root/bin/diet" doctor

print -- "Setup complete."
if $build_shortcut && ! $open_shortcut; then
  print -- "Import the Shortcut once with:"
  print -- "  open '$signed_shortcut'"
fi
print -- "Then edit the generated PROFILE_JSON, run 'diet profile', and analyze a day with: diet yesterday"
if [[ -z "$reminder_time" ]]; then
  print -- "Optional daily reminder: diet reminder set --time 21:30"
fi
