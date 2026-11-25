"""Where each Claude Code session stands, as its hooks last saw it: one small JSON file per
session in the sessions folder, so the sweep can keep a worktree a live session stands in
even between two of its commands, when no process has its current directory there.

    <session id>.json  {"cwd": the session's folder, "pid": its `claude` process,
                        "started": that process's start dates as `host.start_dates` gives
                        them, "updated": epoch seconds}

    record(host, data)     write or refresh the record of a hook's session; never raises
    recorded(host)         every record that reads well: [(session id, record, path)]
    running(host, record)  whether its process still runs; None when `ps` cannot be read

`record` runs on every hook call, so it reads no configuration and runs no git: it looks for
the session's process once, then reuses the pid and dates of the record, and writes only
when the folder changed or the record is more than a minute old.
"""

import json
import os
import re

from .. import names

SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")  # what a session id must be to name a file
FRESH = 60  # seconds a record of the same folder is left as it is


def record(host, data):
    """Record where the hook's session stands; nothing, silently, when it cannot."""
    try:
        write_record(host, data)
    except Exception:
        pass


def write_record(host, data):
    session, cwd = data.get("session_id"), data.get("cwd")
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session) or not isinstance(cwd, str) or not cwd:
        return
    path = names.sessions_dir(host.environ) / ("%s.json" % session)
    now = int(host.clock())
    known = read(path)
    if known is not None and not host.alive(known["pid"]):
        known = None  # a resumed session keeps its id under a new process: look for it again
    if known is not None:
        if known["cwd"] == cwd and 0 <= now - known["updated"] <= FRESH:
            return
        pid, started = known["pid"], known["started"]
    else:
        pid = host.session_process()
        started = host.start_dates(pid) if pid is not None else None
        if not started:
            return
    save(path, {"cwd": cwd, "pid": pid, "started": started, "updated": now})


def read(path):
    """A record, or None when the file is missing or is not one."""
    try:
        data = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    cwd, pid, started, updated = (data.get(key) for key in ("cwd", "pid", "started", "updated"))
    if not isinstance(cwd, str) or not cwd or type(pid) is not int or pid <= 1:
        return None
    if not isinstance(started, list) or not started or not all(isinstance(date, str) for date in started):
        return None
    if type(updated) not in (int, float):
        return None
    return data


def save(path, data):
    """Write a record in one step, so a reader never sees half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_name("%s.new-%d" % (path.name, os.getpid()))
    try:
        draft.write_text(json.dumps(data) + "\n", encoding="utf8")
        os.replace(draft, path)
    finally:
        if draft.exists():
            draft.unlink()


def recorded(host):
    folder = names.sessions_dir(host.environ)
    try:
        paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    except OSError:
        return []
    found = []
    for path in paths:
        session = path.name[: -len(".json")]
        data = read(path) if SESSION_ID.fullmatch(session) else None
        if data is not None:
            found.append((session, data, path))
    return found


def running(host, data):
    """Whether the record's process runs: its pid with one of the start dates recorded (a
    pid used again by another process does not count)."""
    dates = host.start_dates(data["pid"])
    if dates is None:
        return None
    return any(" ".join(date.split()) in dates for date in data["started"])
