#!/usr/bin/env bash
set -euo pipefail

# Default workspace path used by GitHub Actions
GITHUB_WORKSPACE_DIR=${GITHUB_WORKSPACE:-/github/workspace}

# Input: single file path
INPUT_FILE=${INPUT_FILE:-}
if [[ -z "$INPUT_FILE" ]]; then
  echo "No file provided via INPUT_FILE"
  exit 1
fi

export HOME=${HOME:-/root}
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v pcb >/dev/null 2>&1 || { echo "pcb not found after install"; ls -la "$HOME/.local/bin" "/root/.local/bin" || true; exit 1; }
pcb --version

# Configure git safe directory to avoid "unsafe repository" errors in container
if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory "$GITHUB_WORKSPACE_DIR" || true
  git config --global --add safe.directory '*' || true
  git --version || true
fi

# Resolve file path (workspace-relative or absolute)
p="$INPUT_FILE"
if [[ -f "$GITHUB_WORKSPACE_DIR/$p" ]]; then
  pushd "$(dirname "$GITHUB_WORKSPACE_DIR/$p")" >/dev/null
  target="$(basename "$p")"
elif [[ -f "$p" ]]; then
  pushd "$(dirname "$p")" >/dev/null
  target="$(basename "$p")"
else
  echo "Target not found: $p"
  exit 1
fi

if [[ ! -f "$target" ]]; then
  echo "Target not found after resolution: $target"
  popd >/dev/null || true
  exit 1
fi

# Capture stdout to a temp file; stream stderr to console
tmp_stdout=$(mktemp)
if ! pcb release -f json "$target" > "$tmp_stdout" 2> >(tee /dev/stderr); then
  echo "Release failed for: $p"
  popd >/dev/null || true
  exit 1
fi

# Some logs may sneak into stdout; consider only the last 5 lines as the JSON payload
json_tail=$(tail -n 5 "$tmp_stdout")
echo "$json_tail"
archive=$(jq -r '.archive' <<< "$json_tail" 2>/dev/null || true)
version=$(jq -r '.version' <<< "$json_tail" 2>/dev/null || true)
if [[ -z "$archive" || ! -f "$archive" ]]; then
  echo "Release did not produce an archive for $p"
  popd >/dev/null || true
  exit 1
fi

# Expose outputs
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "archive=$archive"
    echo "version=$version"
  } >> "$GITHUB_OUTPUT"
fi

popd >/dev/null || true
