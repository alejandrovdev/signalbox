"""The sweep: removes the worktrees whose branch was merged into the base and deletes the
merged local branches no worktree holds. Nothing it deletes names work the base lacks: a
merged branch is one whose tip `origin/<base>` reaches, or the head of a pull request the
lane merged (by squash or rebase, whose commits the base does not reach).

    sweep(repository, host, out, dry_run, as_json, caller)   say what it removed and kept; 0

A merged worktree stays, with the reason said, when it is the main checkout; when it holds
the caller's directory; when it has uncommitted changes or untracked files; when it is
locked, unless the lock is a Claude Code agent's and that agent is gone (no process with the
lock's pid and start date runs) or finished (its record says so); when it holds ignored
files that are not the project's build output; when it was used since its branch was
merged (its HEAD moved later), or no reflog says; when a process runs in it, or `lsof`
cannot say. A branch is never deleted when it is the base, points at the base's tip (a
branch just cut), or is the first branch of an agent that still runs. A remote-tracking ref
goes with its branch only when the base reaches it too: the sweep never deletes a branch on
origin.
"""

import json
import os
import re
from pathlib import Path

from .. import names
from ..atoms import logs, patterns, worktrees

# The lock Claude Code puts on an agent's worktree: the agent, and its session's pid and start.
AGENT_LOCK = re.compile(r"^claude agent (\S+) \(pid (\d+) start (.+)\)$")
AGENT_BRANCH = "worktree-%s"  # the branch Claude Code creates an agent's worktree on


def real(path):
    return os.path.realpath(str(path))


def inside(path, root):
    path = real(path)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Agents:
    """Whether an agent lock still holds, asked once per agent."""

    def __init__(self, host):
        self.host = host
        self.known = {}

    def finished(self, agent, folder):
        """Whether Claude Code's record of the agent says it finished, in this worktree."""
        root = names.claude_projects(self.host.environ)
        for path in root.glob("*/*/subagents/%s.meta.json" % agent):
            try:
                record = json.loads(path.read_text(encoding="utf8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("finished") is True and record.get("worktreePath"):
                if real(record["worktreePath"]) == real(folder):
                    return True
        return False

    def holds(self, reason, folder):
        """Whether a lock holds: any lock but an agent's does; an agent's until its agent is
        gone or finished. When `ps` cannot be read, it holds."""
        found = AGENT_LOCK.fullmatch(reason or "")
        if found is None:
            return True
        agent, pid, started = found.groups()
        key = (agent, real(folder))
        if key not in self.known:
            self.known[key] = self.alive(agent, int(pid), started, folder)
        return self.known[key]

    def alive(self, agent, pid, started, folder):
        if self.finished(agent, folder):
            return False
        dates = self.host.start_dates(pid)
        if dates is None:
            return True
        # Claude Code writes the date as `ps` prints it in UTC and the C locale; this machine's
        # own `ps` may print it in its zone. The lock is stale only when neither matches.
        return " ".join(started.split()) in dates


class Sweep:
    def __init__(self, repository, host, dry_run, caller):
        self.repository = repository
        self.host = host
        self.git = repository.git
        self.top = repository.top
        self.dry_run = dry_run
        self.callers = [real(folder) for folder in caller]
        self.agents = Agents(host)
        self.read = False
        self.running = None

    def directories(self):
        if not self.read:
            self.running = self.host.directories()
            self.read = True
        return self.running

    def lane_merged(self):
        """The heads of the pull requests the lane merged: head: the record."""
        records, _ = logs.read(names.lane_log(self.repository.project.name, self.host.environ))
        return {record["head"]: record for record in records if record.get("status") == "merged" and isinstance(record.get("head"), str)}

    def merged_branches(self):
        """(branch: tip of every merged local branch, the branches merged through the lane only)."""
        tip = self.repository.tip
        listing = self.git.out(self.top, "for-each-ref", "--format=%(refname:lstrip=2) %(objectname)", "refs/heads")
        branches = dict(line.rsplit(" ", 1) for line in listing.splitlines() if line)
        reached = self.git.out(self.top, "for-each-ref", "--merged=%s" % tip, "--format=%(refname:lstrip=2)", "refs/heads")
        merged = {branch: branches[branch] for branch in reached.split() if branch in branches}
        by_lane = self.lane_merged()
        through_lane = {branch: by_lane[head] for branch, head in branches.items() if branch not in merged and head in by_lane}
        merged.update({branch: branches[branch] for branch in through_lane})
        return merged, through_lane

    def last_head_move(self, folder):
        """When a worktree's HEAD last moved (a commit, a checkout, its creation), in seconds."""
        done = self.host.run([self.git.path, "-C", str(folder), "log", "-g", "-1", "--date=unix", "--format=%gd", "HEAD"])
        found = re.search(r"@\{(\d+)\}$", done.stdout.strip()) if done.returncode == 0 else None
        return int(found.group(1)) if found else None

    def merged_at(self, tip, record=None):
        """When the base received a commit, in seconds: the committer time of the merge that
        brought it in (the oldest commit of the base's first-parent chain that descends from
        it), or of the lane's merge commit."""
        if record is not None:
            commit = record.get("mergeCommit")
            if isinstance(commit, str) and self.git.ok(self.top, "cat-file", "-e", commit + "^{commit}"):
                return int(self.git.line(self.top, "show", "-s", "--format=%ct", commit))
            return None
        found = self.git.out(self.top, "rev-list", "--first-parent", "--ancestry-path", "%s..%s" % (tip, self.repository.tip))
        descendants = found.split()
        if not descendants:
            return None
        return int(self.git.line(self.top, "show", "-s", "--format=%ct", descendants[-1]))

    def reason_to_keep(self, tree, tip, record):
        """Why a worktree on a merged branch stays, or None when the sweep takes it."""
        if tree.main:
            return "the main checkout"
        root = real(tree.path)
        if any(inside(caller, root) for caller in self.callers):
            return "it holds the caller's directory; a sweep run from elsewhere removes it"
        # --untracked-files: a `status.showUntrackedFiles=no` setting must not hide a file.
        status = self.git.out(tree.path, "status", "--porcelain", "--ignored", "--untracked-files=normal").splitlines()
        if any(line and not line.startswith("!! ") for line in status):
            return "uncommitted changes"
        if tree.locked is not None and self.agents.holds(tree.locked, tree.path):
            return "locked (%s)" % tree.locked if tree.locked else "locked"
        output = self.repository.project.build_output
        kept = next((line[3:] for line in status if line.startswith("!! ") and not patterns.any_match(line[3:], output)), None)
        if kept is not None:
            return "ignored files that are not build output (%s)" % kept.rstrip("/")
        # A worktree cut from a commit the base already held, or one somebody checked out or
        # committed in after the merge, is in use: its HEAD moved after the base received it.
        moved = self.last_head_move(tree.path)
        if moved is None:
            return "cannot tell when it was last used (no reflog)"
        received = self.merged_at(tip, record)
        if received is None or moved > received:
            return "used since its branch was merged"
        running = self.directories()
        if running is None:
            return "cannot tell whether a process runs in it (lsof failed)"
        busy = next((pid for pid, folder in running if inside(folder, root)), None)
        if busy is not None:
            return "a process runs in it (pid %d)" % busy
        return None

    def remove(self, tree):
        """Remove a worktree, without --force; why it stays when git refuses, else None."""
        if tree.locked is not None:
            self.git.run(self.top, "worktree", "unlock", tree.path)
        done = self.host.run([self.git.path, "-C", str(self.top), "worktree", "remove", tree.path])
        if done.returncode != 0:
            return "git worktree remove failed: %s" % (done.stderr.strip().splitlines() or [""])[0]
        return None

    def protected_branches(self, trees):
        """The first branch of every agent that still runs: it may switch back to it."""
        found = set()
        for tree in trees:
            agent = AGENT_LOCK.fullmatch(tree.locked or "")
            if agent and self.agents.holds(tree.locked, tree.path):
                found.add(AGENT_BRANCH % agent.group(1))
        return found

    def delete_branch(self, branch, tip, through_lane):
        if not self.git.ok(self.top, "branch", "-D", branch):
            return False
        tracking = "refs/remotes/origin/%s" % branch
        tracked = self.host.run([self.git.path, "-C", str(self.top), "rev-parse", "--verify", "--quiet", tracking])
        if tracked.returncode == 0:
            reached = self.git.ok(self.top, "merge-base", "--is-ancestor", tracking, self.repository.tip)
            if reached or (branch in through_lane and tracked.stdout.strip() == tip):
                self.git.run(self.top, "update-ref", "-d", tracking)
        return True

    def run(self):
        if not self.dry_run:
            self.git.run(self.top, "worktree", "prune")
        merged, through_lane = self.merged_branches()
        # A branch that never left the base (a worktree somebody just created) has its tip on
        # the base's first-parent chain; one merged with a merge commit has it off the chain.
        chain = set(self.git.out(self.top, "rev-list", "--first-parent", self.repository.tip).split())
        trees = worktrees.listed(self.git, self.top)
        outcomes, held = [], set()
        for tree in trees:
            if tree.branch is None or tree.prunable:
                continue
            held.add(tree.branch)
            tip = merged.get(tree.branch)
            if tip is None or (tip in chain and tree.branch not in through_lane):
                continue
            reason = self.reason_to_keep(tree, tip, through_lane.get(tree.branch))
            if reason is None and not self.dry_run:
                reason = self.remove(tree)
            if reason is None:
                held.discard(tree.branch)
            outcomes.append({"path": tree.path, "branch": tree.branch, "reason": reason})
        protected = self.protected_branches(trees)
        deleted = []
        for branch in sorted(merged):
            tip = merged[branch]
            if branch in held or branch in protected or branch == self.repository.base or tip == self.repository.tip:
                continue
            if not self.dry_run and not self.delete_branch(branch, tip, through_lane):
                continue
            deleted.append(branch)
        return outcomes, deleted


def sweep(repository, host, out, dry_run=False, as_json=False, caller=()):
    outcomes, deleted = Sweep(repository, host, dry_run, caller).run()
    if as_json:
        removed = [{"path": item["path"], "branch": item["branch"]} for item in outcomes if item["reason"] is None]
        kept = [item for item in outcomes if item["reason"] is not None]
        out(json.dumps({"dryRun": dry_run, "removedWorktrees": removed, "deletedBranches": deleted, "kept": kept}, indent=2))
        return 0
    base = repository.remote_base
    lines = []
    for item in outcomes:
        if item["reason"] is None:
            did = "would remove" if dry_run else "removed"
            lines.append("sweep: %s worktree %s (%s, merged into %s)" % (did, item["path"], item["branch"], base))
        else:
            lines.append("sweep: kept worktree %s (%s): %s" % (item["path"], item["branch"], item["reason"]))
    for branch in deleted:
        lines.append("sweep: %s branch %s (merged into %s)" % ("would delete" if dry_run else "deleted", branch, base))
    out("\n".join(lines) if lines else "sweep: nothing to sweep.")
    return 0


def caller_folders(environ=None):
    """The directories a sweep must never remove: this process's and the shell's (`PWD`)."""
    environ = os.environ if environ is None else environ
    found = [Path.cwd()]
    if environ.get("PWD"):
        found.append(Path(environ["PWD"]))
    return found
