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

# Normalize to newline-separated list
NORMALIZED=$(printf "%s" "$RAW_PATHS" | tr ',' '\n' | sed '/^\s*$/d' | sed 's/^\s*//;s/\s*$//')
printf "%s\n" "$NORMALIZED" > "$GITHUB_WORKSPACE_DIR/paths.txt"

echo "Paths:"; cat "$GITHUB_WORKSPACE_DIR/paths.txt"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v pcb >/dev/null 2>&1 || { echo "pcb not found"; exit 1; }
pcb --version

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

  json_out=$(pcb release -f json "$target")
  echo "$json_out"
  archive=$(jq -r '.archive' <<< "$json_out")
  version=$(jq -r '.version' <<< "$json_out")
  if [[ -z "$archive" || ! -f "$archive" ]]; then
    echo "Release did not produce an archive for $p"
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
