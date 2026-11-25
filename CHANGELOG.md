# Changelog

Each release of signalbox, newest first. Versions follow `MAJOR.MINOR.PATCH`: from 1.0.0 on,
a change that breaks a command, a flag, the configuration file or the lane log's fields takes
a new major version.

## [1.2.0] - 2026-11-25

### Added

- A repository with no remote at all uses its own base branch: the board, the session brief,
  the sweep and the metrics work in it. The merge lane still needs `origin` on GitHub.

## [1.1.0] - 2026-11-25

### Added

- `setup` draws each step as a whole screen in a terminal of 112 by 32 or more: a status bar
  with the step and a progress bar, an animated railway signal, the name in large pixels,
  the steps, the answers so far, the step's panel and a panel about the option under the
  cursor (what it does, what it needs, what needs it, the tools it uses). From 80 by 24 a
  roomier layout; smaller terminals keep the plain questions.
- `setup` offers to install what is missing (git, gh, Claude Code) with Homebrew, after a
  warning, and installs only after the summary's yes; `--install-missing` installs without
  asking.
- The README opens with a banner and the setup screen, says what problem it solves, and
  moves how it works and troubleshooting to `docs/`, with a picture of the merge lane.

### Changed

- A `SIGNALBOX_GIT`, `SIGNALBOX_GH` or `SIGNALBOX_CLAUDE` that names no program counts as
  missing.

## [1.0.0] - 2026-11-25

### Added

- Groups of repositories that depend on each other: `[groups.<name>]` lists linked projects
  in dependency order. The session brief also names the other repositories' worktrees that
  change files; `signalbox status --group` prints every board of the group; `signalbox merge
  <pr> [<project>#<pr> ...]` merges several pull requests of the group in its order, reading
  them all first and stopping at the first that does not merge. Lane records gain `group`.
- The sweep keeps the worktree a live Claude Code session stands in: the hooks record where
  each session stands and its `claude` process, and a sweep removes the records of sessions
  that ended. `SIGNALBOX_SESSIONS_DIR` moves those records.
- The sweep keeps a worktree with files marked skip-worktree or assume-unchanged, whose
  edits `git status` does not show.
- `signalbox deinit --shared-hooks` removes the hooks `init --shared-hooks` wrote.
- The flow metrics count squash and rebase merges, following `merge_method`.

### Changed

- `signalbox uninstall --purge` also removes the sessions' records.
- `signalbox merge` takes more than one pull request; a second bare number is a usage error
  that names the `<project>#<pr>` form.

## [0.1.0] - 2026-11-25

### Added

- The board (`signalbox status`), the session brief (`brief`, `changes`), the merge lane
  (`merge`), the merge guard, the sweep (`sweep`) and flow metrics (`metrics`), each a
  feature a project turns on or off.
- `setup`, `init`, `deinit`, `doctor` and `uninstall`, with a terminal interface and plain
  questions when the terminal cannot draw.
- The user-level Claude Code hooks, silent outside a linked repository, and `init
  --shared-hooks` for a team.
