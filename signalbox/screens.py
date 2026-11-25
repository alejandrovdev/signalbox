"""The screens `setup` and `init` ask, each one a widget for one answer, built from what the
command found and the answers so far. Nothing is decided here: `answers` turns the answers
into a configuration."""

from . import answers, config, doctor, names
from .ui import Screen
from .ui.widgets import Checklist, Choice, Confirm, Item, Summary, TextField

FEATURE_FLAGS = "--preset or --feature"

NOTHING_INSTALLED = "Nothing is installed from here: install what is missing yourself."
GET_HOMEBREW = "Homebrew was not found, so nothing is installed from here: get it from https://brew.sh, or install what is missing yourself."
WARNING = (
    "Warning: %s can install what is missing with Homebrew on the next screen. It downloads software from the "
    "internet, a cask may ask for your password, and gh and Claude Code still need you to sign in "
    "(`gh auth login`, `claude`) once setup ends." % names.TOOL
)
INSTALL_TITLE = "Install what is missing"


def prerequisites(checks, installs=(), homebrew=True):
    """The checks, one line each; then a warning when the next screen offers to install
    `installs` ((name, check) pairs Homebrew installs), how to get Homebrew when it is what
    is missing for that, else that nothing is installed."""
    if installs:
        last = WARNING
    elif not homebrew and any(check.failed and check.install for check in checks):
        last = GET_HOMEBREW
    else:
        last = NOTHING_INSTALLED
    body = tuple(check.line() for check in checks) + ("", last)
    return Screen(answers.PREREQUISITES, "--yes", lambda given: Summary("Prerequisites", body))


def install(installs):
    """Whether to install what is missing with Homebrew (`installs`: (name, check) pairs);
    asked only when there is something. It starts on Skip, and installs nothing itself."""
    items = (
        Item("yes", "Install with Homebrew", "; ".join(doctor.brew_command(check) for _, check in installs)),
        Item("no", "Skip", "install them yourself; `%s` lists them" % names.command("doctor")),
    )
    values = [item.value for item in items]

    def make(given):
        if not installs:
            return None
        chosen = given.get(answers.INSTALL)
        return Choice(INSTALL_TITLE, items, cursor=values.index(chosen) if chosen in values else values.index("no"))

    return Screen(answers.INSTALL, "--install-missing", make)


def features(title):
    items = tuple(Item(name, name, feature.summary) for name, feature in config.FEATURES.items())
    needs = {name: feature.needs for name, feature in config.FEATURES.items()}
    facts = {name: (("tools", ", ".join(feature.tools) or "nothing"),) for name, feature in config.FEATURES.items()}
    return Screen(answers.FEATURES, FEATURE_FLAGS, lambda given: Checklist.of(title, items, needs, given.get(answers.FEATURES) or (), facts))


def name(check):
    note = "Lowercase letters, digits and hyphens, at most 32."
    return Screen(answers.NAME, "--name", lambda given: TextField("Project name", given.get(answers.NAME) or "", check, note))


def gate():
    """The command the merge lane runs on each merged result; asked only with the merge lane."""
    note = "The command the merge lane runs in the merge worktree, such as make check; it passes with exit 0."

    def make(given):
        if not answers.needs_gate(given):
            return None
        return TextField("Gate", given.get(answers.GATE) or "", answers.gate_problem, note)

    return Screen(answers.GATE, "--gate", make)


def confirm(question, describe):
    def shown(given):
        asked = question(given) if callable(question) else question
        return Confirm("Summary", tuple(describe(given)), asked)

    return Screen(answers.CONFIRM, "--yes", shown)
