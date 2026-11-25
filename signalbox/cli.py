"""The command line: `signalbox <command>`.

    setup                      once: check the prerequisites, choose the defaults, install the hooks
    init [folder]              link a repository's folder, choosing its features
    deinit [folder]            unlink it
    status                     the board: every worktree of this repository
    brief, changes             what the session hooks print
    sweep                      remove merged worktrees and merged local branches
    merge <pr>                 the merge lane
    metrics                    flow metrics
    doctor                     check what each project needs on this Mac
    hook <event>               what Claude Code's hooks run
    uninstall                  remove the hooks

A command about a repository runs in a folder of it (any worktree), and says how to link one
it is not linked. Exit codes: 0 done, 1 refused or something needs attention, 2 the command
line is wrong. `brief`, `changes` and `hook` exit 0 whatever happens (the guard exits 2 to
refuse), so a hook never blocks a session by failing. This file only reads the command line
and prints: what a command does is in the module it calls.
"""

import argparse
import json
import os
import signal
import sys
from pathlib import Path

from . import __version__, config, linked, names
from . import repository as repositories
from .atoms import tools
from .features import board, brief, guard, lane, sweep
from .host import Host

FEATURE_NAMES = {
    "status": "board",
    "brief": "session-brief",
    "changes": "session-brief",
    "sweep": "sweep",
    "merge": "merge-lane",
    "metrics": "metrics",
}
BOOT = "import sys; sys.path.insert(0, sys.argv.pop(1)); from signalbox.cli import main; sys.exit(main(sys.argv[1:]))"
HOOK_EVENTS = ("session-start", "user-prompt-submit", "pre-tool-use")


def printer(stream):
    def write(line=""):
        stream.write(line + "\n")
        stream.flush()  # a caller reads the lane's progress while it runs

    return write


def pull_request(text):
    if not text.isdigit() or text.startswith("0"):
        raise argparse.ArgumentTypeError("%r is not a pull request number" % text)
    return int(text)


def package_parent():
    return str(Path(__file__).resolve().parent.parent)


def in_background(words, host, folder):
    """Run `signalbox <words>` detached from this process, in `folder`; never raises."""
    try:
        host.spawn([sys.executable, "-I", "-c", BOOT, package_parent(), *words], cwd=str(folder))
    except Exception:
        pass


class Context:
    """What a command about a repository needs: the configuration, the host, the folder."""

    def __init__(self, chosen, host, out, err, folder):
        self.chosen = chosen
        self.host = host
        self.out = out
        self.err = err
        self.folder = folder
        self.config_file = chosen.config or names.config_file(host.environ)

    def configuration(self):
        return config.load(self.config_file)

    def repository(self, feature):
        """The repository of the folder's project, when it has the feature; Refusal otherwise."""
        project, _ = linked.project_for(self.configuration(), self.folder, self.host.git)
        if project is None:
            raise repositories.Refusal(
                "%s is in no linked project. Link its repository with: %s" % (self.folder, names.command("init"))
            )
        if not project.has(feature):
            raise repositories.Refusal(
                'The project "%s" does not have the %s feature. Turn it on with: %s'
                % (project.name, feature, names.command("init", str(project.directory)))
            )
        return repositories.open(project, self.host, self.folder)


def status(context):
    chosen = context.chosen
    return board.status(context.repository("board"), context.host, context.out, as_json=chosen.json, github=not chosen.no_github)


def quietly(run):
    """A hook's command: whatever fails, print nothing and exit 0."""

    def command(context):
        try:
            run(context)
        except Exception:
            pass
        return 0

    return command


@quietly
def brief_command(context):
    lines = []
    brief.brief(context.repository("session-brief"), context.host, lines.append)
    for line in lines:
        context.out(line)


@quietly
def changes_command(context):
    lines = []
    brief.changes(context.repository("session-brief"), context.host, lines.append)
    for line in lines:
        context.out(line)


def sweep_command(context):
    chosen = context.chosen
    if chosen.background:
        # The session-start hook: the sweep runs detached, so a session never waits for a
        # removal and a hook's timeout never interrupts one. The other arguments go with it.
        words = ["sweep"] + (["--dry-run"] if chosen.dry_run else []) + (["--json"] if chosen.json else [])
        if chosen.config:
            words += ["--config", str(chosen.config)]
        in_background(words, context.host, context.folder)
        return 0
    repository = context.repository("sweep")
    return sweep.sweep(
        repository, context.host, context.out, dry_run=chosen.dry_run, as_json=chosen.json, caller=sweep.caller_folders(context.host.environ)
    )


def merge_command(context):
    chosen = context.chosen
    repository = context.repository("merge-lane")

    def stopped(number, frame):
        raise lane.Interrupted(143)

    previous = signal.signal(signal.SIGTERM, stopped)
    try:
        return lane.merge(
            repository,
            context.host,
            context.out,
            context.err,
            chosen.number,
            dry_run=chosen.dry_run,
            allow_gate_change=chosen.allow_gate_change,
            caller=sweep.caller_folders(context.host.environ),
        )
    finally:
        signal.signal(signal.SIGTERM, previous)


def read_hook_input(stdin):
    try:
        data = json.loads(stdin.read())
    except (ValueError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def hook_command(context):
    """What Claude Code's hooks run. Nothing outside a linked project; never a failure: the
    guard's refusal (exit 2) is the only exit other than 0."""
    try:
        return run_hook(context)
    except Exception:
        return 0


def run_hook(context):
    event = context.chosen.event
    data = read_hook_input(context.chosen.stdin)
    if data is None:
        return 0
    if event == "pre-tool-use":
        command = (data.get("tool_input") or {}).get("command") if isinstance(data.get("tool_input"), dict) else None
        if data.get("tool_name") != "Bash" or not isinstance(command, str) or "merge" not in command:
            return 0  # the usual case, decided before reading any file
    folder = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else os.getcwd()
    if not os.path.isdir(folder):
        return 0
    project, _ = linked.project_for(context.configuration(), folder, context.host.git)
    if project is None:
        return 0
    if event == "pre-tool-use":
        if not project.has("merge-guard"):
            return 0
        number = guard.direct_merge(command)
        if number is None:
            return 0
        context.err(guard.refusal(number))
        return 2
    if event == "session-start":
        if project.has("session-brief"):
            lines = []
            brief.brief(repositories.open(project, context.host, folder), context.host, lines.append)
            for line in lines:
                context.out(line)
        if project.has("sweep"):
            words = ["sweep"] + (["--config", str(context.chosen.config)] if context.chosen.config else [])
            in_background(words, context.host, folder)
        return 0
    if event == "user-prompt-submit" and project.has("session-brief"):
        lines = []
        brief.changes(repositories.open(project, context.host, folder), context.host, lines.append)
        for line in lines:
            context.out(line)
    return 0


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", metavar="<file>", type=Path, default=argparse.SUPPRESS, help="the configuration file")
    top = argparse.ArgumentParser(
        prog=names.TOOL,
        parents=[common],
        description="Lanes for parallel Claude Code sessions in git worktrees.",
    )
    top.add_argument("--version", action="store_true", help="print the version and exit")
    commands = top.add_subparsers(dest="command", metavar="<command>")

    def command(name, run, summary):
        added = commands.add_parser(name, parents=[common], help=summary, description=summary)
        added.set_defaults(run=run)
        return added

    showing = command("status", status, "Show the board: what every worktree of this repository changes.")
    showing.add_argument("--json", action="store_true", help="print one JSON object")
    showing.add_argument("--no-github", action="store_true", help="leave out the open pull requests (no gh)")
    command("brief", brief_command, "Print what the session-start hook tells a session.")
    command("changes", changes_command, "Print what changed in the other worktrees since the last run here.")
    sweeping = command("sweep", sweep_command, "Remove merged, clean worktrees and merged local branches.")
    sweeping.add_argument("--dry-run", action="store_true", help="say what it would do, change nothing")
    sweeping.add_argument("--json", action="store_true", help="print one JSON object")
    sweeping.add_argument("--background", action="store_true", help="start it detached, print nothing, exit 0")
    merging = command("merge", merge_command, "Merge a pull request through the lane, once its merged result passes the gate.")
    merging.add_argument("number", type=pull_request, metavar="<pr>", help="the pull request's number")
    merging.add_argument("--dry-run", action="store_true", help="merge and run the gate in the merge worktree, then stop")
    merging.add_argument(
        "--allow-gate-change", action="store_true", help="run a pull request that changes the gate's own files"
    )
    hooking = command("hook", hook_command, "What Claude Code's hooks run (reads the hook's JSON on stdin).")
    hooking.add_argument("event", choices=HOOK_EVENTS)
    return top


def main(arguments, host=None, out=None, err=None, stdin=None, folder=None):
    """Run one command; its exit code. `host` stands in for the machine, `out` and `err` for
    the two outputs, `folder` for the current directory."""
    out = out or printer(sys.stdout)
    err = err or printer(sys.stderr)
    reading = parser()
    try:
        chosen = reading.parse_args(arguments)
    except SystemExit as stop:  # argparse printed the help (0), or what is wrong (2)
        return stop.code or 0
    if chosen.version:
        out("%s %s" % (names.TOOL, __version__))
        return 0
    if chosen.command is None:
        reading.print_usage(sys.stderr)
        sys.stderr.write("%s: error: name a command\n" % names.TOOL)
        return 2
    chosen.config = getattr(chosen, "config", None)
    chosen.stdin = stdin or sys.stdin
    host = host or Host()
    try:
        here = Path(folder) if folder else Path.cwd()
    except OSError:
        here = Path(host.environ.get("PWD") or "/")
    context = Context(chosen, host, out, err, here)
    try:
        return chosen.run(context)
    except config.ConfigError as error:
        for key, problem in error.problems:
            err("%s: %s%s" % (context.config_file, key + ": " if key else "", problem))
        return 1
    except repositories.Refusal as refusal:
        err("%s: %s" % (names.TOOL, refusal))
        return 1
    except tools.ToolNotFound as error:
        err("%s: %s" % (names.TOOL, error))
        return 1
