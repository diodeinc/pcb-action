# pcb-action

Reusable GitHub Action to run `pcb release` for one or more boards.

## Usage

```yaml
- uses: diodeinc/pcb-action@v1
  with:
    # Newline or comma-separated list of paths to pass to `pcb release`.
    # Each path should point to either a `.zen` file or a directory containing exactly one `.zen`.
    paths: |
      boards/9M0001
      boards/9M0002/9M0002.zen
    # Optional: artifact name prefix (defaults to `pcb-releases`)
    artifact-name: pcb-releases
```

The action will:
- Install the latest `pcb` CLI
- For each provided path, resolve to a single `.zen` file
- Run `pcb release -f json <zen>` and parse the produced archive/version
- Collect all archives into a single artifact upload
