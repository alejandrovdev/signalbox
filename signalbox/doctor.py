"""What signalbox needs on this Mac and in each project, checked.

    prerequisites(host, settings)   git (2.38 or later), gh signed in, Claude Code, the hooks
    checks(project, host)           the folder, the repository, the base on origin, and what
                                    the project's features need
    report(configuration, host, settings, out)   print it all; 0 when nothing failed

Nothing is changed. The only programs run are `git --version`, `gh auth status` and git
reading each repository.
"""

import re
from dataclasses import dataclass

from . import hooks, names
from . import repository as repositories
from .atoms import worktrees

OK, MISSING, PROBLEM, UNKNOWN = "ok", "missing", "problem", "unknown"
GIT_NEEDED = (2, 38)  # merge-tree --write-tree


@dataclass(frozen=True)
class Check:
    state: str
    what: str
    advice: str = ""

    @property
    def failed(self):
        return self.state in (MISSING, PROBLEM)

    def line(self):
        return "%-7s  %s" % (self.state, self.what + (": " + self.advice if self.advice else ""))


def git_check(host):
    found = host.find("git")
    if found is None:
        return Check(MISSING, "git", "install it with: brew install git")
    done = host.run([found, "--version"])
    version = re.search(r"(\d+)\.(\d+)", done.stdout or "")
    if version is None:
        return Check(UNKNOWN, "git at %s" % found, "its version could not be read")
    numbers = (int(version.group(1)), int(version.group(2)))
    shown = "git %d.%d" % numbers
    if numbers < GIT_NEEDED:
        return Check(PROBLEM, shown, "2.38 or later is needed: brew install git")
    return Check(OK, shown)


def gh_check(host):
    gh = host.gh()
    if gh is None:
        return Check(MISSING, "gh", "install it with: brew install gh")
    if host.run([gh, "auth", "status"]).returncode != 0:
        return Check(PROBLEM, "gh", "not signed in: sign in with: gh auth login")
    return Check(OK, "gh, signed in")


def claude_check(host):
    found = host.find("claude")
    if found is None:
        return Check(MISSING, "Claude Code (claude)", "the session brief and the merge guard need it")
    return Check(OK, "Claude Code at %s" % found)


def hooks_check(settings):
    present = hooks.installed(settings)
    if present == list(hooks.EVENTS):
        return Check(OK, "Claude Code hooks in %s" % settings)
    return Check(MISSING, "Claude Code hooks", "install them with: %s" % names.command("setup"))


def prerequisites(host, settings):
    return [git_check(host), gh_check(host), claude_check(host), hooks_check(settings)]


def checks(project, host):
    folder = project.directory
    if not folder.is_dir():
        return [Check(MISSING, "folder %s" % folder, "create it, or correct `path` of the project in the configuration")]
    results = [Check(OK, "folder %s" % folder)]
    if host.find("git") is None:
        return results
    where = worktrees.locate(host.git, folder)
    if where is None:
        return results + [Check(PROBLEM, "git repository", "%s is not one: correct `path`" % folder)]
    results.append(Check(OK, "git repository"))
    try:
        opened = repositories.open(project, host, folder)
        results.append(Check(OK, "base branch %s" % opened.remote_base))
    except repositories.Refusal as refusal:
        results.append(Check(PROBLEM, "base branch", str(refusal)))
    if project.has("merge-lane"):
        if project.gate:
            results.append(Check(OK, "gate: %s" % project.gate))
        else:
            results.append(Check(PROBLEM, "gate", "set `gate` for the project: the merge lane runs it"))
        url = host.run([host.git.path, "-C", str(folder), "remote", "get-url", "origin"]).stdout.strip()
        if "github.com" in url:
            results.append(Check(OK, "origin is on GitHub"))
        else:
            results.append(Check(PROBLEM, "origin", "the merge lane merges through GitHub; origin is %s" % (url or "not set")))
    return results


def report(configuration, host, settings, out):
    failed = False
    out("Prerequisites")
    for check in prerequisites(host, settings):
        out("  " + check.line())
        failed = failed or check.failed
    if not configuration.projects:
        out("")
        out(names.no_project())
        return 1 if failed else 0
    for project in configuration.projects:
        out("")
        out(project.name)
        try:
            results = checks(project, host)
        except Exception as error:  # one project never hides the others
            results = [Check(PROBLEM, "its checks could not run", repr(error))]
        for check in results:
            out("  " + check.line())
            failed = failed or check.failed
    return 1 if failed else 0
