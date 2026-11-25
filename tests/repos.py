"""Temporary repositories with real git, and stand-ins for what is not git.

Each test gets a scratch folder with a home of its own (so no global git configuration of
the person running the tests applies), a `bin` folder first on PATH for stub programs, and
helpers that build repositories, commits and worktrees. Nothing here touches the network.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from signalbox import config
from signalbox.host import Host

GIT = shutil.which("git")
IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false")


def lines(changes=None):
    """A file of six numbered lines, some replaced, so that changes to other lines merge."""
    changes = changes or {}
    return "".join("%s\n" % changes.get(number, "line %d" % number) for number in range(1, 7))


def hours_ago(hours):
    """A date `hours` before now, as git reads it."""
    return "@%d +0000" % int(time.time() - hours * 3600)


def project(path, features=None, **values):
    return config.Project("shop", str(path), tuple(config.FEATURES) if features is None else features, **values)


class StandInHost(Host):
    """The real host for git, with `ps` and `lsof` answered by the test."""

    def __init__(self, environ):
        super().__init__(environ)
        self.started = {}  # pid: the start dates `ps` prints for it (UTC first, then local); None: `ps` fails
        self.cwd_list = []  # (pid, directory): what `lsof` reads; None when it cannot be read
        self.claude = None  # the pid of the session's `claude` process a hook finds, if any
        self.dead = set()  # the pids `alive` says no longer run
        self.walks = 0  # how many times a hook looked for it
        self.spawned = []
        self.now = None

    def start_dates(self, pid):
        return None if self.started is None else self.started.get(pid, [])

    def alive(self, pid):
        return pid not in self.dead

    def session_process(self):
        self.walks += 1
        return self.claude

    def directories(self):
        return self.cwd_list

    def clock(self):
        return self.now if self.now is not None else super().clock()


class Scratch(unittest.TestCase):
    """A test with a scratch folder, a scratch home and a host that runs real git there."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.tmp = Path(os.path.realpath(self.scratch.name))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.environ = {
            "PATH": "%s:%s" % (self.bin, os.environ.get("PATH", "/usr/bin:/bin")),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "LANG": "C",
            "SIGNALBOX_LOG_DIR": str(self.tmp / "logs"),
            "SIGNALBOX_CACHE_DIR": str(self.tmp / "cache"),
            "SIGNALBOX_CONFIG": str(self.tmp / "state" / "config.toml"),
            "SIGNALBOX_CLAUDE_SETTINGS": str(self.home / ".claude" / "settings.json"),
            "SIGNALBOX_SESSIONS_DIR": str(self.tmp / "sessions"),
        }
        self.host = StandInHost(self.environ)
        self.printed = []

    def tearDown(self):
        self.scratch.cleanup()

    def out(self, line=""):
        self.printed.append(line)

    def text(self):
        return "\n".join(self.printed) + "\n" if self.printed else ""

    # git

    def git(self, folder, *args, date=None):
        environ = dict(self.environ)
        if date:
            environ.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        done = subprocess.run(
            [GIT, *IDENTITY, "-c", "core.hooksPath=/dev/null", *args],
            cwd=folder,
            env=environ,
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args), done.stderr))
        return done.stdout

    def write(self, path, content):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit_all(self, folder, message, date=None):
        self.git(folder, "add", ".")
        self.git(folder, "commit", "-qm", message, date=date or hours_ago(1))

    def base_repo(self, name="repo"):
        """A repository on main with shared.txt and README.md, and origin/main at main."""
        repo = self.tmp / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.write(repo / "shared.txt", lines())
        self.write(repo / "README.md", lines())
        self.commit_all(repo, "base", hours_ago(100))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")
        return repo

    def add_worktree(self, repo, name, branch, start=None):
        folder = self.tmp / name
        self.git(repo, "worktree", "add", "-q", "-b", branch, str(folder), *([start] if start else []))
        return folder

    def repo_with_worktrees(self):
        """Worktree a commits a change to shared.txt's first line and its feature document,
        then leaves README.md changed and new-a.txt untracked; main then changes shared.txt's
        first line, so a conflicts. Worktree b, cut after that, commits its feature document
        and leaves shared.txt and README.md changed. A detached worktree is not listed."""
        repo = self.base_repo()
        a = self.add_worktree(repo, "wt-a", "a")
        self.write(a / "shared.txt", lines({1: "a"}))
        self.write(a / "docs/features/feature-a.md", "a")
        self.commit_all(a, "a", hours_ago(5.5))
        self.write(a / "README.md", lines({2: "a, not committed"}))
        self.write(a / "new-a.txt", "untracked")

        self.write(repo / "shared.txt", lines({1: "main"}))
        self.commit_all(repo, "main moves", hours_ago(50.5))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "main")

        b = self.add_worktree(repo, "wt-b", "b")
        self.write(b / "docs/features/feature-b.md", "b")
        self.commit_all(b, "b", hours_ago(30.2))
        self.write(b / "shared.txt", lines({6: "b"}))
        self.write(b / "README.md", lines({6: "b"}))

        self.git(repo, "worktree", "add", "-q", "--detach", str(self.tmp / "wt-detached"))
        return repo, a, b

    # stand-in programs

    def stub(self, name, body):
        """A program first on PATH, a shell script."""
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path

    def stub_gh(self, stdout="", stderr="", code=0):
        """A `gh` that records its arguments, one call a line, then answers as told."""
        calls = self.tmp / "gh-calls"
        answer = self.tmp / "gh-stdout"
        answer.write_text(stdout)
        complaint = self.tmp / "gh-stderr"
        complaint.write_text(stderr)
        self.stub("gh", "echo \"$@\" >> '%s'\ncat '%s'\ncat '%s' >&2\nexit %d" % (calls, answer, complaint, code))

        def recorded():
            return calls.read_text().splitlines() if calls.exists() else []

        return recorded

    def lane_log(self, records, name="shop"):
        path = self.tmp / "logs" / name / "lane.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join((json.dumps(record) if isinstance(record, dict) else record) + "\n" for record in records))
        return path


def lane_record(pull_request, started_at, status):
    return {
        "version": 1,
        "startedAt": started_at,
        "pullRequest": pull_request,
        "head": "a" * 40,
        "status": status,
        "waitSeconds": 0,
        "gateSeconds": 100,
        "durationSeconds": 120,
        "attempts": 1,
        "baseTip": "b" * 40,
        "tree": "c" * 40,
        "mergeCommit": "d" * 40 if status == "merged" else None,
    }


class Keys:
    """Stands in for a person at the terminal: presses the keys given, in order, and keeps
    each widget it was shown. `terminal` is whether a person is there at all."""

    def __init__(self, *keys, terminal=True):
        self.keys = list(keys)
        self.terminal = terminal
        self.shown = []  # every widget, before each key
        self.sequences = []  # the sequence it was in, before each key
        self.asked = 0  # how many sequences it was asked

    def ask(self, sequence):
        """Press the keys; with no person at a terminal and no key left, end as input that
        ended does."""
        from signalbox.ui import RUNNING, Missing

        if sequence.state != RUNNING:
            return sequence
        self.asked += 1
        while sequence.state == RUNNING:
            self.shown.append(sequence.widget)
            self.sequences.append(sequence)
            if not self.keys and not self.terminal:
                raise Missing(sequence.flag, sequence.widget.title)
            if not self.keys:
                raise AssertionError('no key was left to press on "%s"' % sequence.widget.title)
            sequence = sequence.press(self.keys.pop(0))
        return sequence

    def titles(self):
        """The titles of the screens shown, in order, each once in a row."""
        titles = []
        for widget in self.shown:
            if not titles or titles[-1] != widget.title:
                titles.append(widget.title)
        return titles

    def last(self, title):
        """The widget of a screen as it was last shown."""
        return [widget for widget in self.shown if widget.title == title][-1]

    def at(self, title):
        """The sequence as it was when a screen was last shown."""
        return [sequence for sequence in self.sequences if sequence.widget.title == title][-1]
