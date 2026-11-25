"""What git says about a repository's worktrees: where a folder is, every worktree, and how
one worktree stands against the base.

    locate(git, folder)                   its top, git directory and common directory, or None
    listed(git, top)                      every worktree, the main checkout first
    facts(git, worktree, base, ...)       behind, ahead, changed files, conflict, last commit
    all_facts(git, top, base, ...)        the facts of every worktree on a branch, read at once
    shared_files(facts)                   each file two or more of them change
    conflicts(git, folder, base, ref)     whether merging ref into base conflicts (no checkout)
    default_branch(git, top, remote)      the branch the remote's HEAD names, or None
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .git import GitError, paths


@dataclass(frozen=True)
class Where:
    top: Path  # the worktree's top folder
    git_dir: Path  # its own git directory
    common_dir: Path  # the repository's, shared by every worktree


@dataclass(frozen=True)
class Worktree:
    path: str
    head: Optional[str]
    branch: Optional[str]  # None when detached
    locked: Optional[str]  # the lock's reason ("" when it gives none), or None
    prunable: bool
    main: bool  # the main checkout


@dataclass(frozen=True)
class Facts:
    path: str
    branch: Optional[str]
    behind: int
    ahead: int
    files: frozenset  # changed since the merge base, the working tree and untracked files included
    conflicts: Optional[bool] = None  # None: not asked
    last_commit: Optional[int] = None  # its committer time in seconds; None: not asked


def locate(git, folder):
    """Where a folder is in its repository; None when it is in none."""
    result = git.runner(
        [git.path, "-C", str(folder), "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir"]
    )
    if result.returncode != 0:
        return None
    found = result.stdout.splitlines()
    if len(found) != 3:
        return None
    return Where(*(Path(line) for line in found))


def listed(git, top):
    """Every worktree, as `git worktree list --porcelain` lists them, the main checkout first."""
    worktrees = []
    for block in git.out(top, "worktree", "list", "--porcelain").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line:
                name, _, value = line.partition(" ")
                fields[name] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch")
        worktrees.append(
            Worktree(
                fields["worktree"],
                fields.get("HEAD"),
                branch[len("refs/heads/") :] if branch and branch.startswith("refs/heads/") else branch,
                fields.get("locked"),
                "prunable" in fields,
                not worktrees,
            )
        )
    return worktrees


def on_branches(git, top):
    """The worktrees on a branch whose folder exists."""
    return [tree for tree in listed(git, top) if tree.branch is not None and not tree.prunable]


def conflicts(git, folder, base, ref):
    """Whether merging `ref` into `base` conflicts, from `git merge-tree --write-tree`."""
    return git.run(folder, "merge-tree", "--write-tree", base, ref, accepted=(0, 1)).returncode == 1


def facts(git, path, branch, base, ref=None, conflict=True, last_commit=False):
    """One worktree against `base` (a commit). `ref` defaults to the worktree's branch; a
    detached worktree passes HEAD."""
    ref = ref or "refs/heads/%s" % branch
    behind, ahead = (int(count) for count in git.out(path, "rev-list", "--left-right", "--count", "%s...%s" % (base, ref)).split())
    merge_base = git.line(path, "merge-base", base, ref)
    tracked = paths(git.out(path, "diff", "--name-only", "-z", merge_base))
    untracked = paths(git.out(path, "ls-files", "--others", "--exclude-standard", "-z"))
    return Facts(
        path,
        branch,
        behind,
        ahead,
        frozenset(tracked) | frozenset(untracked),
        conflicts(git, path, base, ref) if conflict else None,
        int(git.line(path, "log", "-1", "--format=%ct", ref)) if last_commit else None,
    )


def all_facts(git, top, base, conflict=True, last_commit=False):
    """The facts of every worktree on a branch, in list order, read at once."""
    trees = on_branches(git, top)
    if not trees:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(trees))) as pool:
        found = [
            pool.submit(facts, git, tree.path, tree.branch, base, None, conflict, last_commit) for tree in trees
        ]
        return [future.result() for future in found]


def shared_files(found):
    """Each file changed in two or more of the worktrees, with their branches in list order."""
    branches = {}
    for one in found:
        for file in one.files:
            branches.setdefault(file, []).append(one.branch)
    return {file: listed for file, listed in branches.items() if len(listed) >= 2}


def default_branch(git, top, remote):
    """The branch the remote's HEAD names (`refs/remotes/<remote>/HEAD`), or None."""
    try:
        name = git.line(top, "symbolic-ref", "--quiet", "--short", "refs/remotes/%s/HEAD" % remote)
    except GitError:
        return None
    prefix = remote + "/"
    return name[len(prefix) :] if name.startswith(prefix) else None


def commit_of(git, folder, ref):
    """The commit a ref names, or None."""
    result = git.runner([git.path, "-C", str(folder), "rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    return result.stdout.strip() if result.returncode == 0 else None
