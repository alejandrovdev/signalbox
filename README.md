# signalbox

Lanes for parallel Claude Code sessions that work in git worktrees of one repository.

A railway signal box sets which train enters which track, keeps two trains off one track,
and lets one through at a time. signalbox does the same for the sessions of a repository:

- **Board**: what every worktree changes, how far each is from the base branch, whether it
  conflicts with it, which files two worktrees change at once, whether the merge lane is busy.
- **Session brief**: at every session start, the board's summary reaches the session; before
  every prompt, what changed in the other worktrees since the last one.
- **Merge lane**: one pull request at a time, checked by the repository's own gate on the
  result of merging it with the current base, then merged only if its head did not move.
- **Merge guard**: sessions that try a direct `gh pr merge` are told to use the lane.
- **Sweep**: clean worktrees whose branch was merged, and merged local branches, go away.
- **Flow metrics**: lead time, lane waits and failures, merges outside the lane.

It installs once with Homebrew, is set up from a terminal interface, and links any number of
repositories, each with only the features it needs.

Status: in development; not released yet.
