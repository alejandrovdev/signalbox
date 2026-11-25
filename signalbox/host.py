"""The machine as the features see it: its programs, processes, clock and environment.

A feature receives a `Host` and asks it for everything outside the repository's files: git,
gh, the start date of a process, the current directories of processes, the clock. Tools are
looked for when first needed, so a project without the merge lane needs no `gh`. A test
passes a host whose `ps` and `lsof` are stand-ins and whose PATH holds stub programs.
"""

import os
import re
import subprocess
import time
from functools import cached_property
from types import SimpleNamespace

from . import names
from .atoms import git, tools


def failed(words, error):
    """What a program that could not start answers: exit 127 and why."""
    return SimpleNamespace(args=list(words), returncode=127, stdout="", stderr=str(error))


class Host:
    def __init__(self, environ=None):
        self.environ = dict(os.environ) if environ is None else environ

    def tool(self, name):
        return tools.need(name, override=names.chosen(name, self.environ), path=self.environ.get("PATH"))

    def find(self, name):
        """Where a tool is, or None."""
        return tools.find(name, override=names.chosen(name, self.environ), path=self.environ.get("PATH"))

    def run(self, words, cwd=None, input=None, extra=None, timeout=None):
        """A program's (returncode, stdout, stderr), as text; 127 when it cannot start."""
        environ = dict(self.environ, **(extra or {}))
        try:
            return subprocess.run(
                list(words),
                cwd=cwd,
                input=input,
                env=environ,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=None if input is not None else subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return failed(words, error)

    @cached_property
    def git(self):
        return git.Git(self.tool("git"), self.run)

    def gh(self):
        """The gh program, or None when it is not installed."""
        found = self.find("gh")
        return found if found and os.access(found, os.X_OK) else None

    def clock(self):
        return time.time()

    def start_dates(self, pid):
        """How `ps` prints the start of a process: in UTC and the C locale (as Claude Code
        writes it in a worktree's lock), then in this machine's zone. Empty when no process
        has that pid; None when `ps` cannot be read."""
        dates = []
        for extra in ({"LC_ALL": "C", "TZ": "UTC"}, {}):
            done = self.run(["ps", "-o", "lstart=", "-p", str(pid)], extra=extra)
            if done.returncode == 127:
                return None
            if done.returncode != 0 or not done.stdout.strip():
                return []
            dates.append(" ".join(done.stdout.split()))
        return dates

    def directories(self):
        """(pid, current directory) of every process `lsof` can read; None when it cannot."""
        done = self.run(["lsof", "-d", "cwd", "-Fn"])
        if done.returncode == 127 or (done.returncode != 0 and not done.stdout):
            return None
        found, pid = [], None
        for line in done.stdout.splitlines():
            if line.startswith("p") and re.fullmatch(r"p\d+", line):
                pid = int(line[1:])
            elif line.startswith("n") and pid is not None:
                found.append((pid, line[1:]))
        return found

    def spawn(self, words, cwd=None):
        """Start a program detached from this one, with nothing to read and nowhere to print."""
        nowhere = subprocess.DEVNULL
        return subprocess.Popen(
            list(words), cwd=cwd, env=self.environ, stdin=nowhere, stdout=nowhere, stderr=nowhere, start_new_session=True
        )
