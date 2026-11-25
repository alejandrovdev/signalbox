"""The curses renderer: draws a sequence's step and feeds it the keys pressed.

    layout(widget, number, total, width, height)   the plain layout's lines, without curses
    palette(has_colors, colors, no_color)          how much color the terminal takes
    Painter                                        the curses attributes of lines and cells
    key_name(code)                                 a key curses read, as the widgets name it
    run(sequence)                                  ask a sequence on the terminal

In a terminal with 256 colors that writes UTF-8 and is large enough, every step is a whole
screen composed by `scene`: a status bar, the signal, the name in large pixels, panels and
keys, redrawn every tick so it moves while no key is pressed. Otherwise it is the plain
layout: a title, a one-line hint of the keys, the widget's rows and an error line, every
line cut to the width and the rows scrolled to keep the cursor in view, so any size of
terminal draws without failing. The plain layout's title and error are colored when the
terminal has colors, and keep bold, dim and reverse without them or with NO_COLOR set.
CannotStart says that curses could not take the terminal; whoever asked falls back to plain
questions.
"""

import codecs
import os
import time
from collections import OrderedDict

from .. import __version__
from . import scene, signal
from .canvas import Style
from .sequence import RUNNING
from .widgets import BACKSPACE, CTRL_C, DOWN, ENTER, ESC, LEFT, RIGHT, UP, typed

TITLE, HINT, ROW, FOCUS, ERROR = "title", "hint", "row", "focus", "error"
PLAIN, BASIC, FULL = "plain", "basic", "full"
GREEN_TEXT = 28  # the plain layout's title: a green that also reads on a light background
ON_GREEN = 16  # the text of the line under the cursor, on the accent's green
RED_TEXT = 160  # the error


class CannotStart(Exception):
    """curses could not take the terminal."""


def layout(widget, number, total, width, height):
    """What to draw, as (text, style) lines: at most `height` lines, each shorter than `width`
    (the last column is never written, where curses fails)."""
    room = max(width - 1, 0)
    rows = [(text, FOCUS if focused else ROW) for text, focused in widget.rows()]
    focus = next((index for index, (_, style) in enumerate(rows) if style == FOCUS), 0)
    title = ("%d/%d  %s" % (number, total, widget.title), TITLE)
    error = [("", ROW), (widget.error, ERROR)] if widget.error else []
    head = [title, (widget.hint, HINT), ("", ROW)]
    space = height - len(head) - len(error)
    if space < 1:  # too low for everything: the title, the line under the cursor, the error
        essential = [title] + rows[focus : focus + 1] + ([(widget.error, ERROR)] if widget.error else [])
        lines = essential[: max(height, 0)]
    else:
        start = max(0, min(focus - space // 2, len(rows) - space))
        lines = head + rows[start : start + space] + error
    return [(text[:room], style) for text, style in lines]


def palette(has_colors, colors, no_color):
    """How much color to use: FULL with 256 colors, BASIC with fewer, PLAIN without any or
    when NO_COLOR asks for none."""
    if no_color or not has_colors:
        return PLAIN
    return FULL if colors >= 256 else BASIC


def no_color(environ):
    """Whether NO_COLOR asks for no color: set and not empty, as no-color.org defines it."""
    return bool(environ.get("NO_COLOR"))


def draws_blocks(encoding):
    """Whether the terminal writes UTF-8, which the whole-screen layout's characters need."""
    try:
        return codecs.lookup(encoding).name == "utf-8"
    except (LookupError, TypeError):
        return False


def pair_limit(curses):
    """The most color pairs an attribute carries: `color_pair` encodes at most 255."""
    return min(getattr(curses, "COLOR_PAIRS", 0), 256) - 1


class Painter:
    """The curses attributes of each kind of plain line and of each styled cell, for a depth
    of color. `rich` says whether to compose whole screens. A color pair is made the first
    time it is asked for; when all `limit` of them are taken, the one unused for longest is
    made again, never one the frame being drawn uses (`begin` starts a frame). When the frame
    needs more, a cell takes the terminal's own colors instead of failing."""

    def __init__(self, curses, depth, rich, limit):
        self.curses = curses
        self.depth = depth
        self.rich = rich
        self.limit = limit
        self.pairs = OrderedDict()  # (foreground, background): pair number, oldest use first
        self.used = set()

    def begin(self):
        self.used = set()

    def pair(self, foreground, background):
        key = (foreground, background)
        if key in self.pairs:
            self.pairs.move_to_end(key)
        else:
            if len(self.pairs) < self.limit:
                number = len(self.pairs) + 1
            else:
                old = next((each for each, number in self.pairs.items() if number not in self.used), None)
                if old is None:
                    return self.curses.A_NORMAL
                number = self.pairs.pop(old)
            self.curses.init_pair(number, foreground, background)
            self.pairs[key] = number
        self.used.add(self.pairs[key])
        return self.curses.color_pair(self.pairs[key])

    def cell(self, style):
        """The attribute of a styled cell."""
        attribute = self.curses.A_NORMAL
        if (style.fg, style.bg) != (-1, -1):
            attribute = self.pair(style.fg, style.bg)
        if style.bold:
            attribute |= self.curses.A_BOLD
        if style.dim:
            attribute |= self.curses.A_DIM
        return attribute

    def style(self, style):
        """The attribute of a plain layout's line."""
        curses = self.curses
        if self.depth == FULL:
            if style == TITLE:
                return self.pair(GREEN_TEXT, -1) | curses.A_BOLD
            if style == FOCUS:
                return self.pair(ON_GREEN, scene.ACCENT)
            if style == ERROR:
                return self.pair(RED_TEXT, -1) | curses.A_BOLD
        elif self.depth == BASIC:
            if style == TITLE:
                return self.pair(curses.COLOR_GREEN, -1) | curses.A_BOLD
            if style == ERROR:
                return self.pair(curses.COLOR_RED, -1) | curses.A_BOLD
        plain = {TITLE: curses.A_BOLD, HINT: curses.A_DIM, ROW: curses.A_NORMAL, FOCUS: curses.A_REVERSE, ERROR: curses.A_BOLD}
        return plain[style]


def named_keys():
    import curses

    return {
        curses.KEY_UP: UP,
        curses.KEY_DOWN: DOWN,
        curses.KEY_LEFT: LEFT,
        curses.KEY_RIGHT: RIGHT,
        curses.KEY_ENTER: ENTER,
        curses.KEY_BACKSPACE: BACKSPACE,
        10: ENTER,
        13: ENTER,
        27: ESC,
        127: BACKSPACE,
        8: BACKSPACE,
        3: CTRL_C,
    }


def key_name(code):
    """A key as the widgets name it; None for a key they do not take. `code` is what
    curses read: a character, or a number (a key's code from 256 on, else a character's)."""
    named = named_keys()
    if isinstance(code, str):
        if len(code) != 1:
            return None
        if ord(code) < 256 and ord(code) in named:
            return named[ord(code)]
        return code if typed(code) else None
    if code in named:
        return named[code]
    if 0 <= code < 256 and typed(chr(code)):
        return chr(code)
    return None


def put(window, row, column, text, attribute):
    import curses

    try:
        window.addstr(row, column, text, attribute)
    except curses.error:
        pass  # a terminal that shrank while drawing: the next key or tick draws again


def paint(window, canvas, painter):
    """Write a canvas, a run of same-styled cells at a time; blank cells stay erased."""
    for row in range(canvas.height):
        cells = canvas.rows[row]
        column = 0
        while column < canvas.width:
            character, style = cells[column]
            end = column + 1
            while end < canvas.width and cells[end][1] == style:
                end += 1
            text = "".join(each for each, _ in cells[column:end])
            if style != Style() or text.strip():
                put(window, row, column, text, painter.cell(style))
            column = end


def draw(window, sequence, painter, tick):
    """The step as a whole screen when the painter composes them and it fits, else the plain
    layout."""
    height, width = window.getmaxyx()
    window.erase()
    painter.begin()
    canvas = scene.compose(scene.view_of(sequence, __version__), width, height, tick) if painter.rich else None
    if canvas is not None:
        paint(window, canvas, painter)
    else:
        number, total = sequence.step()
        for row, (text, style) in enumerate(layout(sequence.widget, number, total, width, height)):
            put(window, row, 0, text, painter.style(style))
    window.refresh()


def drive(window, sequence, painter, clock=time.monotonic):
    """Draw and read keys until the sequence ends. With whole screens, a read gives up after
    a tick, so they move while no key is pressed."""
    import curses

    try:
        curses.curs_set(0)
    except curses.error:
        pass  # a terminal that cannot hide its cursor
    try:
        curses.set_escdelay(25)  # Esc is a key here, not the start of another
    except (AttributeError, curses.error):
        pass
    window.keypad(True)
    if painter.rich:
        window.timeout(round(signal.TICK * 1000))
    try:
        while sequence.state == RUNNING:
            draw(window, sequence, painter, int(clock() / signal.TICK))
            try:
                code = window.get_wch()
            except curses.error:  # no key: a tick went by, or a signal interrupted the read
                continue
            name = key_name(code)
            if name is not None:
                sequence = sequence.press(name)
    except KeyboardInterrupt:
        sequence = sequence.quit()
    return sequence


def run(sequence):
    """Ask a sequence on the terminal; CannotStart when curses cannot take it."""
    try:
        import curses
    except ImportError as error:
        raise CannotStart(str(error))
    started = []

    def inside(window):
        started.append(True)
        depth = palette(curses.has_colors(), getattr(curses, "COLORS", 0), no_color(os.environ))
        try:
            # curses.wrapper starts colors, which would paint white on black; keep the
            # terminal's own colors, light or dark.
            curses.use_default_colors()
        except curses.error:
            depth = PLAIN  # colored text would sit on black
        rich = depth == FULL and draws_blocks(getattr(window, "encoding", None))
        return drive(window, sequence, Painter(curses, depth, rich, pair_limit(curses)))

    try:
        return curses.wrapper(inside)
    except curses.error as error:
        if started:
            raise
        raise CannotStart(str(error))
