"""The merge lane: merges one pull request into the base, one run at a time per repository,
only after the project's gate passed on the result of merging it with the current base, the
tree GitHub then writes to the base.

    merge(repository, host, out, err, number, dry_run, allow_gate_change, caller)   exit code

Steps, in order. The whole run holds the lane's lock in the git common directory (and any
`legacy_locks`), so a second run says it waits and takes its turn; the gate a killed run
left running is stopped first. (1) Read the pull request; refuse it unless it is open, not a
draft, based on the base and from this repository. (2) Fetch the base and the head; the head
is the commit read in (1) from here on. A pull request that changes a file the gate command
names is refused unless `--allow-gate-change`: it would check itself. (3) In the merge
worktree (detached, outside every checkout; made again when gone or left with a stale
`index.lock`), check out the base and merge the head in a commit that is never pushed; a
conflict refuses. (4) Run the gate there, in its own process group, which stops with the
lane; its output reaches the caller and, when it fails, is kept whole in the log folder. (5)
When the base moved meanwhile, start again from (3), 3 attempts in all. (6) Post a commit
status on the head. (7) `gh pr merge --match-head-commit <head>`, so a push after the check
is refused. (8) The base's tree must be the checked one. (9) Say whether GitHub deletes
merged branches, and sweep.

The caller's working tree, index and branch are never changed. Each run that got the lock is
one JSON line in the lane log, best effort: the log never changes the output or the exit.
Exit codes: 0 merged (with dry_run: checked, nothing merged), 1 refused, 130 or 143 stopped.
"""

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .. import names
from .. import repository as repositories
from ..atoms import git as git_atom
from ..atoms import lock, logs
from . import sweep as sweep_feature

MAX_ATTEMPTS = 3
VIEW_FIELDS = "number,state,isDraft,baseRefName,headRefName,headRefOid,isCrossRepository"
STOP_SECONDS = 10  # what a stopped gate gets before SIGKILL
NO_HOOKS = ("-c", "core.hooksPath=/dev/null")
IDENTITY = ("-c", "user.name=merge lane", "-c", "user.email=merge-lane@localhost", "-c", "commit.gpgsign=false")


class Stop(Exception):
    """Ends a run with an exit code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Interrupted(BaseException):
    """A signal that stops the lane: 130 for SIGINT, 143 for SIGTERM."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def utc(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


class Run:
    def __init__(self, repository, host, out, err, number, dry_run, allow_gate_change, caller):
        self.repository = repository
        self.project = repository.project
        self.host = host
        self.out = out
        self.err = err
        self.number = number
        self.dry_run = dry_run
        self.allow_gate_change = allow_gate_change
        self.caller = caller
        self.top = repository.top
        self.base = repository.base
        self.remote_base = "refs/remotes/%s/%s" % (repositories.REMOTE, self.base)
        self.started = host.clock()
        configured = self.project.merge_worktree
        self.worktree = Path(os.path.expanduser(configured)) if configured else names.default_merge_worktree(self.project.name, host.environ)
        self.state_file = Path(repository.common_dir) / names.LANE_STATE
        self.record = {
            "version": 1,
            "startedAt": utc(self.started),
            "pullRequest": number,
            "head": None,
            "status": None,
            "waitSeconds": 0,
            "gateSeconds": None,
            "durationSeconds": 0,
            "attempts": 0,
            "baseTip": None,
            "tree": None,
            "mergeCommit": None,
            "failedStep": None,
            "gateLog": None,
        }
        self.logged = False
        self.gate = None  # the running gate's process

    # git, gh and messages

    def git(self, *args, accepted=(0,)):
        return self.repository.git.run(self.top, *NO_HOOKS, *args, accepted=accepted)

    def lane_git(self, *args, accepted=(0,)):
        return self.repository.git.run(self.worktree, *NO_HOOKS, *IDENTITY, *args, accepted=accepted)

    def gh(self, *args):
        return self.host.run([self.gh_path, *args], cwd=self.top)

    def refuse(self, status, message):
        self.record["status"] = status
        self.err("lane: #%d %s" % (self.number, message))
        raise Stop(1)

    def post_status(self, state, description):
        """The receipt on the head; one that cannot be posted is a warning. A dry run posts nothing."""
        if self.dry_run:
            return
        head = self.record["head"]
        done = self.gh(
            "api",
            "--method",
            "POST",
            "repos/{owner}/{repo}/statuses/" + head,
            "-f",
            "state=" + state,
            "-f",
            "context=" + names.STATUS_CONTEXT,
            "-f",
            "description=" + description,
        )
        if done.returncode != 0:
            self.err("lane: warning: could not post the %s status on %s." % (names.STATUS_CONTEXT, head[:12]))

    def fetch_base(self, *more):
        refspec = "+refs/heads/%s:%s" % (self.base, self.remote_base)
        done = self.host.run(
            [self.repository.git.path, "-C", str(self.top), *NO_HOOKS, "fetch", "--quiet", "--no-write-fetch-head", "origin", refspec, *more]
        )
        if done.returncode != 0:
            what = " and ".join([self.base] + [ref.split("/", 2)[-1] for ref in more])
            if done.stderr.strip():
                self.err(done.stderr.rstrip("\n"))
            self.record["status"] = "error"
            self.err("lane: could not fetch origin %s." % what)
            raise Stop(1)
        return self.git("rev-parse", self.remote_base).stdout.strip()

    # the lock and what a killed run left

    def take_locks(self):
        paths = [Path(self.repository.common_dir) / names.LANE_LOCK]
        paths += [Path(self.repository.common_dir) / legacy for legacy in self.project.legacy_locks]
        held, said = [], False
        try:
            for path in paths:
                taken = lock.take(path, wait=False)
                if taken is None:
                    if not said:
                        self.out("lane: another merge is running; #%d waits for its turn." % self.number)
                        said = True
                    taken = lock.take(path, wait=True)
                held.append(taken)
        except OSError as error:
            for taken in held:
                taken.close()
            self.err("lane: cannot take the lane's lock: %s" % error)
            raise Stop(1)
        return held

    def read_state(self):
        try:
            state = json.loads(self.state_file.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def write_state(self, state):
        try:
            if state:
                draft = self.state_file.with_name(self.state_file.name + ".writing")
                draft.write_text(json.dumps(state) + "\n", encoding="utf8")
                os.replace(draft, self.state_file)
            else:
                self.state_file.unlink(missing_ok=True)
        except OSError:
            pass

    def stop_leftover_gate(self):
        """Stop the gate a run killed with SIGKILL left running: the lock is ours, so nothing
        else may run in the merge worktree."""
        gate = self.read_state().get("gate")
        if isinstance(gate, dict) and isinstance(gate.get("pid"), int) and isinstance(gate.get("started"), list):
            pid = gate["pid"]
            dates = self.host.start_dates(pid)
            if dates and set(dates) & set(gate["started"]):
                stop_group(pid)
                self.out("lane: stopped the gate a stopped run left running (pid %d)." % pid)
        self.write_state({})

    # the merge worktree and the gate

    def prepare_worktree(self, tip):
        path = self.worktree
        if (path / ".git").exists():
            found = self.host.run([self.repository.git.path, "-C", str(path), "rev-parse", "--absolute-git-dir"])
            stale = Path(found.stdout.strip()) / "index.lock" if found.returncode == 0 else None
            if stale is not None and stale.exists():
                self.out("lane: the merge worktree held a stale index.lock (a killed git left it); made it again.")
                stale.unlink(missing_ok=True)
                removed = self.host.run([self.repository.git.path, "-C", str(self.top), "worktree", "remove", "--force", "--force", str(path)])
                if removed.returncode != 0:
                    shutil.rmtree(path, ignore_errors=True)
        if not (path / ".git").exists():
            if path.exists() and any(path.iterdir()):
                self.record["status"] = "error"
                self.err("lane: %s is a folder that is not the merge worktree: move it away, or set merge_worktree." % path)
                raise Stop(1)
            self.git("worktree", "prune")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.git("worktree", "add", "--quiet", "--detach", str(path), tip)
        self.lane_git("reset", "--quiet", "--hard")
        self.lane_git("checkout", "--quiet", "--detach", tip)
        self.lane_git("clean", "-d", "--force", "--quiet")

    def gate_files(self, base_tip, head):
        """The files the gate command names that the pull request changes."""
        try:
            words = shlex.split(self.project.gate or "")
        except ValueError:
            return []
        named = {word[2:] if word.startswith("./") else word for word in words if word and not word.startswith(("-", "/"))}
        changed = git_atom.paths(self.git("diff", "--name-only", "-z", "%s...%s" % (base_tip, head)).stdout)
        return sorted(named & set(changed))

    def run_gate(self, attempt):
        """Run the gate in the merge worktree; (exit code, its whole output)."""
        environ = dict(self.host.environ, **self.project.gate_environment)
        captured = []
        self.gate = subprocess.Popen(
            ["/bin/sh", "-c", self.project.gate],
            cwd=self.worktree,
            env=environ,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own process group, which stops with the lane
        )
        self.write_state({"gate": {"pid": self.gate.pid, "started": self.host.start_dates(self.gate.pid) or []}})
        try:
            for raw in self.gate.stdout:
                line = raw.decode("utf8", "replace")
                captured.append(line)
                self.err(line.rstrip("\n"))
            code = self.gate.wait()
        finally:
            if self.gate.poll() is None:
                stop_group(self.gate.pid)
            self.gate.stdout.close()
            self.gate = None
            self.write_state({})
        return code, "".join(captured)

    def keep_output(self, attempt, text):
        """Keep a failed gate's whole output in the log folder; its path, or None."""
        folder = names.project_logs(self.project.name, self.host.environ) / "gates"
        name = "%s-pr%d-attempt%d.log" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.started)), self.number, attempt)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(text, encoding="utf8")
        except OSError:
            return None
        return str(folder / name)

    def failed_step(self, text):
        pattern = self.project.gate_failed_step
        if not pattern:
            return None
        found = [match.group("step") for match in re.finditer(pattern, text, re.M)]
        return found[-1] if found else None

    # the run

    def view(self):
        done = self.gh("pr", "view", str(self.number), "--json", VIEW_FIELDS)
        try:
            pull = json.loads(done.stdout) if done.returncode == 0 else None
        except ValueError:
            pull = None
        if not isinstance(pull, dict):
            if done.stderr.strip():
                self.err(done.stderr.rstrip("\n"))
            self.refuse("refused", "refused: gh pr view could not read it.")
        self.record["head"] = pull.get("headRefOid")
        state = str(pull.get("state", "")).lower()
        if state != "open":
            self.refuse("refused", "refused: it is %s, not open." % state)
        if pull.get("isDraft") is not False:
            self.refuse("refused", "refused: it is a draft.")
        if pull.get("baseRefName") != self.base:
            self.refuse("refused", "refused: it is based on %s, not %s." % (pull.get("baseRefName"), self.base))
        if pull.get("isCrossRepository") is not False:
            self.refuse("refused", "refused: its head is in another repository.")
        return pull["headRefName"], pull["headRefOid"]

    def attempts(self, head):
        gate_seconds = 0
        while True:
            self.record["attempts"] += 1
            attempt = self.record["attempts"]
            base_tip = self.git("rev-parse", self.remote_base).stdout.strip()
            self.record.update(baseTip=base_tip, tree=None)
            self.prepare_worktree(base_tip)
            merged = self.lane_git(
                "merge", "--quiet", "--no-ff", "--no-edit", "-m", "Merge pull request #%d (merge lane check, never pushed)" % self.number, head, accepted=(0, 1, 2, 128)
            )
            if merged.returncode != 0:
                self.lane_git("merge", "--abort", accepted=(0, 128))
                self.post_status("failure", "conflicts with %s" % self.base)
                self.refuse("conflict", "not merged: it conflicts with %s; update the branch with %s and run again." % (self.base, self.base))
            tree = self.lane_git("rev-parse", "HEAD^{tree}").stdout.strip()
            self.record["tree"] = tree
            began = self.host.clock()
            try:
                code, text = self.run_gate(attempt)
            finally:
                gate_seconds += int(self.host.clock() - began)
                self.record["gateSeconds"] = gate_seconds
            if code in (130, 143):
                raise Interrupted(code)
            if code != 0:
                step = self.failed_step(text)
                self.record["failedStep"] = step
                self.record["gateLog"] = self.keep_output(attempt, text)
                if self.record["gateLog"]:
                    self.err("lane: the gate's whole output is in %s" % self.record["gateLog"])
                if step:
                    self.post_status("failure", 'failed at "%s"' % step)
                    self.refuse("gate_failed", 'not merged: the gate failed at "%s".' % step)
                self.post_status("failure", "the gate failed (exit %d)" % code)
                self.refuse("gate_failed", "not merged: the gate failed (exit %d)." % code)
            if self.fetch_base() == base_tip:
                return base_tip, tree
            if attempt >= MAX_ATTEMPTS:
                self.post_status("failure", "%s moved during each of %d attempts" % (self.base, MAX_ATTEMPTS))
                self.refuse("base_moved", "not merged: %s moved during each of %d attempts; run again." % (self.base, MAX_ATTEMPTS))
            self.out("lane: %s moved during the gate; attempt %d of %d." % (self.base, attempt + 1, MAX_ATTEMPTS))

    def merge(self):
        head_ref, head = self.view()
        self.fetch_base("refs/heads/" + head_ref)
        if not self.repository.git.ok(self.top, "cat-file", "-e", head + "^{commit}"):
            self.record["status"] = "error"
            self.err("lane: #%d: its head %s is no longer on %s; run again." % (self.number, head[:12], head_ref))
            raise Stop(1)
        changed = self.gate_files(self.git("rev-parse", self.remote_base).stdout.strip(), head)
        if changed and not self.allow_gate_change:
            self.refuse(
                "gate_changed",
                "refused: it changes the gate (%s), which would check itself. Review that change, then run again "
                "with --allow-gate-change." % ", ".join(changed),
            )
        base_tip, tree = self.attempts(head)
        if self.dry_run:
            self.record["status"] = "checked"
            self.out("lane: #%d checked: tree %s on %s %s; nothing merged (--dry-run)." % (self.number, tree[:12], self.base, base_tip[:12]))
            return 0
        self.post_status("success", "checked tree %s on %s %s" % (tree[:12], self.base, base_tip[:12]))
        merged = self.gh("pr", "merge", str(self.number), "--" + self.project.merge_method, "--match-head-commit", head)
        if merged.returncode != 0:
            if merged.stderr.strip():
                self.err(merged.stderr.rstrip("\n"))
            self.refuse("merge_failed", "not merged: gh pr merge refused it.")
        merge_commit = self.fetch_base()
        self.record["mergeCommit"] = merge_commit
        written = self.git("rev-parse", merge_commit + "^{tree}").stdout.strip()
        if written != tree:
            self.refuse(
                "tree_mismatch",
                "merged, but %s %s has tree %s, not the checked tree %s." % (self.base, merge_commit[:12], written[:12], tree[:12]),
            )
        self.record["status"] = "merged"
        self.out("lane: #%d merged: %s %s holds the checked tree %s." % (self.number, self.base, merge_commit[:12], tree[:12]))
        self.log()
        self.clean_up()
        return 0

    def clean_up(self):
        """After the run is recorded: an interrupted cleanup is still a merge."""
        done = self.gh("api", "repos/{owner}/{repo}")
        try:
            setting = json.loads(done.stdout).get("delete_branch_on_merge") if done.returncode == 0 else None
        except (ValueError, AttributeError):
            setting = None
        if setting is None:
            self.err("lane: warning: could not read whether GitHub deletes merged branches.")
        elif setting is not True:
            self.err(
                'lane: warning: GitHub keeps merged branches; turn on "Automatically delete head branches" in the '
                "repository's settings."
            )
        if not self.project.has("sweep"):
            return
        try:
            fresh = repositories.open(self.project, self.host, self.top)
            sweep_feature.sweep(fresh, self.host, self.out, caller=self.caller)
        except Exception as error:  # the merge stands
            self.err("lane: warning: the sweep of merged worktrees and branches failed: %s" % error)

    def log(self):
        if self.logged:
            return
        self.logged = True
        self.record["durationSeconds"] = int(self.host.clock() - self.started)
        logs.append(names.lane_log(self.project.name, self.host.environ), self.record)

    def run(self):
        self.gh_path = self.host.gh()
        if self.gh_path is None:
            self.err("lane: gh is required but not installed: install it with: brew install gh")
            return 1
        held = self.take_locks()
        try:
            self.record["waitSeconds"] = int(self.host.clock() - self.started)
            try:
                self.stop_leftover_gate()
                return self.merge()
            except Stop as stop:
                return stop.code
            except Interrupted as interrupted:
                self.record["status"] = "interrupted"
                return interrupted.code
            except KeyboardInterrupt:
                self.record["status"] = "interrupted"
                return 130
            except git_atom.GitError as error:
                self.record["status"] = "error"
                self.err("lane: %s" % error)
                return 1
            finally:
                self.log()
        finally:
            for taken in held:
                taken.close()


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_group(pgid):
    """Stop a process group: SIGTERM, then SIGKILL when some of it still runs after a while."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + STOP_SECONDS
    while time.time() < deadline:
        if not group_alive(pgid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def merge(repository, host, out, err, number, dry_run=False, allow_gate_change=False, caller=()):
    return Run(repository, host, out, err, number, dry_run, allow_gate_change, caller).run()
