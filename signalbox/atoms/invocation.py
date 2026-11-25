"""How a tool is named in a command it hands to someone else: a tab, a session, a service.

The installed command when PATH has one. Otherwise the interpreter that is running, with
the tool as a module and the folder that holds its package, so that the command works from
any folder: this is how a copy that was never installed still names itself.
"""

import os
import shlex
import shutil
import sys
from pathlib import Path

ENV = "/usr/bin/env"


def own_package():
    """The folder of the package this file belongs to."""
    return Path(__file__).resolve().parent.parent


def words(name, path=None, python=None, package=None):
    """What runs a tool: (its words, the environment they need). `path` replaces PATH."""
    found = shutil.which(name, path=path)
    if found:
        return [os.path.abspath(found)], {}
    folder = Path(package) if package else own_package()
    return [python or sys.executable, "-m", name], {"PYTHONPATH": str(folder.parent)}


def shell(words, environment=None):
    """Those words as a shell reads them: one command, which may follow `exec`."""
    if environment:
        words = [ENV] + ["%s=%s" % pair for pair in sorted(environment.items())] + list(words)
    return " ".join(shlex.quote(word) for word in words)
