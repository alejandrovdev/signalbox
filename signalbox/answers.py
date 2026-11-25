"""What the screens of `setup` and `init` answer, turned into a configuration, and a
configuration turned into the answers they start from: the only module that knows both.
Answers are a dict under the keys below. Nothing here asks, prints or writes.
"""

import dataclasses
import re
from pathlib import Path

from . import config

PREREQUISITES, INSTALL, FEATURES, NAME, GATE, CONFIRM = "prerequisites", "install", "features", "name", "gate", "confirm"
NAME_SHAPE = "a project's name has lowercase letters, digits and hyphens, at most 32"
FIRST = "recommended"  # the preset the features start from when nothing was chosen


def setup_answers(configuration):
    """What setup starts from: the default features saved, else the recommended ones, and
    installing nothing."""
    return {INSTALL: "no", FEATURES: configuration.defaults.features or config.PRESETS[FIRST]}


def with_setup(configuration, answers):
    return dataclasses.replace(configuration, defaults=config.Defaults(config.ordered(answers[FEATURES])))


def init_answers(configuration, existing, suggested, gate):
    if existing is not None:
        return {NAME: existing.name, FEATURES: existing.features, GATE: existing.gate or gate}
    return {NAME: suggested, FEATURES: configuration.defaults.features or config.PRESETS[FIRST], GATE: gate}


def needs_gate(answers):
    return "merge-lane" in (answers.get(FEATURES) or ())


def with_project(configuration, answers, folder, existing=None, base=None):
    """(the configuration with init's project, the project). A linked project is replaced
    where it is, keeping the keys init does not ask; a new one is added last."""
    features = config.ordered(answers[FEATURES])
    gate = answers.get(GATE) if needs_gate(answers) else (existing.gate if existing is not None else None)
    if existing is not None:
        project = dataclasses.replace(
            existing, name=answers[NAME], features=features, gate=gate, base=existing.base if base is None else base
        )
        projects = tuple(project if other is existing else other for other in configuration.projects)
        groups = tuple(
            dataclasses.replace(group, projects=tuple(project.name if name == existing.name else name for name in group.projects))
            for group in configuration.groups
        )
        configuration = dataclasses.replace(configuration, groups=groups)
    else:
        project = config.Project(answers[NAME], str(folder), features, base, gate)
        projects = configuration.projects + (project,)
    return dataclasses.replace(configuration, projects=projects), project


def without(configuration, project):
    """(the configuration without the project, what that did to its group, as lines). The
    project leaves its group; a group left with fewer than two projects goes."""
    groups, said = [], []
    for group in configuration.groups:
        if project.name not in group.projects:
            groups.append(group)
            continue
        kept = tuple(name for name in group.projects if name != project.name)
        if len(kept) < 2:
            said.append('Removed the group "%s": only "%s" was left in it.' % (group.name, '", "'.join(kept)))
            continue
        groups.append(dataclasses.replace(group, projects=kept))
        said.append('Removed "%s" from the group "%s", which keeps: %s.' % (project.name, group.name, ", ".join(kept)))
    projects = tuple(other for other in configuration.projects if other != project)
    return dataclasses.replace(configuration, projects=projects, groups=tuple(groups)), said


def name_problem(name, configuration, existing=None):
    if not config.PROJECT_NAME.fullmatch(name or ""):
        return NAME_SHAPE
    for other in configuration.projects:
        if other is not existing and other.name == name:
            return 'the project "%s" already has this name' % name
    return ""


def suggested_name(folder, configuration):
    """A folder's name made a project's: lowercase, anything else a hyphen, at most 32, and
    numbered when another project has it."""
    base = re.sub(r"[^a-z0-9]+", "-", Path(folder).name.lower()).strip("-")[:32].strip("-") or "project"
    name, number = base, 2
    while name_problem(name, configuration):
        tail = "-%d" % number
        name = base[: 32 - len(tail)].rstrip("-") + tail
        number += 1
    return name


def gate_problem(gate):
    return "" if (gate or "").strip() else "name the command the lane runs, such as make check"


def features_problem(features):
    return "; ".join(
        '"%s" needs "%s"' % (name, needed)
        for name in config.ordered(features)
        for needed in config.FEATURES[name].needs
        if needed not in features
    )


def installing(answers, installs):
    """What setup installs with Homebrew first, by name: `installs` when the answer is yes."""
    return list(installs) if installs and answers.get(INSTALL) == "yes" else []


def setup_summary(configuration, answers, settings, install_hooks, installs=()):
    """What setup is about to save, in lines; and what it installs with Homebrew first when
    the answer is yes (`installs`: what each is called)."""
    lines = ["Default features: %s" % ", ".join(config.ordered(answers[FEATURES]))]
    if install_hooks:
        lines.append("Claude Code hooks: installed in %s (they do nothing outside a linked repository)" % settings)
    else:
        lines.append("Claude Code hooks: not installed (--no-hooks)")
    if configuration.projects:
        lines.append("Linked projects: %s; they keep their features." % ", ".join(project.name for project in configuration.projects))
    else:
        lines.append("No project yet: link one from its folder with signalbox init.")
    if installing(answers, installs):
        lines.append("Install with Homebrew: %s" % ", ".join(installs))
    return lines


def init_summary(answers, folder, base, existing=None):
    lines = [
        "Project: %s" % answers[NAME],
        "Folder: %s" % (existing.path if existing is not None else folder),
        "Base: origin/%s" % base,
        "Features: %s" % ", ".join(config.ordered(answers[FEATURES])),
    ]
    if needs_gate(answers):
        lines.append("Gate: %s" % answers[GATE])
    if existing is not None and existing.name != answers[NAME]:
        lines.append('This repository is linked already, as "%s": this renames it to "%s".' % (existing.name, answers[NAME]))
    elif existing is not None:
        lines.append('It replaces the settings of the linked project "%s".' % existing.name)
    return lines


def shown_features(answers):
    return ", ".join(config.ordered(answers.get(FEATURES) or ())) or None


def setup_recap(answers, install_hooks, installs=()):
    """What setup's screens show of the answers so far, as (label, value) pairs; what it
    installs with Homebrew only when it offers to (`installs`: what each is called)."""
    pairs = (("features", shown_features(answers)), ("hooks", "install" if install_hooks else "not installed"))
    if installs:
        pairs += (("install", ", ".join(installing(answers, installs)) or "nothing"),)
    return pairs


def init_recap(answers, base):
    """What init's screens show of the answers so far, as (label, value) pairs; the gate only
    with the merge lane."""
    pairs = [("project", answers.get(NAME) or None), ("base", base), ("features", shown_features(answers))]
    if needs_gate(answers):
        pairs.append(("gate", answers.get(GATE) or None))
    return tuple(pairs)
