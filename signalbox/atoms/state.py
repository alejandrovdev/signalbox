"""A state file: a JSON table replaced whole, with a lock beside it.

Two runs may overlap (a pass every few seconds, a command a person runs), so whoever
reads the state to change it holds the lock from the read to the write.

    claim(root, marker)     make a folder, with a file in it that says who made it
    claimed(root, marker)   whether a folder carries that file
    release(root, marker)   remove a folder that holds nothing but that file

A folder that was already there is never marked: what is in it is someone else's.
"""

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


def claim(root, marker):
    """Make a folder that is not there and mark it as made here; returns the folder. One
    that is there already is left as it is, marked or not."""
    root = Path(root)
    if not root.is_dir():
        try:
            root.mkdir(parents=True)
        except FileExistsError:  # another process made it just now, and marks it
            return root
        (root / marker).write_text("The files in this folder are kept by the tool that made it.\n", encoding="utf8")
    return root


def claimed(root, marker):
    return (Path(root) / marker).is_file()


def release(root, marker):
    """Remove a marked folder once its marker is all it holds; else both stay."""
    root = Path(root)
    try:
        if [entry.name for entry in root.iterdir()] == [marker]:
            (root / marker).unlink()
            root.rmdir()
    except OSError:
        pass


class Store:
    """`prepare` is called before the file's folder is made: what marks the folder above it."""

    def __init__(self, path, prepare=None):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")
        self.prepare = prepare

    def make_folder(self):
        if self.prepare is not None:
            self.prepare()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        """Hold the file's lock; waits for whoever holds it."""
        self.make_folder()
        with open(self.lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield self

    def load(self, default):
        """The table in the file; `default` when the file is absent or is not a table."""
        try:
            loaded = json.loads(self.path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return default
        return loaded if isinstance(loaded, dict) else default

    def save(self, table):
        self.make_folder()
        draft = self.path.with_suffix(".writing")
        draft.write_text(json.dumps(table, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf8")
        os.replace(draft, self.path)


def exclusive(path):
    """Take a lock for as long as the file returned stays open; None when another process
    holds it. Nobody waits: this is how only one of something runs at a time."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(path, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock
