"""The configuration file: the projects signalbox serves and the features of each.

One TOML file that `setup` and `init` write and a person may edit by hand:

    [defaults]
    preset = "recommended"

    [projects.shop]
    path = "~/Projects/shop"
    base = "main"                     # optional: else the repository's default branch
    preset = "recommended"            # or: features = ["board", "sweep"]
    gate = "pnpm verify"              # what the merge lane runs on the merged result
    gate_env = { TEST_WORKERS = "4" }
    gate_failed_step = 'FAILED at "(?P<step>[^"]+)"'
    feature_docs = ["docs/features/*.md"]
    build_output = [".turbo", "dist"]
    merge_method = "merge"

`[defaults]` is optional: what a new project starts from. Reading the file checks its shape
only: types, names, that each feature has the features it builds on, and that a project with
the merge lane names its gate. Whether a folder exists or a tool is installed is for
`doctor` to say.
"""

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import names
from .atoms import state


@dataclass(frozen=True)
class Feature:
    summary: str
    needs: tuple = ()  # other features
    tools: tuple = ()  # what must be installed


# In the order a feature may be turned on: each one comes after the features it needs.
FEATURES = {
    "board": Feature(
        "signalbox status: what each worktree changes, behind and ahead, conflicts, shared files",
        tools=("git",),
    ),
    "session-brief": Feature(
        "The board's summary at session start, and other worktrees' news before each prompt",
        needs=("board",),
        tools=("git", "claude"),
    ),
    "merge-lane": Feature(
        "signalbox merge <pr>: one at a time, gated on the merged result, merged if the head held",
        tools=("git", "gh"),
    ),
    "merge-guard": Feature(
        "Sessions that try gh pr merge are told to use the lane",
        needs=("merge-lane",),
        tools=("claude",),
    ),
    "sweep": Feature("Merged, clean worktrees and merged local branches go away by themselves", tools=("git",)),
    "metrics": Feature(
        "signalbox metrics: lead time, lane waits and failures, merges outside the lane",
        needs=("board",),
        tools=("git", "gh"),
    ),
}

PRESETS = {
    "minimal": ("board", "sweep"),
    "recommended": tuple(FEATURES),
}

TOOLS = ("git", "gh", "claude")
MERGE_METHODS = ("merge", "squash", "rebase")
# Ignored paths a new worktree builds again: the sweep removes a merged worktree that holds
# no other ignored file. A pattern with no slash matches a name anywhere; one with a slash,
# the path from the worktree's top.
BUILD_OUTPUT = (
    ".DS_Store",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".next",
    ".turbo",
    ".astro",
    "*.tsbuildinfo",
)

PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
BRANCH = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")
VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROJECT_KEYS = (
    "path",
    "base",
    "preset",
    "features",
    "gate",
    "gate_env",
    "gate_failed_step",
    "feature_docs",
    "build_output",
    "merge_method",
    "merge_worktree",
)
DEFAULTS_KEYS = ("preset", "features")


class ConfigError(Exception):
    """What is wrong with a configuration file, as (key, message) pairs in the file's order."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(("%s: %s" % pair) if pair[0] else pair[1] for pair in self.problems))


@dataclass(frozen=True)
class Project:
    name: str
    path: str  # as written, so that it is written back the same way
    features: tuple
    base: Optional[str] = None  # None: the repository's default branch
    gate: Optional[str] = None
    gate_env: tuple = ()  # (name, value) pairs, in the file's order
    gate_failed_step: Optional[str] = None
    feature_docs: tuple = ()
    build_output: tuple = BUILD_OUTPUT
    merge_method: str = MERGE_METHODS[0]
    merge_worktree: Optional[str] = None  # None: the default place

    @property
    def directory(self):
        return Path(os.path.expanduser(self.path))

    @property
    def gate_environment(self):
        return dict(self.gate_env)

    def has(self, feature):
        return feature in self.features


@dataclass(frozen=True)
class Defaults:
    """What a new project starts from; None where nothing was chosen."""

    features: Optional[tuple] = None


@dataclass(frozen=True)
class Config:
    projects: tuple = ()
    defaults: Defaults = Defaults()

    def project(self, name):
        return next((project for project in self.projects if project.name == name), None)


def default_path():
    return names.config_file()


def ordered(features):
    """The given features in the order of the feature table."""
    return tuple(name for name in FEATURES if name in features)


def tools(features):
    """What must be installed for these features, in a fixed order."""
    needed = {tool for name in features for tool in FEATURES[name].tools}
    return tuple(tool for tool in TOOLS if tool in needed)


def folder_key(path):
    return os.path.normpath(os.path.expanduser(path))


def chosen_features(key, table, problems):
    """The features a table asks for, or None when it states them wrongly."""
    has_preset, has_features = "preset" in table, "features" in table
    if has_preset and has_features:
        problems.append((key, "give either preset or features, not both"))
        return None
    if not has_preset and not has_features:
        problems.append((key, "give a preset or a list of features"))
        return None
    if has_preset:
        preset = table["preset"]
        if not isinstance(preset, str):
            problems.append((key + ".preset", "must be text"))
            return None
        if preset not in PRESETS:
            problems.append((key + ".preset", 'unknown preset "%s"; one of: %s' % (preset, ", ".join(PRESETS))))
            return None
        return PRESETS[preset]
    listed = table["features"]
    if not isinstance(listed, list) or not all(isinstance(name, str) for name in listed):
        problems.append((key + ".features", "must be a list of feature names"))
        return None
    if not listed:
        problems.append((key + ".features", "name at least one feature"))
        return None
    before = len(problems)
    for name in listed:
        if name not in FEATURES:
            problems.append((key + ".features", 'unknown feature "%s"' % name))
    for name in ordered(listed):
        for needed in FEATURES[name].needs:
            if needed not in listed:
                problems.append((key + ".features", '"%s" needs "%s"' % (name, needed)))
    return ordered(listed) if len(problems) == before else None


def text_value(key, table, name, problems, pattern=None, shape="", required=False):
    """A text value of a table, or None when it is absent or wrong."""
    if name not in table:
        if required:
            problems.append(("%s.%s" % (key, name), "missing"))
        return None
    value = table[name]
    if not isinstance(value, str):
        problems.append(("%s.%s" % (key, name), "must be text"))
        return None
    if not value or (pattern and not pattern.fullmatch(value)):
        problems.append(("%s.%s" % (key, name), shape or "must not be empty"))
        return None
    return value


def text_list(key, table, name, problems):
    """A list of text of a table, or None when it is absent or wrong."""
    if name not in table:
        return None
    value = table[name]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        problems.append(("%s.%s" % (key, name), "must be a list of text"))
        return None
    return tuple(value)


def environment(key, table, problems):
    """The gate's variables as (name, value) pairs, or () when absent or wrong."""
    if "gate_env" not in table:
        return ()
    value = table["gate_env"]
    key += ".gate_env"
    if not isinstance(value, dict):
        problems.append((key, "must be a table of variables"))
        return ()
    pairs = []
    for name, text in value.items():
        if not VARIABLE.fullmatch(name):
            problems.append(("%s.%s" % (key, name), "not a variable name"))
        elif not isinstance(text, str):
            problems.append(("%s.%s" % (key, name), "must be text"))
        else:
            pairs.append((name, text))
    return tuple(pairs)


def failed_step(key, table, problems):
    pattern = text_value(key, table, "gate_failed_step", problems)
    if pattern is None:
        return None
    key += ".gate_failed_step"
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        problems.append((key, "not a regular expression: %s" % error))
        return None
    if "step" not in compiled.groupindex:
        problems.append((key, 'needs a group named "step": (?P<step>...)'))
        return None
    return pattern


def project_from(name, table, problems):
    key = "projects.%s" % name
    if not PROJECT_NAME.fullmatch(name):
        problems.append((key, "a project's name has lowercase letters, digits and hyphens, at most 32"))
        return None
    if not isinstance(table, dict):
        problems.append((key, "must be a table"))
        return None
    before = len(problems)
    path = text_value(key, table, "path", problems, required=True)
    base = text_value(key, table, "base", problems, BRANCH, "a branch name, such as main")
    features = chosen_features(key, table, problems)
    gate = text_value(key, table, "gate", problems)
    if features is not None and "merge-lane" in features and gate is None and "gate" not in table:
        problems.append((key + ".gate", "missing: merge-lane runs it on each pull request"))
    gate_env = environment(key, table, problems)
    step = failed_step(key, table, problems)
    docs = text_list(key, table, "feature_docs", problems)
    output = text_list(key, table, "build_output", problems)
    method = table.get("merge_method", MERGE_METHODS[0])
    if method not in MERGE_METHODS:
        shown = method if isinstance(method, str) else repr(method)
        problems.append(
            (key + ".merge_method", 'unknown method "%s"; one of: %s' % (shown, ", ".join(MERGE_METHODS)))
        )
    worktree = text_value(key, table, "merge_worktree", problems)
    for other in table:
        if other not in PROJECT_KEYS:
            problems.append(("%s.%s" % (key, other), "unknown key"))
    if len(problems) != before:
        return None
    return Project(
        name,
        path,
        features,
        base,
        gate,
        gate_env,
        step,
        docs or (),
        BUILD_OUTPUT if output is None else output,
        method,
        worktree,
    )


def defaults_from(table, problems):
    key = "defaults"
    if not isinstance(table, dict):
        problems.append((key, "must be a table"))
        return Defaults()
    features = chosen_features(key, table, problems) if "preset" in table or "features" in table else None
    for other in table:
        if other not in DEFAULTS_KEYS:
            problems.append(("%s.%s" % (key, other), "unknown key"))
    return Defaults(features)


def parse(text):
    """The configuration a TOML text describes; ConfigError lists everything wrong with it."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError([("", str(error))])
    problems = []
    for key in data:
        if key not in ("defaults", "projects"):
            problems.append((key, "unknown key"))
    defaults = defaults_from(data.get("defaults", {}), problems)
    tables = data.get("projects", {})
    if not isinstance(tables, dict):
        problems.append(("projects", "must be a table of projects"))
        tables = {}
    projects, folders = [], {}
    for name, table in tables.items():
        project = project_from(name, table, problems)
        if project is None:
            continue
        folder = folder_key(project.path)
        if folder in folders:
            problems.append(("projects.%s.path" % name, 'the project "%s" already uses this folder' % folders[folder]))
        folders.setdefault(folder, name)
        projects.append(project)
    if problems:
        raise ConfigError(problems)
    return Config(tuple(projects), defaults)


def quoted(value):
    # A JSON string is a valid TOML basic string.
    return json.dumps(value, ensure_ascii=False)


def listed(values):
    return "[%s]" % ", ".join(quoted(value) for value in values)


def features_line(features):
    """A set of features as a table states it: by its preset's name, else as a list."""
    preset = next((name for name, chosen in PRESETS.items() if chosen == features), None)
    if preset:
        return "preset = %s" % quoted(preset)
    return "features = %s" % listed(features)


def project_lines(project):
    lines = ["[projects.%s]" % project.name, "path = %s" % quoted(project.path)]
    if project.base:
        lines.append("base = %s" % quoted(project.base))
    lines.append(features_line(project.features))
    if project.gate is not None:
        lines.append("gate = %s" % quoted(project.gate))
    if project.gate_env:
        pairs = ", ".join("%s = %s" % (name, quoted(value)) for name, value in project.gate_env)
        lines.append("gate_env = { %s }" % pairs)
    if project.gate_failed_step is not None:
        lines.append("gate_failed_step = %s" % quoted(project.gate_failed_step))
    if project.feature_docs:
        lines.append("feature_docs = %s" % listed(project.feature_docs))
    if project.build_output != BUILD_OUTPUT:
        lines.append("build_output = %s" % listed(project.build_output))
    if project.merge_method != MERGE_METHODS[0]:
        lines.append("merge_method = %s" % quoted(project.merge_method))
    if project.merge_worktree is not None:
        lines.append("merge_worktree = %s" % quoted(project.merge_worktree))
    return lines


def dump(config):
    """The TOML text of a configuration; `parse` reads back the same configuration."""
    lines = []
    if config.defaults.features is not None:
        lines += ["[defaults]", features_line(config.defaults.features)]
    for project in config.projects:
        if lines:
            lines.append("")
        lines += project_lines(project)
    return "\n".join(lines) + "\n" if lines else ""


def load(path=None):
    """The configuration in a file; an absent file is a configuration without projects."""
    path = Path(path) if path else default_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    return parse(text)


def save(config, path=None):
    """Write a configuration, replacing the file in one step. A folder made for it is marked
    as the tool's."""
    path = Path(path) if path else default_path()
    state.claim(path.parent, names.MARKER)
    draft = path.with_name(path.name + ".new")
    draft.write_text(dump(config), encoding="utf-8")
    os.replace(draft, path)
