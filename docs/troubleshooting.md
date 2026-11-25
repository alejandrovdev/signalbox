# Troubleshooting signalbox

Part of [signalbox](../README.md).

Start with:

```sh
signalbox doctor
signalbox status
```

`doctor` checks the prerequisites (git 2.38 or later, `gh` installed and signed in, Claude
Code, the hooks), then each project: its folder, its git repository, its base on `origin`
and, with `merge-lane`, its gate and whether `origin` is on GitHub. Each line is `ok`,
`missing`, `problem` or `unknown`, with what to do; the exit code is 1 when something is
`missing` or a `problem`. A prerequisite no linked project needs (`gh` when no project
has `merge-lane` or `metrics`, Claude Code and the hooks when none has `session-brief` or
`merge-guard`) is shown with `(no project needs it)` and does not make it fail. It changes
nothing.

```text
Prerequisites
  ok       git 2.54
  ok       gh, signed in
  ok       Claude Code at /opt/homebrew/bin/claude
  ok       Claude Code hooks in ~/.claude/settings.json

shop
  ok       folder ~/Projects/shop
  ok       git repository
  ok       base branch origin/main
  ok       gate: make check
  ok       origin is on GitHub
```

## `no such ref: origin/main`

```text
signalbox: no such ref: origin/main; fetch it with: git fetch origin main
```

signalbox reads the base as last fetched and never fetches for the board, the brief, the
sweep or the metrics. Fetch it. When the base is not `main`, set `base` for the project, or
let git record `origin`'s default branch with `git remote set-head origin --auto`.

## The lane waits: the lock is busy

`lane: another merge is running; #42 waits for its turn.` means another run of the same
repository holds the lock; this one starts when it ends. `signalbox status` shows
`Lock: busy` meanwhile. The lock is released when the process that holds it ends, however it
ends, so a busy lock always means a live process. To see which:

```sh
lsof "$(git rev-parse --git-common-dir)/signalbox-lane.lock"
```

A gate that a killed lane left running is stopped by the next run, which says
`lane: stopped the gate a stopped run left running (pid <n>).`

## The merge is refused because the head moved

```text
lane: #42 not merged: gh pr merge refused it.
```

after `gh`'s own message: somebody pushed to the pull request after the lane checked it, and
`--match-head-commit` made GitHub refuse the merge. Nothing was merged; run
`signalbox merge 42` again to check the new head. A head that moved before the gate ran
stops the run earlier: `its head <commit> is no longer on <branch>; run again.`

## The pull request changes its own gate

```text
lane: #42 refused: it changes the gate (scripts/check.sh), which would check itself. Review that change, then run again with --allow-gate-change.
```

Read the change to the gate yourself, then `signalbox merge 42 --allow-gate-change`.

## The base moved during each attempt

`not merged: main moved during each of 3 attempts; run again.` Other merges landed while the
gate ran, three times. Run it again when the base is quieter; the metrics' `Most attempts`
shows how often this happens.

## The gate failed

The message gives the exit code (or the step, with `gate_failed_step`) and the file with the
whole output, under `~/Library/Logs/signalbox/<project>/gates/`. To try the gate on the
merged result without merging, run `signalbox merge 42 --dry-run`.

## A worktree is not swept

`signalbox sweep --dry-run` lists every merged worktree with the reason it stays (see
[the sweep](how-it-works.md#the-sweep)). A worktree whose branch is not merged into `origin/<base>` as last
fetched is not listed at all: fetch first.

## Sessions get no brief

- `signalbox doctor` must show the hooks as `ok`; otherwise run `signalbox setup`.
- The session's folder must be in a linked project with `session-brief`. Run
  `signalbox brief` in that folder: it prints what the hook would. It prints nothing outside
  a linked project or without the feature.

