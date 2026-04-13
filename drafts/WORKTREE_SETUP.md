# Worktree & Parallel Experiment Setup

**Date**: 2026-03-09

---

## Directory Layout

| Path | Branch | Purpose |
|------|--------|---------|
| `/Users/ghydsg/Desktop/xyz_proto_test/NexusAgent` | `improve/speed` | Speed improvement development & testing |
| `/Users/ghydsg/Desktop/xyz_proto_test/NexusAgent-gaia` | `feat/gaia-benchmark` | GAIA benchmark batch runs |

## How It Was Set Up

```bash
# From the original repo
cd /Users/ghydsg/Desktop/xyz_proto_test/NexusAgent
git worktree add ../NexusAgent-gaia feat/gaia-benchmark
```

### Symlinks (GAIA data is gitignored)

The GAIA test/validation data is not tracked by git, so symlinks were created in the worktree pointing back to the original repo:

```bash
ln -s /Users/ghydsg/Desktop/xyz_proto_test/NexusAgent/benchmark_test/gaia/test \
      /Users/ghydsg/Desktop/xyz_proto_test/NexusAgent-gaia/benchmark_test/gaia/test

ln -s /Users/ghydsg/Desktop/xyz_proto_test/NexusAgent/benchmark_test/gaia/validation \
      /Users/ghydsg/Desktop/xyz_proto_test/NexusAgent-gaia/benchmark_test/gaia/validation
```

### Results file

The existing results JSON was copied (not symlinked) into the worktree so the resume feature works:

```bash
cp NexusAgent/benchmark_test/gaia/results/gaia_test_batch_0-300.json \
   NexusAgent-gaia/benchmark_test/gaia/results/
```

---

## Running GAIA (in tmux)

```bash
tmux new -s gaia   # or: tmux attach -t gaia
cd /Users/ghydsg/Desktop/xyz_proto_test/NexusAgent-gaia
caffeinate -dims uv run python benchmark_test/gaia/scripts/batch_gaia.py \
    --start 0 --end 301 --split test \
    --resume benchmark_test/gaia/results/gaia_test_batch_0-300.json
```

- Detach: `Ctrl+B` then `D`
- Reattach: `tmux attach -t gaia`

---

## Running Speed Tests

```bash
cd /Users/ghydsg/Desktop/xyz_proto_test/NexusAgent   # improve/speed branch
# Start backend & frontend normally here
```

---

## Notes

- Both directories share the same `.git` — commits on either branch are visible to both.
- The backend (port 8000) can only serve one branch at a time. Don't run both backends simultaneously on the same port.
- GAIA batch runner connects to the backend via WebSocket on port 8000, so the backend must be running from the `feat/gaia-benchmark` branch (or compatible code) when running GAIA.
- To remove the worktree when done: `git worktree remove ../NexusAgent-gaia`

---

## Batch Script Fixes (in worktree)

Two fixes were applied to `NexusAgent-gaia/benchmark_test/gaia/scripts/batch_gaia.py`:

1. **Resume retries failures** — only `"success"` results are kept on resume; failed tasks are re-run
2. **Progress bar fix** — tracks only remaining tasks, not the full resumed set
3. **`outfile` crash fix** — saves results even when no new tasks ran

These changes are local to the worktree and not on the `improve/speed` branch.
