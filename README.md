# pcb-action

Reusable GitHub Action to run `pcb release` for a single board `.zen` file.

## Usage

```yaml
- uses: diodeinc/pcb-action@v1
  with:
    # Path to a `.zen` file (absolute or repo-relative)
    file: boards/DIO0002/DIO0002.zen
```

The action will:

- Install the latest `pcb` CLI
- Resolve the provided path to a `.zen` file
- Run `pcb release -f json <zen>` and parse the produced `archive` and `version`
- Expose outputs: `archive` (absolute path) and `version`

## Reusable workflow

This repository also provides a reusable workflow that discovers boards under `boards/*/*.zen`, fans out to release each board, and uploads one artifact per board.

In your repository, create a workflow that calls it:

```yaml
name: PCB Releases

on:
  workflow_dispatch:

jobs:
  call-release:
    uses: diodeinc/pcb-action/.github/workflows/pcb-release.yml@v1
```
