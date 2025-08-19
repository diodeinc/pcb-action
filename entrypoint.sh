#!/usr/bin/env bash
set -euo pipefail

# Default workspace path used by GitHub Actions
GITHUB_WORKSPACE_DIR=${GITHUB_WORKSPACE:-/github/workspace}
ARTIFACTS_DIR="$GITHUB_WORKSPACE_DIR/artifacts"

mkdir -p "$ARTIFACTS_DIR"

# Inputs are exposed as env vars in Docker actions: INPUT_PATHS
RAW_PATHS=${INPUT_PATHS:-}
if [[ -z "$RAW_PATHS" ]]; then
  echo "No paths provided via INPUT_PATHS"
  exit 1
fi

# Normalize to newline-separated list; interpret literal \n
NORMALIZED=$(printf "%b" "$RAW_PATHS" | tr ',' '\n' | sed '/^\s*$/d' | sed 's/^\s*//;s/\s*$//')
printf "%s\n" "$NORMALIZED" > "$GITHUB_WORKSPACE_DIR/paths.txt"

echo "Paths:"; cat "$GITHUB_WORKSPACE_DIR/paths.txt"

export HOME=${HOME:-/root}
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
# If pcb is missing, install at runtime as a fallback
if ! command -v pcb >/dev/null 2>&1; then
  echo "pcb not found; installing..."
  if ! command -v curl >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y --no-install-recommends curl ca-certificates jq
  fi
  curl --proto '=https' --tlsv1.2 -LsSf https://github.com/diodeinc/pcb/releases/latest/download/pcb-installer.sh | HOME=/root sh
  # Try common install locations
  if [[ ! -x "$HOME/.local/bin/pcb" && -x "/root/.local/bin/pcb" ]]; then
    export PATH="/root/.local/bin:/root/.cargo/bin:$PATH"
  fi
fi
command -v pcb >/dev/null 2>&1 || { echo "pcb not found after install"; ls -la "$HOME/.local/bin" "/root/.local/bin" || true; exit 1; }
pcb --version

# Configure git safe directory to avoid "unsafe repository" errors in container
if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory "$GITHUB_WORKSPACE_DIR" || true
  git config --global --add safe.directory '*' || true
  git --version || true
fi

failed=0
failed_list=()

while IFS= read -r p; do
  echo "Processing: $p"
  if [[ -d "$GITHUB_WORKSPACE_DIR/$p" ]]; then
    pushd "$GITHUB_WORKSPACE_DIR/$p" >/dev/null
    shopt -s nullglob
    zens=( *.zen )
    if [[ ${#zens[@]} -ne 1 ]]; then
      echo "Expected exactly one .zen file in $p, found ${#zens[@]} — skipping"
      popd >/dev/null
      continue
    fi
    target="${zens[0]}"
  else
    # Support both absolute and workspace-relative paths
    if [[ -f "$GITHUB_WORKSPACE_DIR/$p" ]]; then
      pushd "$(dirname "$GITHUB_WORKSPACE_DIR/$p")" >/dev/null
      target="$(basename "$p")"
    elif [[ -f "$p" ]]; then
      pushd "$(dirname "$p")" >/dev/null
      target="$(basename "$p")"
    else
      echo "Target not found: $p — skipping"
      continue
    fi
  fi

  if [[ ! -f "$target" ]]; then
    echo "Target not found after resolution: $target — skipping"
    popd >/dev/null || true
    continue
  fi

  # Capture stdout to a temp file; stream stderr to console
  tmp_stdout=$(mktemp)
  if ! pcb release -f json "$target" > "$tmp_stdout" 2> >(tee /dev/stderr); then
    echo "Release failed for: $p"
    # Errors already printed to stderr
    failed=1
    failed_list+=("$p")
    popd >/dev/null || true
    continue
  fi
  # Some logs may sneak into stdout; consider only the last 5 lines as the JSON payload
  json_tail=$(tail -n 5 "$tmp_stdout")
  echo "$json_tail"
  archive=$(jq -r '.archive' <<< "$json_tail" 2>/dev/null || true)
  version=$(jq -r '.version' <<< "$json_tail" 2>/dev/null || true)
  if [[ -z "$archive" || ! -f "$archive" ]]; then
    echo "Release did not produce an archive for $p"
    failed=1
    failed_list+=("$p")
    popd >/dev/null || true
    continue
  fi
  base_name=$(basename "${target%.zen}")
  dest="$ARTIFACTS_DIR/${base_name}-${version}.zip"
  cp "$archive" "$dest"
  echo "Collected artifact: $dest"
  popd >/dev/null || true

done < "$GITHUB_WORKSPACE_DIR/paths.txt"

# Expose outputs
FILES=$(ls -1 "$ARTIFACTS_DIR" 2>/dev/null | sed "s#^#$ARTIFACTS_DIR/#" || true)
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "artifact-dir=$ARTIFACTS_DIR"
    printf "artifact-files<<EOF\n%s\nEOF\n" "$FILES"
  } >> "$GITHUB_OUTPUT"
fi

if [[ $failed -ne 0 ]]; then
  echo "One or more boards failed to release:"
  printf ' - %s\n' "${failed_list[@]}"
  exit 1
fi
