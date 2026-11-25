"""The terminal interface every screen of the tool shares.

    widgets    Choice, Checklist, TextField, Confirm, Summary: state machines driven by keys
    sequence   the screens of a command one after another, with back, next and quit
    screen     the curses renderer
    scene      a step as a whole screen, composed on a `canvas`: the name in large pixels
               (`wordmark`), the railway `signal`, the steps, panels and keys
    plain      numbered questions read with `input`, when the terminal cannot draw

`Console` asks a sequence on the terminal when both input and output are one and curses
starts, and with plain questions otherwise. Asking changes nothing: whoever asked reads
the answers and acts on them. Standard library only.
"""

import sys

from . import plain, screen
from .plain import Missing
from .sequence import DONE, QUIT, RUNNING, Screen, Sequence

__all__ = ["Console", "Missing", "Screen", "Sequence", "DONE", "QUIT", "RUNNING"]


class Console:
    """Where a sequence is asked: the terminal, drawn, or plain questions."""

    def __init__(self, stdin=None, stdout=None, read=input, write=print, tty=None):
        self.stdin = stdin
        self.stdout = stdout
        self.read = read
        self.write = write
        self.tty = tty  # None: found out from the streams

    @property
    def terminal(self):
        """Whether a person is at a terminal: input and output both are one."""
        if self.tty is not None:
            return self.tty
        streams = (self.stdin or sys.stdin, self.stdout or sys.stdout)
        try:
            return all(stream.isatty() for stream in streams)
        except (AttributeError, ValueError):  # no stream, or a closed one
            return False

    def ask(self, sequence):
        """The sequence once it ended, done or quit. Missing when the input ended first."""
        if sequence.state != RUNNING:
            return sequence
        if self.terminal:
            try:
                return screen.run(sequence)
            except screen.CannotStart:
                pass
        return plain.run(sequence, self.read, self.write)
