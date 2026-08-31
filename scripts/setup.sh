#!/bin/zsh
set -euo pipefail

project_root=${0:A:h:h}
install_links=true
build_shortcut=true
open_shortcut=false
signed_shortcut=""

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
    -h|--help)
      cat <<'HELP_EOF'
Usage: ./scripts/setup.sh [--no-install] [--skip-shortcut] [--open-shortcut]

  --no-install      Do not create ~/.local/bin or ~/.codex/skills links
  --skip-shortcut   Do not build and sign the Apple Photos Shortcut
  --open-shortcut   Open the signed Shortcut for one-time import
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
  "$project_root/data/medical" \
  "$project_root/data/supplements" \
  "$project_root/runtime/daily" \
  "$project_root/runtime/reports/nutrition" \
  "$project_root/runtime/state" \
  "$project_root/site/daily" \
  "$project_root/site/health/assets" \
  "$project_root/site/nutrition" \
  "$project_root/build/shortcuts"

if [[ ! -f "$project_root/config/health_profile.json" ]]; then
  /bin/cp "$project_root/config/health_profile.example.json" \
    "$project_root/config/health_profile.json"
  print -- "Created config/health_profile.json; edit its targets before relying on reports."
else
  print -- "Kept existing config/health_profile.json."
fi

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

"$project_root/bin/diet" doctor

print -- "Setup complete."
if $build_shortcut && ! $open_shortcut; then
  print -- "Import the Shortcut once with:"
  print -- "  open '$signed_shortcut'"
fi
print -- "Then edit config/health_profile.json and run: diet yesterday"
