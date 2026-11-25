"""Every name signalbox creates, from its own identifier and a project's name.

Nothing here belongs to one Mac or to one repository.

    configuration  ~/Library/Application Support/<tool>/config.toml
    logs           ~/Library/Logs/<tool>/<project>/
    merge worktree ~/Library/Caches/<tool>/<project>/merge.noindex
    in a repository's git common directory: <tool>-lane.lock, <tool>-lane.json;
    in each worktree's git directory: <tool>-changes.json
    variables      <TOOL>_CONFIG, <TOOL>_STATE_DIR, <TOOL>_LOG_DIR, <TOOL>_CACHE_DIR,
                   <TOOL>_CLAUDE_SETTINGS, <TOOL>_CLAUDE_PROJECTS, and <TOOL>_GIT, <TOOL>_GH for a tool
"""

import os
import re
from pathlib import Path

TOOL = "signalbox"
LABEL = "io.github.alejandrovdev." + TOOL  # the address the tool is published at, reversed
MARKER = "." + TOOL  # the file in a folder the tool made
PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
LANE_LOCK = TOOL + "-lane.lock"  # in the git common directory: one lane run at a time
LANE_STATE = TOOL + "-lane.json"  # in the git common directory: the gate a lane runs
CHANGES_STATE = TOOL + "-changes.json"  # in a worktree's git directory: what `changes` saw
STATUS_CONTEXT = TOOL + "/merge-lane"  # the commit status the lane posts


def command(*words):
    """A command of the tool as a person types it."""
    return " ".join((TOOL,) + words)


def no_project():
    """What a command says when the configuration has no project."""
    return "No project is linked. Link one from its folder with: " + command("init")


def variable(name):
    """The environment variable that moves a folder or names a tool."""
    return "%s_%s" % (TOOL.upper(), name.upper())


def chosen(name, environ=None):
    """What the environment says for a variable; empty when it says nothing."""
    return (os.environ if environ is None else environ).get(variable(name), "")


def library(*parts):
    return Path.home().joinpath("Library", *parts)


def config_file(environ=None):
    return Path(chosen("config", environ) or library("Application Support", TOOL, "config.toml"))


def state_root(environ=None):
    return Path(chosen("state_dir", environ) or library("Application Support", TOOL))


def log_root(environ=None):
    return Path(chosen("log_dir", environ) or library("Logs", TOOL))


def cache_root(environ=None):
    return Path(chosen("cache_dir", environ) or library("Caches", TOOL))


def project_logs(project_name, environ=None):
    return log_root(environ) / project_name


def lane_log(project_name, environ=None):
    """One JSON line per lane run: what the metrics read."""
    return project_logs(project_name, environ) / "lane.jsonl"


def default_merge_worktree(project_name, environ=None):
    """The detached worktree the lane merges in; `.noindex` keeps it out of Spotlight."""
    return cache_root(environ) / project_name / "merge.noindex"


def claude_settings(environ=None):
    """The person's Claude Code settings, where the user-level hooks go."""
    return Path(chosen("claude_settings", environ) or Path.home() / ".claude" / "settings.json")


def claude_projects(environ=None):
    """Where Claude Code keeps each session's records, its agents' among them."""
    return Path(chosen("claude_projects", environ) or Path.home() / ".claude" / "projects")
