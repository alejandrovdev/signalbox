"""A grid of styled cells a whole screen is composed on, without curses.

    Style      a cell's colors (numbers of the 256-color palette, -1 for the terminal's
               own) and whether it is bold or dim
    Canvas     the grid: text, filled runs, boxes with rounded corners, and pixels drawn
               two to a cell with half blocks

Whatever is written past an edge is dropped, and the last column is never written: curses
fails on the last cell of the screen, so it stays empty everywhere, as in the plain layout.
"""

from dataclasses import dataclass

UPPER_HALF, LOWER_HALF = "▀", "▄"


@dataclass(frozen=True)
class Style:
    fg: int = -1
    bg: int = -1
    bold: bool = False
    dim: bool = False


BLANK = (" ", Style())


class Canvas:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.rows = [[BLANK] * width for _ in range(height)]

    def cell(self, row, column):
        return self.rows[row][column]

    def line(self, row):
        """A row's characters, for reading it back."""
        return "".join(character for character, _ in self.rows[row])

    def put(self, row, column, character, style):
        if 0 <= row < self.height and 0 <= column < self.width - 1:
            self.rows[row][column] = (character, style)

    def text(self, row, column, text, style, width=None):
        """Write text from a column, cut to `width` characters when given; the column after it."""
        if width is not None:
            text = text[: max(width, 0)]
        for offset, character in enumerate(text):
            self.put(row, column + offset, character, style)
        return column + len(text)

    def fill(self, row, column, width, style):
        self.text(row, column, " " * max(width, 0), style)

    def box(self, top, left, height, width, style, title="", title_style=None, note="", note_style=None):
        """A frame with rounded corners, a title on its top border and a note at the right
        of its bottom border."""
        if height < 2 or width < 4:
            return
        right, bottom = left + width - 1, top + height - 1
        for column in range(left + 1, right):
            self.put(top, column, "─", style)
            self.put(bottom, column, "─", style)
        for row in range(top + 1, bottom):
            self.put(row, left, "│", style)
            self.put(row, right, "│", style)
        for row, column, corner in ((top, left, "╭"), (top, right, "╮"), (bottom, left, "╰"), (bottom, right, "╯")):
            self.put(row, column, corner, style)
        if title:
            self.text(top, left + 2, " %s " % title, title_style or style, width=width - 4)
        if note:
            note = " %s " % note
            self.text(bottom, max(left + 2, right - 1 - len(note)), note, note_style or style, width=width - 4)

    def pixels(self, top, left, grid):
        """Draw rows of pixel colors two to a cell, the first row of each pair above. A None
        pixel lets the terminal's background through; two of them leave the cell as it is."""
        for pair in range(0, len(grid), 2):
            upper = grid[pair]
            lower = grid[pair + 1] if pair + 1 < len(grid) else [None] * len(upper)
            for offset, (above, below) in enumerate(zip(upper, lower)):
                row, column = top + pair // 2, left + offset
                if above is None and below is None:
                    continue
                if below is None:
                    self.put(row, column, UPPER_HALF, Style(above))
                elif above is None:
                    self.put(row, column, LOWER_HALF, Style(below))
                else:
                    self.put(row, column, UPPER_HALF, Style(above, below))
