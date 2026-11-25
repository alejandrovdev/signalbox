"""The command line: `signalbox <command>`.

    setup                      once: check the prerequisites (and, with Homebrew, offer to
                               install what is missing), choose the defaults, install the hooks
    init [folder]              link a repository's folder, choosing its features
    deinit [folder]            unlink it (--shared-hooks: also remove the repository's hooks)
    status                     the board: every worktree of this repository (--group: of
                               each repository of its group)
    brief, changes             what the session hooks print
    sweep                      remove merged worktrees and merged local branches
    merge <pr> [<project>#<pr> ...]
                               the merge lane; with pull requests of other projects of its
                               group, each in the group's order, stopping at the first that
                               does not merge
    metrics                    flow metrics
    doctor                     check what each project needs on this Mac
    hook <event>               what Claude Code's hooks run; each also records where its
                               session stands, so the sweep keeps that worktree
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
import re
import signal
import sys
from pathlib import Path

from . import __version__, config, doctor, hooks, linked, linking, names, ui
from . import repository as repositories
from .atoms import sessions, tools
from .features import board, brief, groups, guard, lane, metrics, sweep
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


PROJECT_PULL = re.compile(r"^([a-z0-9][a-z0-9-]{0,31})#([1-9][0-9]*)$")


def project_pull(text):
    found = PROJECT_PULL.fullmatch(text)
    if found is None:
        raise argparse.ArgumentTypeError("%r is not <project>#<pr>, such as api#12" % text)
    return found.group(1), int(found.group(2))


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

    def __init__(self, chosen, host, out, err, folder, asker=None):
        self.chosen = chosen
        self.host = host
        self.out = out
        self.err = err
        self.folder = folder
        self.asker = asker or ui.Console()
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


def group_of(context, project):
    """The group of the folder's project; Refusal when it is in none."""
    group = context.configuration().group_of(project.name)
    if group is None:
        raise repositories.Refusal(
            'The project "%s" is in no group. Make one in the configuration (%s): [groups.<name>] with projects = ["%s", '
            '"<another project>"], in dependency order.' % (project.name, context.config_file, project.name)
        )
    return group


def opened_or_why(project, host):
    try:
        return repositories.open(project, host, project.directory)
    except repositories.Refusal as refusal:
        return str(refusal)


def status(context):
    chosen = context.chosen
    repository = context.repository("board")
    if not chosen.group:
        return board.status(repository, context.host, context.out, as_json=chosen.json, github=not chosen.no_github)
    group, configuration = group_of(context, repository.project), context.configuration()
    opened = []
    for name in group.projects:
        project = configuration.project(name)
        opened.append((project, repository if project == repository.project else opened_or_why(project, context.host)))
    return board.group_status(group.name, opened, context.host, context.out, as_json=chosen.json, github=not chosen.no_github)


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
    brief.brief(context.repository("session-brief"), context.host, lines.append, context.configuration())
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


def group_runs(context, repository, others):
    """The group and (repository, number) of each pull request, in the group's order. Usage
    when a project is named twice, unknown or outside the folder's project's group."""
    project, configuration = repository.project, context.configuration()
    group = configuration.group_of(project.name)
    if group is None:
        raise linking.Usage(
            'the project "%s" is in no group: name pull requests of other projects only within a group '
            "([groups.<name>] in the configuration)" % project.name
        )
    named = {project.name: (repository, context.chosen.number)}
    for name, number in others:
        if name in named:
            raise linking.Usage('the project "%s" is named twice' % name)
        if configuration.project(name) is None:
            raise linking.Usage('unknown project "%s"' % name)
        if name not in group.projects:
            raise linking.Usage('the project "%s" is not in the group "%s" of "%s"' % (name, group.name, project.name))
        named[name] = (None, number)
    for name, (opened, number) in named.items():
        if opened is None:
            other = configuration.project(name)
            if not other.has("merge-lane"):
                raise repositories.Refusal(
                    'The project "%s" does not have the merge-lane feature. Turn it on with: %s'
                    % (name, names.command("init", str(other.directory)))
                )
            named[name] = (repositories.open(other, context.host, other.directory), number)
    return group, [named[name] for name in group.projects if name in named]


def merge_command(context):
    chosen = context.chosen
    repository = context.repository("merge-lane")
    caller = sweep.caller_folders(context.host.environ)
    group = runs = None
    if chosen.others:
        group, runs = group_runs(context, repository, chosen.others)

    def stopped(number, frame):
        raise lane.Interrupted(143)

    previous = signal.signal(signal.SIGTERM, stopped)
    try:
        if runs:
            return groups.merge(
                group, runs, context.host, context.out, context.err, chosen.dry_run, chosen.allow_gate_change, caller
            )
        return lane.merge(
            repository,
            context.host,
            context.out,
            context.err,
            chosen.number,
            dry_run=chosen.dry_run,
            allow_gate_change=chosen.allow_gate_change,
            caller=caller,
        )
    finally:
        signal.signal(signal.SIGTERM, previous)


def metrics_command(context):
    chosen = context.chosen
    return metrics.report(
        context.repository("metrics"),
        context.host,
        context.out,
        since=chosen.since,
        until=chosen.until,
        as_json=chosen.json,
        github=not chosen.no_github,
    )


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
    if context.chosen.shared and hooks.installed(names.claude_settings(context.host.environ)):
        return 0  # the person's own hooks run it: once is enough
    sessions.record(context.host, data)  # where the session stands, for the sweep; cheap and silent
    if event == "pre-tool-use":
        command = (data.get("tool_input") or {}).get("command") if isinstance(data.get("tool_input"), dict) else None
        if data.get("tool_name") != "Bash" or not isinstance(command, str):
            return 0
        if not any(word in command for word in ("gh", "curl", "wget")):
            return 0  # the usual case, decided before reading any file
    folder = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else os.getcwd()
    if not os.path.isdir(folder):
        return 0
    configuration = context.configuration()
    project, _ = linked.project_for(configuration, folder, context.host.git)
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
            brief.brief(repositories.open(project, context.host, folder), context.host, lines.append, configuration)
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
    showing.add_argument("--group", action="store_true", help="the board of each repository of this project's group, in its order")
    command("brief", brief_command, "Print what the session-start hook tells a session.")
    command("changes", changes_command, "Print what changed in the other worktrees since the last run here.")
    sweeping = command("sweep", sweep_command, "Remove merged, clean worktrees and merged local branches.")
    sweeping.add_argument("--dry-run", action="store_true", help="say what it would do, change nothing")
    sweeping.add_argument("--json", action="store_true", help="print one JSON object")
    sweeping.add_argument("--background", action="store_true", help="start it detached, print nothing, exit 0")
    merging = command(
        "merge",
        merge_command,
        "Merge a pull request through the lane, once its merged result passes the gate. With pull requests of other "
        "projects of its group, merge each through its own lane in the group's order, stopping at the first that does "
        "not merge: what merged stays merged.",
    )
    merging.add_argument("number", type=pull_request, metavar="<pr>", help="the pull request's number, in this folder's project")
    merging.add_argument(
        "others",
        nargs="*",
        type=project_pull,
        metavar="<project>#<pr>",
        help="a pull request of another project of the group, such as api#12",
    )
    merging.add_argument("--dry-run", action="store_true", help="merge and run the gate in the merge worktree, then stop")
    merging.add_argument(
        "--allow-gate-change", action="store_true", help="run a pull request that changes the gate's own files"
    )
    measuring = command("metrics", metrics_command, "Print flow metrics: merges, pull requests, the merge lane, the worktrees now.")
    measuring.add_argument("--since", type=metrics.parse_date, metavar="<YYYY-MM-DD>", help="the first local day (default: 14 days ago)")
    measuring.add_argument("--until", type=metrics.parse_date, metavar="<YYYY-MM-DD>", help="the local day the window ends at, excluded (default: now)")
    measuring.add_argument("--json", action="store_true", help="print one JSON object")
    measuring.add_argument("--no-github", action="store_true", help="leave out the pull requests (no gh)")
    hooking = command("hook", hook_command, "What Claude Code's hooks run (reads the hook's JSON on stdin).")
    hooking.add_argument("event", choices=HOOK_EVENTS)
    hooking.add_argument("--shared", action="store_true", help="run from a repository's settings: nothing when the person's hooks run")

    def chosen_features(added):
        chosen = added.add_mutually_exclusive_group()
        chosen.add_argument("--preset", choices=tuple(config.PRESETS), help="a preset of features")
        chosen.add_argument(
            "--feature",
            action="append",
            choices=tuple(config.FEATURES),
            metavar="<feature>",
            help="a feature, repeated for each one: %s" % ", ".join(config.FEATURES),
        )
        added.add_argument("--yes", action="store_true", help="take what each screen no flag answers starts from, and confirm")

    setting_up = command(
        "setup", lambda context: linking.setup(context.chosen, context), "Set up once: check the prerequisites, choose the defaults, install the Claude Code hooks."
    )
    chosen_features(setting_up)
    setting_up.add_argument("--no-hooks", action="store_true", help="do not install the hooks in ~/.claude/settings.json")
    setting_up.add_argument(
        "--install-missing",
        action="store_true",
        help="install what is missing (git, gh, Claude Code) with Homebrew once the summary is confirmed (--yes alone installs nothing)",
    )
    linking_folder = command("init", lambda context: linking.init(context.chosen, context), "Link a repository's folder, choosing its features and gate.")
    linking_folder.add_argument("folder", nargs="?", help="a folder of the repository (default: this one)")
    linking_folder.add_argument("--name", metavar="<name>", help="the project's name")
    linking_folder.add_argument("--gate", metavar="<command>", help="what the merge lane runs on each merged result")
    linking_folder.add_argument("--base", metavar="<branch>", help="the base branch (default: origin's, else main)")
    linking_folder.add_argument("--shared-hooks", action="store_true", help="also write the hooks in the repository's .claude/settings.json")
    chosen_features(linking_folder)
    unlinking = command("deinit", lambda context: linking.deinit(context.chosen, context), "Unlink a repository's folder.")
    unlinking.add_argument("folder", nargs="?", help="a folder of the repository (default: this one)")
    unlinking.add_argument(
        "--shared-hooks",
        action="store_true",
        help="also remove the hooks init --shared-hooks wrote in the repository's .claude/settings.json",
    )
    command(
        "doctor",
        lambda context: doctor.report(context.configuration(), context.host, names.claude_settings(context.host.environ), context.out),
        "Check what signalbox and each project need on this Mac.",
    )
    removing = command("uninstall", lambda context: linking.uninstall(context.chosen, context), "Remove the Claude Code hooks.")
    removing.add_argument("--purge", action="store_true", help="also remove the configuration, the logs and the merge worktrees")
    return top


def main(arguments, host=None, out=None, err=None, stdin=None, folder=None, asker=None):
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
    context = Context(chosen, host, out, err, here, asker)
    try:
        return chosen.run(context)
    except linking.Usage as error:
        prog = "%s %s" % (names.TOOL, chosen.command)
        sys.stderr.write("usage: %s ...\n%s: error: %s\n" % (prog, prog, error))
        return 2
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
