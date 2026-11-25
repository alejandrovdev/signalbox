"""A linked project's repository as one command sees it: the folder it runs in, the base branch
and the commit `origin/<base>` points to, as last fetched. Nothing here fetches.

    open(project, host, folder)   the Repository, or Refusal when the folder is in no
                                  repository or the base is not on origin
"""

from dataclasses import dataclass
from pathlib import Path

from .atoms import worktrees

REMOTE = "origin"
FALLBACK_BASE = "main"


class Refusal(Exception):
    """What stops a command before it starts: exit 1, with this message."""


@dataclass(frozen=True)
class Repository:
    project: object
    git: object
    where: worktrees.Where
    base: str  # the base branch, as named on origin
    tip: str  # the commit origin/<base> points to; the local base's in a repository with no remote
    local: bool = False  # a repository with no remote at all: its own base branch is the base

    @property
    def remote_base(self):
        return self.base if self.local else "%s/%s" % (REMOTE, self.base)

    @property
    def top(self):
        return self.where.top

    @property
    def common_dir(self):
        return self.where.common_dir


def base_branch(project, git, top):
    """The project's base branch: configured, else the one origin's HEAD names, else main."""
    return project.base or worktrees.default_branch(git, top, REMOTE) or FALLBACK_BASE


def open(project, host, folder):
    git = host.git
    where = worktrees.locate(git, folder)
    if where is None:
        raise Refusal("not a git repository: %s" % Path(folder))
    base = base_branch(project, git, where.top)
    tip = worktrees.commit_of(git, where.top, "refs/remotes/%s/%s" % (REMOTE, base))
    if tip is None and not git.out(where.top, "remote").split():
        # A repository with no remote: what is merged is what its own base branch holds.
        tip = worktrees.commit_of(git, where.top, "refs/heads/%s" % base)
        if tip is not None:
            return Repository(project, git, where, base, tip, local=True)
    if tip is None:
        raise Refusal("no such ref: %s/%s; fetch it with: git fetch %s %s" % (REMOTE, base, REMOTE, base))
    return Repository(project, git, where, base, tip)
