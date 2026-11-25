"""The session brief: what a session is told about the other worktrees. Git only, no fetch.

    brief(repository, host, out)     at session start: this worktree's line, the other
                                     worktrees that change files, and how to merge; at most
                                     2,000 characters
    changes(repository, host, out)   before each prompt: only what is new since the previous
                                     run in this worktree (a new conflict with the base, a
                                     file newly shared with another branch); the first run
                                     records the state and says nothing

Whoever calls these as a hook prints nothing and exits 0 when they fail.
"""

import json
import os

from .. import names
from ..atoms import worktrees
from .board import feature_documents

BRIEF_LIMIT = 2000
BRIEF_FILES = 5
CHANGES_LINES = 10


def same_path(one, other):
    return os.path.realpath(one) == os.path.realpath(other)


def this_and_others(repository, conflict):
    """This worktree's facts (also when detached) and those of every other worktree on a branch."""
    git, top = repository.git, repository.top
    others = [
        one
        for one in worktrees.all_facts(git, top, repository.tip, conflict=False)
        if not same_path(one.path, top)
    ]
    own = next((tree for tree in worktrees.on_branches(git, top) if same_path(tree.path, top)), None)
    if own is not None:
        mine = worktrees.facts(git, str(top), own.branch, repository.tip, conflict=conflict)
    else:
        mine = worktrees.facts(git, str(top), None, repository.tip, ref="HEAD", conflict=conflict)
    return mine, others


def named(items, limit):
    if len(items) > limit:
        return "%s +%d more" % (", ".join(items[:limit]), len(items) - limit)
    return ", ".join(items)


def merge_line(project):
    if not project.has("merge-lane"):
        return None
    return "Merge with %s. While you work run the focused checks; the lane runs the gate (%s)." % (
        names.command("merge", "<number>"),
        project.gate,
    )


def brief(repository, host, out):
    project = repository.project
    mine, others = this_and_others(repository, conflict=True)
    base = repository.remote_base
    conflict = "conflicts with %s" % base if mine.conflicts else "no conflict with %s" % base
    own = "This worktree: %s, %d behind %s, %s." % (mine.branch or "detached HEAD", mine.behind, base, conflict)

    changing = []
    for other in others:
        if other.files:
            changing.append((other, sorted(other.files & mine.files)))
    changing.sort(key=lambda pair: not pair[1])  # those that share files first, each in list order
    entries = []
    for other, shares in changing:
        parts = ["- %s" % other.branch]
        documents = feature_documents(other.files, project.feature_docs)
        if documents:
            parts.append("features: %s" % ", ".join(documents))
        if shares:
            parts.append("shares: %s" % named(shares, BRIEF_FILES))
        entries.append("; ".join(parts))
    footer = [line for line in (merge_line(project),) if line]

    def compose(shown):
        body = entries[:shown]
        if shown < len(entries):
            body.append("- +%d more worktrees" % (len(entries) - shown))
        header = ["Other worktrees that change files:"] if entries else []
        return "\n".join([own] + header + body + footer)

    shown = len(entries)
    text = compose(shown)
    while len(text) + 1 > BRIEF_LIMIT and shown > 0:
        shown -= 1
        text = compose(shown)
    out(text[: BRIEF_LIMIT - 1])


def read_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None  # no state yet, or one that cannot be read: this run records it
    if isinstance(state, dict) and state.get("version") == 1 and isinstance(state.get("shared"), dict):
        return state
    return None


def changes(repository, host, out):
    mine, others = this_and_others(repository, conflict=True)
    shared = {}
    for file in sorted(mine.files):
        branches = [other.branch for other in others if file in other.files]
        if branches:
            shared[file] = branches
    state = {"version": 1, "conflictsWithBase": mine.conflicts, "shared": shared}
    path = repository.where.git_dir / names.CHANGES_STATE
    previous = read_state(path)
    if previous != state:
        draft = path.with_name(path.name + ".writing")
        draft.write_text(json.dumps(state, indent=2) + "\n", encoding="utf8")
        os.replace(draft, path)
    if previous is None:
        return
    found = []
    if state["conflictsWithBase"] and not previous.get("conflictsWithBase"):
        found.append("This branch (%s) now conflicts with %s." % (mine.branch or "detached HEAD", repository.remote_base))
    for file, branches in shared.items():
        before = previous["shared"].get(file) or []
        added = [branch for branch in branches if branch not in before]
        if added:
            found.append("%s is now also changed by %s." % (file, ", ".join(added)))
    if not found:
        return
    shown = found[:CHANGES_LINES]
    if len(found) > CHANGES_LINES:
        shown.append("+%d more" % (len(found) - CHANGES_LINES))
    out("\n".join(shown))
