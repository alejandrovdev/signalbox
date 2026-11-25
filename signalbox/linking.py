"""Setting up and linking: `setup` once, `init` in each repository, `deinit` to unlink one,
`uninstall` to take the hooks out.

    setup(chosen, context)      the defaults new projects start from, and the user-level hooks
    init(chosen, context)       add or replace the project of a folder's repository
    deinit(chosen, context)     remove it, with the merge worktree signalbox made for it and,
                                with --shared-hooks, the hooks init wrote in the repository
    uninstall(chosen, context)  remove the hooks; with purge, the configuration, logs and caches

`setup` and `init` ask their screens through the context's asker (drawn on the terminal, or
plain questions) and change nothing until the last one is confirmed. A flag answers its
screen beforehand; `--yes` takes what each other screen would start from, and confirms. A
screen still to answer when the input ends is a usage error that names its flag (`Usage`).
"""

import json
import os
import shutil
from pathlib import Path

from . import answers, config, doctor, hooks, linked, names, screens, ui
from .atoms import invocation, lock, worktrees
from .repository import FALLBACK_BASE, REMOTE

NOTHING = "Nothing was changed."
NEXT_INIT = "Next: in a repository's folder, run: " + names.command("init")


class Usage(Exception):
    """The command line misses or gets wrong something a command needs (exit 2)."""


def given_features(chosen):
    if getattr(chosen, "preset", None):
        return config.PRESETS[chosen.preset]
    if not getattr(chosen, "feature", None):
        return None
    features = config.ordered(chosen.feature)
    problem = answers.features_problem(features)
    if problem:
        raise Usage("argument --feature: " + problem)
    return features


def asked(asker, steps, start, flags, yes):
    """Ask the screens no flag answered, starting from `start`: the answers, or None when the
    person quit or said no."""
    given = {key for key, value in flags.items() if value is not None}
    known = dict(start, **{key: flags[key] for key in given})
    if yes:
        given |= {key for key, value in known.items() if value is not None} | {answers.PREREQUISITES, answers.CONFIRM}
        known[answers.CONFIRM] = True
    sequence = ui.Sequence.start(steps, known, given)
    try:
        sequence = asker.ask(sequence)
    except ui.Missing as missing:
        raise Usage(str(missing))
    return sequence.answers if sequence.state == ui.DONE else None


def saved(configuration, path, out):
    """Write a configuration once it reads back as itself; whether it was written."""
    try:
        config.parse(config.dump(configuration))
    except config.ConfigError as error:
        for key, problem in error.problems:
            out("%s: %s" % (key, problem) if key else problem)
        out(NOTHING)
        return False
    config.save(configuration, path)
    return True


def hook_command(host, shared=False):
    """What a hook runs: `signalbox` as a teammate's PATH finds it, for the repository's
    settings; for the person's, the installed command, else this very copy."""
    if shared:
        return names.TOOL
    words, environment = invocation.words(names.TOOL, path=host.environ.get("PATH"))
    return invocation.shell(words, environment)


def setup(chosen, context):
    configuration, out = context.configuration(), context.out
    settings = names.claude_settings(context.host.environ)
    install = not chosen.no_hooks
    steps = [
        screens.prerequisites(doctor.prerequisites(context.host, settings)),
        screens.features("Default features"),
        screens.confirm("Save these defaults?", lambda given: answers.setup_summary(configuration, given, settings, install)),
    ]
    got = asked(context.asker, steps, answers.setup_answers(configuration), {answers.FEATURES: given_features(chosen)}, chosen.yes)
    if got is None:
        out(NOTHING)
        return 1
    if not saved(answers.with_setup(configuration, got), context.config_file, out):
        return 1
    out("Saved the configuration in %s." % context.config_file)
    if install:
        try:
            changed = hooks.install(settings, hook_command(context.host))
        except hooks.SettingsError as error:
            out("The Claude Code hooks were not installed: %s" % error)
            return 1
        out(("Installed the Claude Code hooks in %s." if changed else "The Claude Code hooks were installed already in %s.") % settings)
    out(NEXT_INIT)
    return 0


GATE_SCRIPTS = ("verify", "check")


def proposed_gate(folder):
    """A gate from what the repository has: a verify or check script of package.json, a check
    or verify target of a Makefile, scripts/verify.sh; None when it has none."""
    folder = Path(folder)
    try:
        scripts = json.loads((folder / "package.json").read_text(encoding="utf8")).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        scripts = {}
    for name in GATE_SCRIPTS:
        if isinstance(scripts, dict) and name in scripts:
            if (folder / "pnpm-lock.yaml").exists():
                return "pnpm %s" % name
            if (folder / "yarn.lock").exists():
                return "yarn %s" % name
            return "npm run %s" % name
    try:
        makefile = (folder / "Makefile").read_text(encoding="utf8")
    except OSError:
        makefile = ""
    for name in ("check", "verify"):
        if any(line.startswith(name + ":") for line in makefile.splitlines()):
            return "make %s" % name
    if (folder / "scripts" / "verify.sh").exists():
        return "bash scripts/verify.sh"
    return None


def init(chosen, context):
    out, host = context.out, context.host
    folder = Path(os.path.expanduser(chosen.folder)) if chosen.folder else context.folder
    if not folder.is_dir():
        out("%s is not a folder. %s" % (folder, NOTHING))
        return 1
    where = worktrees.locate(host.git, folder)
    if where is None:
        out("%s is not in a git repository: run git init in it first. %s" % (folder, NOTHING))
        return 1
    top = Path(worktrees.listed(host.git, where.top)[0].path)  # the main checkout
    configuration = context.configuration()
    existing, _ = linked.project_for(configuration, folder, host.git)

    def check(name):
        return answers.name_problem(name, configuration, existing)

    if chosen.name is not None and check(chosen.name):
        raise Usage("argument --name: " + check(chosen.name))
    if chosen.gate is not None and answers.gate_problem(chosen.gate):
        raise Usage("argument --gate: " + answers.gate_problem(chosen.gate))
    flags = {answers.NAME: chosen.name, answers.FEATURES: given_features(chosen), answers.GATE: chosen.gate}
    configured_base = chosen.base or (existing.base if existing is not None else None)
    base = configured_base or worktrees.default_branch(host.git, top, REMOTE) or FALLBACK_BASE

    def question(given):
        if existing is None:
            return "Link this repository?"
        return "Rename it?" if given[answers.NAME] != existing.name else "Replace its settings?"

    steps = [
        screens.name(check),
        screens.features("Features"),
        screens.gate(),
        screens.confirm(question, lambda given: answers.init_summary(given, top, base, existing)),
    ]
    start = answers.init_answers(configuration, existing, answers.suggested_name(top, configuration), proposed_gate(top))
    got = asked(context.asker, steps, start, flags, chosen.yes)
    if got is None:
        out(NOTHING)
        return 1
    configured, project = answers.with_project(configuration, got, top, existing, chosen.base)
    if not saved(configured, context.config_file, out):
        return 1
    if existing is not None and existing.name != project.name:
        out('Renamed "%s" to "%s".' % (existing.name, project.name))
    out('Linked "%s" (%s) in %s.' % (project.name, project.path, context.config_file))
    if chosen.shared_hooks:
        shared = shared_settings(top)
        try:
            hooks.install(shared, hook_command(host, shared=True), suffix=" --shared")
        except hooks.SettingsError as error:
            out("The repository's hooks were not written: %s" % error)
            return 1
        out("Wrote the hooks in %s: commit it so the team gets them." % shared)
    needs_claude = project.has("session-brief") or project.has("merge-guard")
    if needs_claude and hooks.installed(names.claude_settings(host.environ)) != list(hooks.EVENTS) and not chosen.shared_hooks:
        out("The Claude Code hooks are not installed: run %s." % names.command("setup"))
    if project.has("board"):
        out("See the board with: %s" % names.command("status"))
    return 0


def merge_worktree(project, environ):
    configured = project.merge_worktree
    return (Path(os.path.expanduser(configured)) if configured else names.default_merge_worktree(project.name, environ)), configured is None


def remove_merge_worktree(project, host, out):
    """Remove the merge worktree signalbox made for a project, unless a lane runs; one the
    configuration placed is left, and said."""
    path, made = merge_worktree(project, host.environ)
    if not path.exists():
        return
    if not made:
        out("Left the merge worktree %s, which the configuration placed: remove it with: git worktree remove --force %s" % (path, path))
        return
    where = worktrees.locate(host.git, project.directory) if project.directory.is_dir() else None
    if where is not None and lock.probe(Path(where.common_dir) / names.LANE_LOCK) == lock.BUSY:
        out("Left the merge worktree %s: a lane runs in it." % path)
        return
    if where is not None:
        host.run([host.git.path, "-C", str(where.top), "worktree", "remove", "--force", "--force", str(path)])
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    if where is not None:
        host.run([host.git.path, "-C", str(where.top), "worktree", "prune"])
    out("Removed the merge worktree %s." % path)


def shared_settings(top):
    """The repository's own Claude Code settings, where `init --shared-hooks` writes."""
    return Path(top) / ".claude" / "settings.json"


def remove_shared_hooks(top, out):
    """Take the `--shared` hooks out of a repository's settings; whether that worked."""
    path = shared_settings(top)
    try:
        removed = hooks.remove(path)
    except hooks.SettingsError as error:
        out("The shared hooks were not removed: %s" % error)
        return False
    out(("Removed the shared hooks from %s: commit that file." if removed else "No shared hook was in %s.") % path)
    return True


def unlinked_shared_hooks(folder, host, out):
    """`deinit --shared-hooks` in a repository no project links any more: the hooks only, when
    its settings hold them; None when there is nothing to do."""
    where = worktrees.locate(host.git, folder) if folder.is_dir() else None
    if where is None:
        return None
    top = Path(worktrees.listed(host.git, where.top)[0].path)  # the main checkout, as init
    if not hooks.installed(shared_settings(top)):
        return None
    return 0 if remove_shared_hooks(top, out) else 1


def deinit(chosen, context):
    out, host = context.out, context.host
    folder = Path(os.path.expanduser(chosen.folder)) if chosen.folder else context.folder
    configuration = context.configuration()
    if folder.is_dir():
        project, _ = linked.project_for(configuration, folder, host.git)
    else:  # a folder that is gone: by the path the configuration names
        project = next((one for one in configuration.projects if config.folder_key(one.path) == config.folder_key(str(folder))), None)
    if project is None and chosen.shared_hooks:
        done = unlinked_shared_hooks(folder, host, out)
        if done is not None:
            return done
    if project is None:
        listed = ", ".join("%s (%s)" % (other.name, other.path) for other in configuration.projects)
        out("No project is linked to %s. %s" % (folder, "The linked projects: " + listed if listed else names.no_project()))
        return 1
    remaining, said = answers.without(configuration, project)
    config.save(remaining, context.config_file)
    out('Unlinked "%s" (%s) from %s.' % (project.name, project.path, context.config_file))
    for line in said:
        out(line)
    remove_merge_worktree(project, host, out)
    left = [
        "its logs in %s" % names.project_logs(project.name, host.environ),
        "the lane's lock and state, and each worktree's %s, in the repository's git folder" % names.CHANGES_STATE,
        "the Claude Code hooks, which do nothing in a repository no project links",
    ]
    out("Left as they are: %s." % "; ".join(left))
    if chosen.shared_hooks:
        return 0 if remove_shared_hooks(project.directory, out) else 1
    if hooks.installed(shared_settings(project.directory)):
        out(
            "The shared hooks stay in %s: remove them with: %s"
            % (shared_settings(project.directory), names.command("deinit", str(project.directory), "--shared-hooks"))
        )
    return 0


def lane_running(project, host):
    """Whether a merge lane holds the project's lock now."""
    if not project.directory.is_dir():
        return False
    where = worktrees.locate(host.git, project.directory)
    return where is not None and lock.probe(Path(where.common_dir) / names.LANE_LOCK) == lock.BUSY


def uninstall(chosen, context):
    out, host = context.out, context.host
    settings = names.claude_settings(host.environ)
    if chosen.purge:
        try:
            configuration = context.configuration()
        except config.ConfigError:
            configuration = config.Config()
        busy = next((project for project in configuration.projects if lane_running(project, host)), None)
        if busy is not None:
            out('A merge lane runs in "%s": run this again once it ends. %s' % (busy.name, NOTHING))
            return 1
    try:
        removed = hooks.remove(settings)
    except hooks.SettingsError as error:
        out("The Claude Code hooks were not removed: %s" % error)
        return 1
    out(("Removed the Claude Code hooks from %s." if removed else "No signalbox hook was in %s.") % settings)
    places = [context.config_file, names.log_root(host.environ), names.cache_root(host.environ), names.sessions_dir(host.environ)]
    if not chosen.purge:
        out("Left as they are: the configuration %s, the logs in %s, the merge worktrees in %s and the sessions' records in %s. Remove them with: %s"
            % (places[0], places[1], places[2], places[3], names.command("uninstall", "--purge")))
        return 0
    for project in configuration.projects:
        remove_merge_worktree(project, host, out)
    for folder in places[1:]:
        if Path(folder).exists():
            shutil.rmtree(folder, ignore_errors=True)
            out("Removed %s." % folder)
    path = Path(context.config_file)
    if path.exists():
        path.unlink()
        out("Removed %s." % path)
    marker = path.parent / names.MARKER
    if marker.exists() and [entry.name for entry in path.parent.iterdir()] == [names.MARKER]:
        marker.unlink()
        path.parent.rmdir()
    return 0
