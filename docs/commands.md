# signalbox commands

Part of [signalbox](../README.md). What `setup` and `init` ask, every command with its
flags and exit codes, how to uninstall, and what stays stable from one release to the next.

## Setting up and linking

### What `setup` asks

1. **Prerequisites**: git (2.38 or later), gh signed in, Claude Code, and the Claude Code
   hooks, each marked `ok`, `missing`, `problem` or `unknown` with what to do about it. When
   git (missing, or older than 2.38), gh or Claude Code is missing and Homebrew is there, a
   warning closes the screen; without Homebrew nothing is offered, and the screen points to
   [brew.sh](https://brew.sh).
2. **Install what is missing** (only then): `Install with Homebrew` (`brew install git`,
   `brew install gh`, `brew install --cask claude-code`) or `Skip`, which it starts on.
   Nothing runs yet.
3. **Default features**: a checklist of the [features](../README.md#features-and-presets), starting
   from the defaults already saved, else from `recommended`. Turning one on turns on what it
   needs; turning one off turns off what needs it, and says why. At least one stays on.
4. **Summary**: what will be installed, the default features, where the hooks go (or `not installed (--no-hooks)`),
   and the projects already linked, which keep their own features. `Save these defaults?`:
   `y` saves; `n` changes nothing.

After the summary's `y`, the installs run first, with their output on screen; a failed
install is reported, the rest still runs, and `setup` exits with 1. Signing in stays yours:
`gh auth login` for gh, and `claude` once after Claude Code is installed. `--yes` alone
installs nothing; `--install-missing` installs. Then `setup` saves and installs the hooks in `~/.claude/settings.json` (unless `--no-hooks`),
keeping every other setting and hook of that file, and says `Next: in a repository's
folder, run: signalbox init`.

### What `init` asks

1. **Project name**: filled in from the folder's name. Lowercase letters, digits and
   hyphens, at most 32; numbered (`shop-2`) when another project has the name.
2. **Features**: start from the default features of `setup` (or `recommended` when none
   were saved); for a repository linked already, from its own.
3. **Gate**: asked only when the features include `merge-lane`. It starts from what the
   repository has, in this order: a `verify` or `check` script in `package.json` (run with
   `pnpm` when there is a `pnpm-lock.yaml`, `yarn` with a `yarn.lock`, else `npm run`), a
   `check` or `verify` target in a `Makefile` (`make check` or `make verify`), or `scripts/verify.sh`
   (`bash scripts/verify.sh`). With none of them, the field is empty and you type one.
4. **Summary**: the project, its folder, its base (`origin/<branch>`), its features and its
   gate. The question is `Link this repository?`, or `Replace its settings?` for a
   repository linked already (`Rename it?` when you changed its name).

The screens are drawn when your terminal can draw them: up and down move, Enter chooses or
accepts, Space turns a feature on or off, Esc goes back, `q` quits (Ctrl-C in a text field).
Otherwise they are plain questions: type the answer and press Enter, `<` goes back, `q` or
Ctrl-C quits. Quitting, or answering `n` at the summary, prints `Nothing was changed.` and
exits with 1.

### `signalbox init [folder]`

- Links the git repository a folder is in: the current folder, or the one you name. Run it
  in the main checkout or in any worktree: the project is always the repository's main
  checkout, and every worktree of the repository belongs to it, wherever the worktree is.
- Outside a git repository it refuses, with exit code 1:

  ```text
  ~/notes is not in a git repository: run git init in it first. Nothing was changed.
  ```

- In a repository linked already, it edits that project: the screens start from its current
  values, and the keys `init` does not ask (`gate_env`, `build_output` and the others) are
  kept. A new name renames the project.
- After saving it prints `Linked "shop" (<folder>) in <configuration file>.` When the
  project has `session-brief` or `merge-guard` and the hooks are not installed, it says to
  run `signalbox setup`.

Flags answer the screens beforehand, so `init` can run without asking anything:

```sh
signalbox init ~/Projects/shop --name shop --preset recommended --gate "pnpm verify" --yes
```

| Flag | Answers |
| --- | --- |
| `--name <name>` | the project's name |
| `--preset minimal\|recommended` or `--feature <feature>` | its features: a preset, or each feature with its own `--feature`, including the features each one needs |
| `--gate <command>` | the gate the merge lane runs |
| `--base <branch>` | the base branch (no screen asks for it; default: the branch `origin/HEAD` names, else `main`) |
| `--shared-hooks` | also write the hooks in the repository's `.claude/settings.json` (see [the hooks](how-it-works.md#the-session-brief-and-the-hooks)) |
| `--yes` | every other screen takes the value it starts from, and the summary is confirmed |

Even with `--yes`, a screen with nothing to start from (a gate the repository does not
suggest, for example) is still asked. When nothing can be typed (no terminal, or the input
ended), the command stops with exit code 2 and names the flag:

```text
signalbox init: error: nothing answered "Gate": give --gate
```

A list of features that leaves out one they need is refused the same way:
`argument --feature: "merge-guard" needs "merge-lane"`.

### `signalbox setup`

`setup` changes only `[defaults]` (what new projects start from) and the hooks; linked
projects keep their own features.

| Flag | Answers |
| --- | --- |
| `--preset minimal\|recommended` or `--feature <feature>` | the default features |
| `--no-hooks` | do not install the hooks in `~/.claude/settings.json` |
| `--yes` | the prerequisites screen is passed, the features take what they start from, and the summary is confirmed |

```sh
signalbox setup --preset recommended --yes
```

### `signalbox deinit [folder]`

Unlinks the project of a folder (its main checkout or any worktree):

- removes the project from the configuration;
- removes the merge worktree signalbox made for it, unless a lane runs in it at that moment;
  a merge worktree you placed with `merge_worktree` is left, with the command that removes
  it;
- with `--shared-hooks`, removes the hooks `init --shared-hooks` wrote in the repository's
  `.claude/settings.json` (commit that file); without it, says that they stay;
- leaves as they are, and says so: its logs, the lane's lock and state and each worktree's
  `signalbox-changes.json` in the repository's git folder, and the Claude Code hooks, which
  do nothing in a repository no project links.

A folder that no project links is refused with exit code 1, and the linked projects are
listed. A project whose folder is gone is unlinked by naming that folder:
`signalbox deinit ~/Projects/old-shop`.

## Every command

Every command takes `--config <file>` and `-h` or `--help`. `signalbox --version` prints the
version. A command about a repository runs in any folder of it (the main checkout or any
worktree); outside a linked repository it says how to link one:

```text
signalbox: ~/notes is in no linked project. Link its repository with: signalbox init
```

| Command | Flags | What it does |
| --- | --- | --- |
| `setup` | `--preset <name>` or `--feature <feature>` (repeated), `--no-hooks`, `--install-missing`, `--yes` | Set up once: check the prerequisites (and install what is missing with Homebrew, when chosen), choose the defaults, install the Claude Code hooks. |
| `init [folder]` | `--name <name>`, `--gate <command>`, `--base <branch>`, `--shared-hooks`, `--preset <name>` or `--feature <feature>` (repeated), `--yes` | Link a repository's folder, or replace the settings of one linked already. |
| `deinit [folder]` | | Unlink a repository's folder. |
| `status` | `--json`, `--no-github`, `--group` | Show the board. `--no-github` leaves out the open pull requests (no `gh`); `--group` shows the board of every project of this project's group, in order. |
| `brief` | | Print what the session-start hook tells a session. |
| `changes` | | Print what changed in the other worktrees since the last run in this worktree. |
| `sweep` | `--dry-run`, `--json`, `--background` | Remove merged, clean worktrees and merged local branches. `--dry-run` says what it would do and changes nothing; `--background` starts it detached, prints nothing and exits 0, passing the other flags on. |
| `merge <pr> [<project>#<pr> ...]` | `--dry-run`, `--allow-gate-change` | Merge a pull request through the lane; with `<project>#<pr>`, several pull requests of one group, in the group's order. `--dry-run` merges and runs the gate in the merge worktree, then stops; `--allow-gate-change` runs a pull request that changes the gate's own files. |
| `metrics` | `--since <YYYY-MM-DD>`, `--until <YYYY-MM-DD>`, `--json`, `--no-github` | Print flow metrics. `--since`: the first local day (default: 14 days ago); `--until`: the local day the window ends at, excluded (default: now). |
| `doctor` | | Check what signalbox and each project need on this Mac. |
| `hook <event>` | `--shared` | What Claude Code's hooks run: `session-start`, `user-prompt-submit` or `pre-tool-use`, with the hook's JSON on standard input. |
| `uninstall` | `--purge` | Remove the Claude Code hooks; with `--purge`, also the configuration, the logs, the merge worktrees and the sessions' records. |

You do not need to type `hook`: Claude Code runs it.

| Exit code | Meaning |
| --- | --- |
| 0 | It did what was asked. `merge --dry-run`: the pull request was checked and nothing was merged. |
| 1 | It refused, or something needs attention: the folder is in no linked project or the feature is off, the configuration has problems, the base is not on `origin`, a check of `doctor` failed, the lane did not merge, or you quit a screen. |
| 2 | The command line is wrong, including a screen no flag answered when nothing can be typed. For `hook pre-tool-use`: the merge guard refused the command. |
| 130, 143 | `merge` was stopped (Ctrl-C, or `SIGTERM`). |

`brief`, `changes` and `hook` exit with 0 whatever happens (only the guard's refusal exits
with 2): a hook must never block a session by failing. Outside a linked repository, or with
the feature off, they print nothing.

`status --json`, `sweep --json` and `metrics --json` print one JSON object with the same
facts as the Markdown output.

## Uninstall

1. Unlink each repository, if you want to do it project by project:

   ```sh
   cd ~/Projects/shop
   signalbox deinit
   ```

2. Remove the hooks from `~/.claude/settings.json`:

   ```sh
   signalbox uninstall
   ```

   The configuration, the logs and the merge worktrees are kept, and it says where.
   `signalbox uninstall --purge` removes them too: the merge worktrees signalbox made, the
   logs folder, the caches folder, the sessions' records and the configuration file. It refuses, and changes
   nothing, while a merge lane runs in a linked repository.

3. Remove the command and the tap. Do this after `signalbox uninstall`: the hooks run the
   installed command.

   ```sh
   brew uninstall signalbox
   brew untap alejandrovdev/signalbox
   ```

Left in place: the lane's lock and state and each worktree's `signalbox-changes.json` in
each repository's git folder, the shared hooks a repository's `.claude/settings.json` holds
(`signalbox deinit --shared-hooks` removes them; commit that file), and a merge worktree placed with
`merge_worktree` (`uninstall --purge` prints the command that removes it).

## Compatibility

From 1.0.0, signalbox follows [Semantic Versioning](https://semver.org): a minor or patch
release keeps working with whatever you built on the following, and only a major release
changes them, with the [changelog](../CHANGELOG.md) saying how to move:

- the commands, their options and what they do, and the exit codes: 0 when a command did
  what was asked, 1 when it refused or something needs attention, 2 when the command line
  is wrong;
- the configuration file: where it is, its keys and what they mean (a minor release may add
  an optional key);
- the lane log's fields and statuses, the JSON of `status`, `sweep` and `metrics` with
  `--json` (a minor release may add a key);
- the files and folders signalbox writes, and the `SIGNALBOX_*` environment variables, as
  [Configuring signalbox](configuration.md) names them.

Not part of it: `signalbox hook`, which Claude Code's hooks call; the wording and layout of
what the commands print without `--json` (read it, do not parse it); and the look of the
setup screens.
