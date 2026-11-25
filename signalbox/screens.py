"""The screens `setup` and `init` ask, each one a widget for one answer, built from what the
command found and the answers so far. Nothing is decided here: `answers` turns the answers
into a configuration."""

from . import answers, config
from .ui import Screen
from .ui.widgets import Checklist, Confirm, Item, Summary, TextField

FEATURE_FLAGS = "--preset or --feature"


def prerequisites(checks):
    body = tuple(check.line() for check in checks) + ("", "Nothing is installed from here: install what is missing yourself.")
    return Screen(answers.PREREQUISITES, "--yes", lambda given: Summary("Prerequisites", body))


def features(title):
    items = tuple(Item(name, name, feature.summary) for name, feature in config.FEATURES.items())
    needs = {name: feature.needs for name, feature in config.FEATURES.items()}
    return Screen(answers.FEATURES, FEATURE_FLAGS, lambda given: Checklist.of(title, items, needs, given.get(answers.FEATURES) or ()))


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
