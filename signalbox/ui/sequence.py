"""A sequence of screens: the widgets a command asks, one after another.

    Sequence.start(screens, answers, given, recap)   the first screen to ask
    press(key)     a key: Enter goes on, Esc or left goes back, q or Ctrl-C quits
    answer(text)   a typed line: "<" goes back, "q" quits (except as text)

Each screen gives one answer, under its key. A screen whose answer was given beforehand
(on the command line) is never asked, and one whose widget is None for the answers so far
does not apply; going back skips both. A screen is made again each time it is reached, from
the answers so far, which hold its own earlier answer: what was chosen before is chosen
again. A confirmation answered no ends the sequence as quitting does: nothing is answered
for whoever asked. The sequence itself changes nothing. A recap, when given, says how the
answers so far are shown: a function of the answers to (label, value) pairs.
"""

import dataclasses
from dataclasses import dataclass, field
from typing import Callable, Optional

from .widgets import CTRL_C, ESC, LEFT, Confirm

RUNNING, DONE, QUIT = "running", "done", "quit"
BACK = "<"


@dataclass(frozen=True)
class Screen:
    key: str  # the answer it gives
    flag: str  # how that answer is given on the command line
    make: Callable  # the answers so far: its widget, or None when it does not apply


@dataclass(frozen=True)
class Sequence:
    screens: tuple
    answers: dict = field(default_factory=dict)
    given: frozenset = frozenset()  # the keys answered beforehand
    index: int = -1  # the screen asked
    widget: object = None
    state: str = RUNNING
    shown: tuple = ()  # the screens asked before this one, in order: where back goes
    recap: Optional[Callable] = None  # the answers so far: (label, value) pairs to show

    @classmethod
    def start(cls, screens, answers=None, given=(), recap=None):
        return cls(tuple(screens), dict(answers or {}), frozenset(given), recap=recap).forward(0)

    def asked(self, index, answers):
        """The widget of a screen, or None when it is not asked with these answers."""
        screen = self.screens[index]
        return None if screen.key in self.given else screen.make(answers)

    def forward(self, start, shown=None):
        shown = self.shown if shown is None else shown
        for index in range(start, len(self.screens)):
            widget = self.asked(index, self.answers)
            if widget is not None:
                return dataclasses.replace(self, index=index, widget=widget, shown=shown)
        return dataclasses.replace(self, index=-1, widget=None, shown=shown, state=DONE)

    def quit(self):
        return dataclasses.replace(self, state=QUIT)

    def back(self):
        if not self.shown:
            return self
        index = self.shown[-1]
        return dataclasses.replace(self, index=index, widget=self.screens[index].make(self.answers), shown=self.shown[:-1])

    def settled(self, widget):
        """Where a widget that changed leaves the sequence."""
        if not widget.done:
            return dataclasses.replace(self, widget=widget)
        if isinstance(widget, Confirm) and widget.value is False:
            return dataclasses.replace(self, widget=widget, state=QUIT)
        answers = dict(self.answers)
        answers[self.screens[self.index].key] = widget.value
        moved = dataclasses.replace(self, answers=answers, widget=widget)
        return moved.forward(self.index + 1, self.shown + (self.index,))

    def press(self, key):
        if self.state != RUNNING:
            return self
        if key == CTRL_C or (key == "q" and not self.widget.takes_text):
            return self.quit()
        if key in (ESC, LEFT):
            return self.back()
        return self.settled(self.widget.press(key))

    def answer(self, text):
        """A typed line, for the plain questions."""
        if self.state != RUNNING:
            return self
        word = text.strip()
        if word == BACK:
            return self.back()
        if word.lower() in ("q", "quit") and not self.widget.takes_text:
            return self.quit()
        return self.settled(self.widget.answer(text))

    @property
    def flag(self):
        """The flag that gives the answer of the screen asked."""
        return self.screens[self.index].flag if self.state == RUNNING else ""

    def step(self):
        """How far it is: (the number of the screen asked, how many there are so far)."""
        number = len(self.shown) + 1
        later = sum(1 for index in range(self.index + 1, len(self.screens)) if self.asked(index, self.answers) is not None)
        return number, number + later
