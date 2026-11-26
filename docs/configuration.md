# Configuring signalbox

Part of [signalbox](../README.md). The configuration file and its keys, the environment
variables, and every file signalbox writes.

## The configuration file

One file, written by `setup` and `init` and editable by hand:

```text
~/Library/Application Support/signalbox/config.toml
```

Every command takes `--config <file>` (before or after its name) to use another one, and the
`SIGNALBOX_CONFIG` variable moves the default.

### One project

What `init` writes for a repository with a `Makefile` that has a `check` target:

```toml
[defaults]
preset = "recommended"

[projects.shop]
path = "~/Projects/shop"
preset = "recommended"
gate = "make check"
```

### Several projects

```toml
[defaults]
preset = "recommended"

[projects.shop]
path = "~/Projects/shop"
base = "main"
preset = "recommended"
gate = "pnpm install --frozen-lockfile && pnpm verify"
gate_env = { TEST_WORKERS = "4" }
gate_failed_step = 'FAILED at "(?P<step>[^"]+)"'
feature_docs = ["docs/features/*.md"]
build_output = ["node_modules", "dist", ".turbo", "coverage"]
merge_method = "squash"

[projects.blog]
path = "~/Projects/blog"
features = ["board", "sweep"]
```

Only `[defaults]` and `[projects.<name>]` tables may be at the top level.

### `[defaults]`

Optional. Projects do not inherit these values: they only fill in the screens of `init` for
a repository linked from then on.

| Key | Type | Rules |
| --- | --- | --- |
| `preset` | text | `minimal` or `recommended`. Not together with `features`. |
| `features` | list of text | Feature names, including the features each one needs. Not together with `preset`. |

### `[projects.<name>]`

The name has lowercase letters, digits and hyphens, starts with a letter or a digit, and is
at most 32 characters long.

| Key | Type | Required | Default and rules |
| --- | --- | --- | --- |
| `path` | text | yes | The repository's main checkout, absolute or starting with `~/`. `init` writes the full path. One project per folder. |
| `preset` | text | one of the two | `minimal` or `recommended`. |
| `features` | list of text | one of the two | At least one feature, including the features each one needs. |
| `base` | text | no | The base branch, as named on `origin`. Default: the branch `origin/HEAD` names (`git remote set-head origin --auto` records it), else `main`. In a repository with no remote at all, the local branch of that name is the base: the board, the brief, the sweep and the metrics work against it; the merge lane needs `origin` on GitHub. |
| `gate` | text | with `merge-lane` | The command the merge lane runs in the merge worktree; it passes with exit code 0. Not empty. |
| `gate_env` | table of text | no | Variables added to the gate's environment, for example `{ TEST_WORKERS = "4" }`. Each name is a shell variable name. |
| `gate_failed_step` | text | no | A regular expression with a group named `step`, read from a failed gate's output. The last match names the step that failed, in the lane's message, the commit status and the log. |
| `feature_docs` | list of text | no | Patterns of feature documents. Default: none. The board and the brief show, for each branch, the changed files that match (by name, without extension); the metrics count them as documentation. |
| `build_output` | list of text | no | Ignored paths a new worktree builds again: the sweep removes a merged worktree whose ignored files all match. Default: `.DS_Store`, `__pycache__`, `*.pyc`, `.pytest_cache`, `node_modules`, `dist`, `build`, `coverage`, `.next`, `.turbo`, `.astro`, `*.tsbuildinfo`. Giving the key replaces that list: repeat what you still want. |
| `merge_method` | text | no | How the lane merges on GitHub: `merge` (the default), `squash` or `rebase`. |
| `merge_worktree` | text | no | Where the lane merges. Default: `~/Library/Caches/signalbox/<project>/merge.noindex`. |
| `gate_files` | list of text | no | Patterns of files whose change counts as a change to the gate (see [the gate-change check](how-it-works.md#the-merge-lane)), besides those the gate command shows. Default: none. |
| `legacy_locks` | list of text | no | Lock files of another merge tool in the repository's git folder, which the lane takes too, so the two never run at once while you move from one to the other. Default: none. |

In patterns (`feature_docs`, `build_output`), `*` and `?` stay within one folder and `**`
crosses folders. A pattern with no slash matches a name anywhere (`node_modules`,
`*.tsbuildinfo`); one with a slash matches the path from the worktree's top
(`docs/features/*.md`).

### `[groups.<name>]`

Repositories that depend on each other (an API and the apps that call it, say) form a group:

```toml
[groups.platform]
projects = ["api", "web", "mobile"]
```

`projects` names linked projects in dependency order: a project merges after those before it.
A group's name follows the project-name rule; a group names at least two projects, and a
project is in one group at most. `init` renames a project inside its group too; `deinit`
takes it out of its group and removes a group left with fewer than two, and says so. See
[Repositories that depend on each other](how-it-works.md#repositories-that-depend-on-each-other).

### After you edit the file

Every command, and every hook, reads the file each time it runs: there is nothing to reload
or restart. Unknown keys are errors. A command that reads the file reports each problem by
its key, one per line, and exits with 1:

```text
~/Library/Application Support/signalbox/config.toml: projects.Blog: a project's name has lowercase letters, digits and hyphens, at most 32
~/Library/Application Support/signalbox/config.toml: projects.web.features: "merge-guard" needs "merge-lane"
~/Library/Application Support/signalbox/config.toml: projects.web.merge_method: unknown method "fast"; one of: merge, squash, rebase
~/Library/Application Support/signalbox/config.toml: projects.web.colour: unknown key
```

Reading the file checks its shape only. Whether a folder exists, the base is on `origin` or
a tool is installed is what `signalbox doctor` checks: run it after an edit. When you turn on
`session-brief` or `merge-guard` by hand, check that the hooks are installed (`doctor` says;
`signalbox setup` installs them).

## Environment variables

These variables move the files signalbox keeps outside your repositories, or name the
programs it runs:

| Variable | Default |
| --- | --- |
| `SIGNALBOX_CONFIG` | `~/Library/Application Support/signalbox/config.toml` |
| `SIGNALBOX_LOG_DIR` | `~/Library/Logs/signalbox` |
| `SIGNALBOX_CACHE_DIR` | `~/Library/Caches/signalbox`, where the merge worktrees go |
| `SIGNALBOX_CLAUDE_SETTINGS` | `~/.claude/settings.json`, where the hooks go |
| `SIGNALBOX_CLAUDE_PROJECTS` | `~/.claude/projects`, where the sweep reads Claude Code's agent records |
| `SIGNALBOX_SESSIONS_DIR` | `~/Library/Application Support/signalbox/sessions`, where the hooks record each session |
| `SIGNALBOX_GIT`, `SIGNALBOX_GH`, `SIGNALBOX_CLAUDE`, `SIGNALBOX_BREW` | the program found in `PATH` or Homebrew's folders (a path to no program counts as missing) |

A rehearsal that must never touch your own configuration, logs, caches or Claude Code
settings points the first six at a scratch folder: see
[CONTRIBUTING.md](../CONTRIBUTING.md#try-a-change-apart-from-your-install).

## Files it writes

| Where | What |
| --- | --- |
| `~/Library/Application Support/signalbox/config.toml` | the configuration; the folder holds a `.signalbox` file that marks it as signalbox's |
| `~/Library/Application Support/signalbox/sessions/<session id>.json` | where each Claude Code session stands, written by the hooks (see [the sweep](how-it-works.md#the-sweep)) |
| `~/Library/Logs/signalbox/<project>/lane.jsonl` | the lane log, one JSON line per run |
| `~/Library/Logs/signalbox/<project>/gates/` | the whole output of each failed gate |
| `~/Library/Caches/signalbox/<project>/merge.noindex` | the merge worktree (unless `merge_worktree` places it) |
| `~/.claude/settings.json` | the three user-level hooks (`setup`) |
| `<repository>/.claude/settings.json` | the shared hooks, only with `init --shared-hooks` |

In each linked repository's git folder (`git rev-parse --git-common-dir`, usually `.git`):

| File | What |
| --- | --- |
| `signalbox-lane.lock` | the lane's lock: one run at a time across all worktrees |
| `signalbox-lane.json` | the pid of the gate a run is running, removed when it ends; how the next run finds a gate a killed run left |
| `signalbox-changes.json`, in each worktree's own git folder | what `changes` last saw in that worktree |
| `worktrees/merge.noindex/` | git's record of the merge worktree, as for any worktree |

On GitHub, the lane posts the commit status `signalbox/merge-lane` on each head it checks,
and merges through `gh`. Apart from these files and what the sweep removes, nothing is
written into your repositories, and no branch is ever deleted on GitHub.
