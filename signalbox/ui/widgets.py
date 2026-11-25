"""The widgets of the terminal interface, as state machines: a key in, a new widget out.

    Choice      one item of a list
    Checklist   several items, each of which may need others: turning one on turns on
                what it needs, turning one off turns off what needs it, and each line says
                why when that happened
    TextField   one line of text, checked by a function when Enter is pressed
    Confirm     yes or no, under the lines it is about
    Summary     lines to read, and Enter to go on

A widget is frozen: `press(key)` returns another one. It is finished when `done` is true,
and then `value` is its answer. `rows()` is what to draw, as (text, whether the cursor is
on it); `plain_lines()`, `prompt` and `answer(text)` are the same widget asked with typed
lines instead of keys. Nothing here draws, reads a key or knows what the answers are for.

Keys are named: the constants below, or the character typed.
"""

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Optional

UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
ENTER, ESC, BACKSPACE, CTRL_C, SPACE = "enter", "esc", "backspace", "ctrl-c", " "


def typed(key):
    """Whether a key is a character to write, not a named key."""
    return len(key) == 1 and key.isprintable()


def padded(items):
    """The label of each item padded to the widest, then its note."""
    width = max((len(item.label) for item in items), default=0)
    return [("%-*s  %s" % (width, item.label, item.note)).rstrip() if item.note else item.label for item in items]


@dataclass(frozen=True)
class Item:
    value: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class Choice:
    title: str
    items: tuple
    current: Optional[str] = None  # the value chosen before: marked, and where the cursor starts
    cursor: int = 0
    error: str = ""
    done: bool = False
    value: Optional[str] = None

    takes_text: ClassVar[bool] = False
    hint: ClassVar[str] = "up/down: move   enter: choose   esc: back   q: quit"

    @classmethod
    def of(cls, title, items, current=None):
        values = [item.value for item in items]
        return cls(title, tuple(items), current, values.index(current) if current in values else 0)

    def labels(self):
        return [
            text + ("  [current]" if item.value == self.current else "") for item, text in zip(self.items, padded(self.items))
        ]

    def rows(self):
        return [(text, index == self.cursor) for index, text in enumerate(self.labels())]

    def chosen(self, index):
        return dataclasses.replace(self, cursor=index, done=True, value=self.items[index].value, error="")

    def press(self, key):
        if key in (UP, "k"):
            return dataclasses.replace(self, cursor=max(self.cursor - 1, 0), error="")
        if key in (DOWN, "j"):
            return dataclasses.replace(self, cursor=min(self.cursor + 1, len(self.items) - 1), error="")
        if key == ENTER and self.items:
            return self.chosen(self.cursor)
        return self

    def plain_lines(self):
        return ["%d) %s" % (number, text) for number, text in enumerate(self.labels(), 1)]

    @property
    def prompt(self):
        values = [item.value for item in self.items]
        if self.current in values:
            return "Number [%d]: " % (values.index(self.current) + 1)
        return "Number: "

    def answer(self, text):
        text = text.strip()
        values = [item.value for item in self.items]
        if not text and self.current in values:
            return self.chosen(values.index(self.current))
        if text.isdigit() and 1 <= int(text) <= len(self.items):
            return self.chosen(int(text) - 1)
        return dataclasses.replace(self, error="Type the number of one of them, 1 to %d." % len(self.items))


@dataclass(frozen=True)
class Checklist:
    title: str
    items: tuple
    needs: dict = field(default_factory=dict)  # an item's value: the values it needs
    checked: frozenset = frozenset()
    why: dict = field(default_factory=dict)  # an item's value: why it was turned on or off for the person
    cursor: int = 0
    error: str = ""
    done: bool = False
    value: Optional[tuple] = None

    takes_text: ClassVar[bool] = False
    hint: ClassVar[str] = "up/down: move   space: on/off   enter: accept   esc: back   q: quit"
    prompt: ClassVar[str] = "Numbers to turn on or off, or Enter to accept: "

    @classmethod
    def of(cls, title, items, needs, checked=()):
        return cls(title, tuple(items), dict(needs), frozenset(checked))

    def needed(self, value):
        """Everything an item needs, and what those need."""
        found, waiting = set(), list(self.needs.get(value, ()))
        while waiting:
            other = waiting.pop()
            if other not in found:
                found.add(other)
                waiting += self.needs.get(other, ())
        return found

    def toggled(self, value):
        """Turn an item on with what it needs, or off with what needs it. Each item moved
        for the person says which item made it move: the one that needs it, or the one it
        needs."""
        checked, why = set(self.checked), dict(self.why)
        if value in checked:
            gone = {value} | ({item.value for item in self.items if value in self.needed(item.value)} & checked)
            for other in gone - {value}:
                needed = next(name for name in self.needs.get(other, ()) if name in gone)
                why[other] = "off because it needs %s" % needed
            checked -= gone
        else:
            checked.add(value)
            waiting = [value]
            while waiting:
                needing = waiting.pop(0)
                for needed in self.needs.get(needing, ()):
                    if needed not in checked:
                        checked.add(needed)
                        why[needed] = "on because %s needs it" % needing
                        waiting.append(needed)
        why.pop(value, None)
        return dataclasses.replace(self, checked=frozenset(checked), why=why, error="")

    def labels(self):
        return [
            "[%s] %s%s" % ("x" if item.value in self.checked else " ", text, "  (%s)" % self.why[item.value] if item.value in self.why else "")
            for item, text in zip(self.items, padded(self.items))
        ]

    def rows(self):
        return [(text, index == self.cursor) for index, text in enumerate(self.labels())]

    def accepted(self):
        if not self.checked:
            return dataclasses.replace(self, error="Turn on at least one.")
        on = tuple(item.value for item in self.items if item.value in self.checked)
        return dataclasses.replace(self, done=True, value=on, error="")

    def press(self, key):
        if key in (UP, "k"):
            return dataclasses.replace(self, cursor=max(self.cursor - 1, 0))
        if key in (DOWN, "j"):
            return dataclasses.replace(self, cursor=min(self.cursor + 1, len(self.items) - 1))
        if key in (SPACE, "x") and self.items:
            return self.toggled(self.items[self.cursor].value)
        if key == ENTER:
            return self.accepted()
        return self

    def plain_lines(self):
        return ["%d) %s" % (number, text) for number, text in enumerate(self.labels(), 1)]

    def answer(self, text):
        words = [word for word in re.split(r"[\s,]+", text.strip()) if word]
        if not words:
            return self.accepted()
        if not all(word.isdigit() and 1 <= int(word) <= len(self.items) for word in words):
            return dataclasses.replace(self, error="Type numbers from 1 to %d, or nothing to accept." % len(self.items))
        widget = self
        for word in words:
            widget = widget.toggled(self.items[int(word) - 1].value)
        return widget


@dataclass(frozen=True)
class TextField:
    title: str
    value: str = ""
    check: Optional[Callable] = None  # what is wrong with a text, or "" when nothing is
    note: str = ""  # a line under the field
    error: str = ""
    done: bool = False

    takes_text: ClassVar[bool] = True
    hint: ClassVar[str] = "type, backspace erases   enter: accept   esc: back   ctrl-c: quit"
    LIMIT: ClassVar[int] = 200

    def rows(self):
        rows = [("> " + self.value + "_", True)]
        return rows + ([("", False), (self.note, False)] if self.note else [])

    def accepted(self):
        problem = self.check(self.value) if self.check else ""
        if problem:
            return dataclasses.replace(self, error=problem)
        return dataclasses.replace(self, done=True, error="")

    def press(self, key):
        if key == ENTER:
            return self.accepted()
        if key == BACKSPACE:
            return dataclasses.replace(self, value=self.value[:-1], error="")
        if typed(key) and len(self.value) < self.LIMIT:
            return dataclasses.replace(self, value=self.value + key, error="")
        return self

    def plain_lines(self):
        return [self.note] if self.note else []

    @property
    def prompt(self):
        return "Answer [%s]: " % self.value if self.value else "Answer: "

    def answer(self, text):
        text = text.strip()
        return dataclasses.replace(self, value=text or self.value).accepted()


@dataclass(frozen=True)
class Confirm:
    title: str
    body: tuple = ()
    question: str = "Go ahead?"
    error: str = ""
    done: bool = False
    value: Optional[bool] = None

    takes_text: ClassVar[bool] = False
    hint: ClassVar[str] = "y: yes   n: no   esc: back   q: quit"

    def rows(self):
        return [(line, False) for line in self.body] + [("", False), (self.question + " (y/n)", True)]

    def answered(self, yes):
        return dataclasses.replace(self, done=True, value=yes, error="")

    def press(self, key):
        if key in ("y", "Y"):
            return self.answered(True)
        if key in ("n", "N"):
            return self.answered(False)
        if key == ENTER:
            return dataclasses.replace(self, error="Press y for yes or n for no.")
        return self

    def plain_lines(self):
        return list(self.body)

    @property
    def prompt(self):
        return self.question + " (y/n): "

    def answer(self, text):
        text = text.strip().lower()
        if text in ("y", "yes"):
            return self.answered(True)
        if text in ("n", "no"):
            return self.answered(False)
        return dataclasses.replace(self, error="Type y for yes or n for no.")


@dataclass(frozen=True)
class Summary:
    title: str
    body: tuple = ()
    error: str = ""
    done: bool = False
    value: Optional[bool] = None

    takes_text: ClassVar[bool] = False
    hint: ClassVar[str] = "enter: continue   esc: back   q: quit"
    prompt: ClassVar[str] = "Press Enter to continue: "

    def rows(self):
        return [(line, False) for line in self.body]

    def press(self, key):
        return dataclasses.replace(self, done=True, value=True) if key == ENTER else self

    def plain_lines(self):
        return list(self.body)

    def answer(self, text):
        return dataclasses.replace(self, done=True, value=True)
