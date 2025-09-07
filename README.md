# pcb-action

Reusable composite GitHub Actions for PCB projects using the `pcb` CLI. This repo provides:

- `setup`: Installs the `pcb` CLI on the runner
- `detect`: Discovers boards defined in the repo
- `release`: Creates a release archive for a given board and optionally uploads it as an artifact

Use these individually or together in your workflows.

## Actions

### setup

Installs the `pcb` CLI.

- Inputs:
  - `version` (optional, default: `latest`) — version of `pcb` to install.

Usage:

```yaml
- name: Setup pcb CLI
  uses: diodeinc/pcb-action/setup@v1
  with:
    # version: 0.XX.X   # optional; installs latest by default
```

### detect

Detects boards in the repository and outputs a JSON array of board names.

- Outputs:
  - `boards` — JSON array of board names (e.g. `["board_a", "board_b"]`).

Usage:

```yaml
- name: Detect boards
  id: detect
  uses: diodeinc/pcb-action/detect@v1

# Example: build a matrix from detected boards
strategy:
  matrix:
    board: ${{ fromJson(steps.detect.outputs.boards) }}
```

### release

Creates a release archive for a specified board.

- Inputs:
  - `board` (required) — board name to release (as reported by `pcb info`).
  - `upload` (optional, default: `true`) — upload the archive as a workflow artifact.

- Outputs:
  - `archive_path` — the created archive filename in the workspace (e.g. `myboard-<short_sha>.zip`).

Usage:

```yaml
- name: Create PCB release
  id: release
  uses: diodeinc/pcb-action/release@v1
  with:
    board: my_board
    # upload: false  # set to false to skip artifact upload
```
