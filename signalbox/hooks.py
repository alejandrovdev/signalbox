"""The Claude Code hooks signalbox installs in the person's settings: one per event, each
running `signalbox hook <event>`, which does nothing outside a linked project. Every other
setting and hook of the file is kept as it was.

    install(settings, command)   add the missing hooks (replacing ours with another command);
                                 the events added or changed
    remove(settings)             take ours out; the events they were on
    installed(settings)          the events ours are on
"""

import json
import os
import re
import stat
from pathlib import Path

# event as `signalbox hook` names it: (Claude Code's event, its matcher, the timeout in seconds)
EVENTS = {
    "session-start": ("SessionStart", None, 15),
    "user-prompt-submit": ("UserPromptSubmit", None, 15),
    "pre-tool-use": ("PreToolUse", "Bash", 10),
}
OURS = re.compile(r"signalbox'? hook (%s)( --shared)?$" % "|".join(EVENTS))


class SettingsError(Exception):
    """A settings file that cannot be read as a JSON object: it is left as it is."""


def read(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise SettingsError("%s cannot be read: %s" % (path, error))
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as error:
        raise SettingsError("%s is not JSON (%s): correct it, then run this again" % (path, error))
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise SettingsError("%s does not hold the settings Claude Code reads" % path)
    return data


def write(path, data):
    """Replace the file in one step: the file a link points to, with the mode it had."""
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_name(path.name + ".signalbox-new")
    draft.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    if path.exists():
        os.chmod(draft, stat.S_IMODE(path.stat().st_mode))
    os.replace(draft, path)


def ours(hook, event=None):
    """Whether a hook entry is one of signalbox's (for this event, when given)."""
    command = hook.get("command") if isinstance(hook, dict) else None
    found = OURS.search(command) if isinstance(command, str) else None
    return found is not None and (event is None or found.group(1) == event)


def entry(command, event, suffix=""):
    return {"type": "command", "command": "%s hook %s%s" % (command, event, suffix), "timeout": EVENTS[event][2]}


def without_ours(groups, event):
    """The event's matcher groups with our entries taken out; groups left empty go."""
    kept = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            kept.append(group)
            continue
        hooks = [hook for hook in group["hooks"] if not ours(hook, event)]
        if hooks or len(hooks) == len(group["hooks"]):
            kept.append(dict(group, hooks=hooks))
    return kept


def installed(path):
    try:
        data = read(path)
    except SettingsError:
        return []
    found = []
    for event, (name, _, _) in EVENTS.items():
        groups = data.get("hooks", {}).get(name)
        if any(ours(hook, event) for group in groups or [] if isinstance(group, dict) for hook in group.get("hooks") or []):
            found.append(event)
    return found


def install(path, command, suffix=""):
    """`suffix` follows each command: " --shared" in a repository's own settings."""
    data = read(path)
    hooks = dict(data.get("hooks", {}))
    changed = []
    for event, (name, matcher, _) in EVENTS.items():
        wanted = entry(command, event, suffix)
        groups = hooks.get(name) or []
        present = [
            hook for group in groups if isinstance(group, dict) for hook in group.get("hooks") or [] if ours(hook, event)
        ]
        if present == [wanted]:
            continue
        group = {"matcher": matcher, "hooks": [wanted]} if matcher else {"hooks": [wanted]}
        hooks[name] = without_ours(groups, event) + [group]
        changed.append(event)
    if changed:
        write(path, dict(data, hooks=hooks))
    return changed


def remove(path):
    data = read(path)
    if not data:
        return []
    hooks = dict(data.get("hooks", {}))
    removed = []
    for event, (name, _, _) in EVENTS.items():
        groups = hooks.get(name)
        if groups is None:
            continue
        kept = without_ours(groups, event)
        if kept != groups:
            removed.append(event)
            if kept:
                hooks[name] = kept
            else:
                del hooks[name]
    if removed:
        data = dict(data, hooks=hooks)
        if not hooks:
            del data["hooks"]  # only ours were there
        write(path, data)
    return removed
