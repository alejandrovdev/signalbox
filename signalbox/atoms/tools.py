"""Finding the programs signalbox runs (git, gh, claude) on any Mac.

A tool is looked for in PATH first and then in Homebrew's folder, which differs between
Apple Silicon and Intel. A background service starts with next to no PATH, so the
fallback is what finds a tool there.
"""

import os
import shutil

HOMEBREW_PREFIXES = ("/opt/homebrew", "/usr/local")  # Apple Silicon, Intel
SYSTEM_FOLDERS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


class ToolNotFound(Exception):
    """A program signalbox needs is not installed where it looks."""

    def __init__(self, name):
        self.name = name
        super().__init__("%s was not found in PATH or in Homebrew's folders" % name)


def homebrew_folders():
    return tuple(prefix + "/bin" for prefix in HOMEBREW_PREFIXES)


def search_path(existing=""):
    """The PATH of what signalbox starts: Homebrew, the system, then what was there."""
    folders = list(homebrew_folders() + SYSTEM_FOLDERS)
    folders += [folder for folder in existing.split(":") if folder and folder not in folders]
    return ":".join(folders)


def is_executable(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find(name, override="", path=None, executable=is_executable):
    """Where a tool is, or None. `override` is a place the user named; `path` replaces PATH."""
    if override:
        return override
    found = shutil.which(name, path=path)
    if found:
        return found
    for folder in homebrew_folders():
        candidate = folder + "/" + name
        if executable(candidate):
            return candidate
    return None


def need(name, override="", path=None, executable=is_executable):
    found = find(name, override, path, executable)
    if found is None:
        raise ToolNotFound(name)
    return found
