"""Tests of the full-screen layout of the setup screens: the canvas it is drawn on, the pixel
name, and the screen of each kind of step at each size, composed without curses."""

import unittest

from signalbox.ui import scene, signal, wordmark
from signalbox.ui.canvas import LOWER_HALF, UPPER_HALF, Canvas, Style
from signalbox.ui.sequence import Screen, Sequence
from signalbox.ui.widgets import DOWN, ENTER, SPACE, Checklist, Choice, Confirm, Item, Summary, TextField

FEATURES = (
    Item("board", "board", "signalbox status: what each worktree changes"),
    Item("session-brief", "session-brief", "The board's summary at session start"),
    Item("merge-lane", "merge-lane", "Each pull request is verified on the merged result, one at a time"),
    Item("merge-guard", "merge-guard", "Refuses a direct merge of a pull request"),
)
NEEDS = {"session-brief": ("board",), "merge-guard": ("merge-lane", "session-brief")}
TOOLS = {"board": ("git",), "session-brief": ("git", "claude"), "merge-lane": ("git", "gh"), "merge-guard": ("claude",)}
STEPS = (("Project name", scene.DONE), ("Features", scene.NOW), ("Gate", scene.LATER), ("Summary", scene.LATER))
WIDE = (140, 40)
ROOMY = (90, 26)


def view(widget, answers=None):
    return scene.View(widget, 2, 4, STEPS, answers or {"project": "shop", "base": "main"}, "1.0.0")


def screen_text(canvas):
    return "\n".join(canvas.line(row) for row in range(canvas.height))


def row_with(canvas, text):
    return next(row for row in range(canvas.height) if text in canvas.line(row))


def features():
    return Checklist.of("Features", FEATURES, NEEDS, ("board",), facts={name: (("tools", ", ".join(tools)),) for name, tools in TOOLS.items()})


PICTURE_TOP = scene.margin(WIDE[1], scene.WIDE_TALL) + scene.NAME_ROW  # where the wide layout draws the signal


def picture(canvas):
    """The cells the signal covers in the wide layout."""
    return [canvas.cell(row, column) for row in range(PICTURE_TOP, PICTURE_TOP + signal.ROWS) for column in range(2, 2 + signal.COLUMNS)]


def drawn(aspect, tick):
    """The cells of the signal drawn alone, holding an aspect (None: changing)."""
    canvas = Canvas(*WIDE)
    canvas.pixels(PICTURE_TOP, 2, scene.see_through(signal.pixels(tick, aspect)))
    return picture(canvas)


class Drawing(unittest.TestCase):
    """The canvas: text, boxes and pixels on a grid of styled cells."""

    def test_text_is_cut_at_the_edge_and_never_reaches_the_last_column(self):
        canvas = Canvas(10, 2)

        end = canvas.text(0, 6, "signalbox", Style(41))

        self.assertEqual(canvas.line(0), "      sig ")
        self.assertEqual(canvas.cell(0, 6), ("s", Style(41)))
        self.assertEqual(end, 15)
        self.assertEqual(canvas.text(1, 0, "signalbox", Style(), width=4), 4)
        self.assertEqual(canvas.line(1), "sign      ")

    def test_a_box_has_rounded_corners_a_title_on_top_and_a_note_below(self):
        canvas = Canvas(30, 4)

        canvas.box(0, 0, 4, 20, Style(240), "Features", Style(84), "1 of 4", Style(245))

        self.assertEqual(canvas.line(0)[:20], "╭─ Features ───────╮")
        self.assertEqual(canvas.line(1)[:20], "│                  │")
        self.assertEqual(canvas.line(3)[:20], "╰───────── 1 of 4 ─╯")
        self.assertEqual(canvas.cell(0, 3), ("F", Style(84)))

    def test_pixels_go_two_to_a_cell_and_none_lets_the_background_through(self):
        canvas = Canvas(5, 1)

        canvas.pixels(0, 0, [[1, None, 3, None], [2, 4, None, None]])

        self.assertEqual(canvas.cell(0, 0), (UPPER_HALF, Style(1, 2)))
        self.assertEqual(canvas.cell(0, 1), (LOWER_HALF, Style(4)))
        self.assertEqual(canvas.cell(0, 2), (UPPER_HALF, Style(3)))
        self.assertEqual(canvas.cell(0, 3), (" ", Style()))


class Name(unittest.TestCase):
    """The name drawn in large pixels."""

    def test_spells_signalbox_five_rows_high_with_a_shadow(self):
        grid = wordmark.pixels("signalbox", 0)

        self.assertEqual(len(grid), 2 * wordmark.ROWS)
        self.assertEqual(len(grid[0]), wordmark.width("signalbox"))
        colors = {color for line in grid for color in line if color is not None}
        self.assertIn(wordmark.SHADOW, colors)
        self.assertGreater(len(colors), 4, "a gradient from top to bottom, and a shadow")

    def test_its_letters_are_a_green_ramp_over_a_dark_green_shadow(self):
        self.assertEqual(len(wordmark.GRADIENT), wordmark.GLYPH_ROWS)
        self.assertTrue(set(wordmark.GRADIENT) <= wordmark.GREENS)
        self.assertIn(wordmark.SHADOW, wordmark.GREENS)
        self.assertIn(scene.ACCENT, wordmark.GREENS, "the accents are the wordmark's green")

    def test_a_shine_crosses_it_now_and_then(self):
        frames = [wordmark.pixels("signalbox", tick) for tick in range(wordmark.SHINE_EVERY)]
        self.assertGreater(len({str(frame) for frame in frames}), 5)
        self.assertEqual(frames[0], wordmark.pixels("signalbox", wordmark.SHINE_EVERY))


class Layouts(unittest.TestCase):
    """Which layout a size gets, and what each one holds."""

    def test_a_wide_terminal_shows_the_signal_the_steps_the_name_and_the_panels(self):
        canvas = scene.compose(view(features()), *WIDE, 0)
        text = screen_text(canvas)

        self.assertIn("◆ signalbox", canvas.line(0))
        self.assertIn("2/4", canvas.line(0))
        self.assertIn("v1.0.0", canvas.line(0))
        self.assertEqual(scene.TAGLINE, "One train per track for your Claude Code sessions.")
        self.assertIn(scene.TAGLINE, text)
        self.assertIn("STEPS", text)
        self.assertIn("◉ Features", text)
        self.assertIn("○ Summary", text)
        self.assertIn("PROJECT", text)
        self.assertIn("shop", text)
        self.assertIn("╭─ ◆ Features", text)
        self.assertGreater(sum(character in (UPPER_HALF, LOWER_HALF) for character, _ in picture(canvas)), 100)

    def test_a_roomy_terminal_keeps_the_name_the_steps_and_the_panels_without_the_signal(self):
        canvas = scene.compose(view(features()), *ROOMY, 0)
        text = screen_text(canvas)

        self.assertIn("› ◉ Features ›", text)
        self.assertIn("╭─ ◆ Features", text)
        self.assertNotIn("STEPS", text)

    def test_a_small_terminal_gets_the_plain_layout(self):
        self.assertIsNone(scene.compose(view(features()), 79, 30, 0))
        self.assertIsNone(scene.compose(view(features()), 120, 23, 0))

    def test_the_panels_start_on_the_same_row_at_every_step(self):
        rows = set()
        summary = Confirm("Summary", ("Project: shop",), "Link this repository?")
        for widget in (features(), Choice.of("Base", (Item("main", "main", "origin's"),)), summary):
            canvas = scene.compose(view(widget), *WIDE, 0)
            rows.add(row_with(canvas, "╭─ ◆"))
        self.assertEqual(len(rows), 1)

    def test_fits_and_leaves_the_last_column_empty_at_every_size(self):
        widgets = (
            features(),
            Choice.of("Base", tuple(Item("b%d" % n, "Branch %d" % n, "remote " * 9) for n in range(40))),
            TextField("Project name", "shop", lambda text: "", "Lowercase letters, digits and hyphens, at most 32."),
            Confirm("Summary", tuple("Line %d: %s" % (n, "value " * 30) for n in range(30)), "Link this repository?"),
            Summary("Prerequisites", ("ok       git at /usr/bin/git", "missing  gh: brew install gh", "", "Nothing is installed.")),
        )
        for widget in widgets:
            for width in range(80, 220, 13):
                for height in range(24, 70, 5):
                    canvas = scene.compose(view(widget), width, height, 7)
                    self.assertEqual((canvas.width, canvas.height), (width, height))
                    self.assertTrue(all(canvas.cell(row, width - 1) == (" ", Style()) for row in range(height)), (widget.title, width, height))


class Colors(unittest.TestCase):
    """A railway signal's colors: green accents, amber warnings, red errors, gray text."""

    def test_the_accents_are_green(self):
        canvas = scene.compose(view(features()), *WIDE, 0)

        self.assertEqual(canvas.cell(0, 1), ("◆", scene.ACCENT_TEXT))
        self.assertEqual(scene.ACCENT_TEXT.fg, scene.ACCENT)
        self.assertEqual(scene.KEY.bg, scene.ACCENT)
        self.assertIn(scene.TITLE.fg, wordmark.GREENS)
        self.assertTrue(set(scene.PULSE) <= wordmark.GREENS)
        bar = canvas.line(0).index("━")
        self.assertEqual(canvas.cell(0, bar)[1], Style(scene.ACCENT))

    def test_warnings_are_amber_errors_red_and_borders_gray(self):
        self.assertEqual(scene.WARNING_TEXT.fg, scene.AMBER)
        self.assertEqual(scene.BAD_TEXT.fg, scene.RED)
        self.assertIn(scene.FRAME.fg, range(232, 256), "a gray of the palette's ramp")


class Picture(unittest.TestCase):
    """The signal beside the steps: it changes aspect, or holds one for a step."""

    def test_the_summary_holds_green(self):
        widget = Confirm("Summary", ("Project: shop",), "Link this repository?")

        for tick in (0, 40, 90):
            canvas = scene.compose(view(widget), *WIDE, tick)
            self.assertEqual(picture(canvas), drawn(signal.GREEN, tick), tick)

    def test_an_error_holds_red(self):
        widget = Checklist.of("Features", FEATURES, NEEDS, ()).press(ENTER)

        tick = next(tick for tick in range(signal.CYCLE) if signal.aspect(tick) == signal.GREEN)
        canvas = scene.compose(view(widget), *WIDE, tick)

        self.assertEqual(picture(canvas), drawn(signal.RED, tick))

    def test_the_other_steps_change_aspect_as_the_signal_does(self):
        for tick in (0, signal.CYCLE // 2, signal.CYCLE - 1):
            canvas = scene.compose(view(features()), *WIDE, tick)
            self.assertEqual(picture(canvas), drawn(None, tick), tick)


class Steps(unittest.TestCase):
    """What each kind of step puts in its panels."""

    def test_the_option_under_the_cursor_is_a_bar_and_its_details_follow_it(self):
        widget = features().press(DOWN)  # on session-brief

        canvas = scene.compose(view(widget), *WIDE, 0)

        row = row_with(canvas, "▸")
        self.assertIn("session-brief", canvas.line(row))
        column = canvas.line(row).index("session-brief")
        self.assertEqual(canvas.cell(row, column)[1].bg, scene.CURSOR_BG)
        text = screen_text(canvas)
        about = text[text.index("╭─ ◆ session-brief") :]
        self.assertIn("The board's summary at session start", about)
        self.assertRegex(about, r"NEEDS +board")
        self.assertRegex(about, r"NEEDED BY +merge-guard")
        self.assertRegex(about, r"TOOLS +git, claude")
        self.assertLess(about.index("NEEDED BY"), about.index("TOOLS"))

    def test_a_checklist_marks_what_is_on_and_counts_it(self):
        widget = features().press(DOWN).press(SPACE)  # session-brief on

        text = screen_text(scene.compose(view(widget), *WIDE, 0))

        self.assertIn("✓ board", text)
        self.assertIn("✓ session-brief", text)
        self.assertIn("○ merge-lane", text)
        self.assertIn("2 of 4 on", text)

    def test_a_choice_marks_the_one_chosen_before(self):
        items = (Item("main", "main", "origin's"), Item("trunk", "trunk", "local"))

        text = screen_text(scene.compose(view(Choice.of("Base", items, "trunk")), *WIDE, 0))

        self.assertIn("◉ trunk", text)
        self.assertIn("○ main", text)

    def test_a_long_list_scrolls_and_says_how_many_are_hidden(self):
        items = tuple(Item("t%d" % n, "item-%02d" % n, "") for n in range(60))
        widget = Choice.of("Long", items)
        for _ in range(30):
            widget = widget.press(DOWN)

        text = screen_text(scene.compose(view(widget), *ROOMY, 0))

        self.assertIn("item-30", text)
        self.assertNotIn("item-00", text)
        self.assertRegex(text, r"↑ \d+ more")
        self.assertRegex(text, r"↓ \d+ more")

    def test_a_text_field_shows_the_text_a_caret_and_its_note(self):
        widget = TextField("Project name", "shop", lambda text: "", "Lowercase letters, digits and hyphens, at most 32.")

        text = screen_text(scene.compose(view(widget), *WIDE, 0))

        self.assertIn("❯ shop▌", text)
        self.assertIn("Lowercase letters", text)

    def test_the_summary_shows_labels_and_values_and_asks_the_question(self):
        widget = Confirm("Summary", ("Project: shop", "Default features: board, sweep"), "Link this repository?")

        text = screen_text(scene.compose(view(widget), *WIDE, 0))

        self.assertRegex(text, r"PROJECT +shop")
        self.assertRegex(text, r"DEFAULT FEATURES +board, sweep")
        self.assertIn("Link this repository?", text)
        self.assertIn(" y  Yes", text)
        self.assertIn(" n  No", text)

    def test_prerequisites_get_a_mark_each(self):
        widget = Summary("Prerequisites", ("ok       git at /usr/bin/git", "missing  gh: brew install gh", "", "Nothing is installed."))

        text = screen_text(scene.compose(view(widget), *WIDE, 0))

        self.assertIn("✓ git at /usr/bin/git", text)
        self.assertIn("✗ missing  gh: brew install gh", text)

    def test_a_warning_stands_out_in_amber_and_long_check_lines_wrap(self):
        advice = "install the GitHub command line for the `merge-lane` feature with: brew install gh, or leave that feature off"
        warning = "Warning: signalbox can install what is missing with Homebrew on the next screen. " * 2
        widget = Summary("Prerequisites", ("missing  gh: " + advice, "", warning.strip()))

        canvas = scene.compose(view(widget), *ROOMY, 0)
        text = screen_text(canvas)

        self.assertIn("leave that feature off", text)
        self.assertNotIn("…", text)
        row = row_with(canvas, "⚠ Warning:")
        column = canvas.line(row).index("⚠")
        self.assertEqual(canvas.cell(row, column)[1], scene.WARNING_TEXT)
        self.assertEqual(canvas.cell(row + 1, column + 2)[1].fg, scene.AMBER, "the paragraph keeps its color")

    def test_the_keys_become_chips_at_the_bottom(self):
        canvas = scene.compose(view(features()), *WIDE, 0)

        last = canvas.line(WIDE[1] - 1)
        self.assertIn(" up/down  move", last)
        self.assertIn(" enter  accept", last)
        column = last.index("enter")
        self.assertEqual(canvas.cell(WIDE[1] - 1, column)[1].bg, scene.ACCENT)

    def test_an_error_shows_above_the_keys_in_red(self):
        widget = Checklist.of("Features", FEATURES, NEEDS, ()).press(ENTER)

        canvas = scene.compose(view(widget), *WIDE, 0)

        self.assertIn("✗ " + widget.error, canvas.line(WIDE[1] - 2))
        self.assertEqual(canvas.cell(WIDE[1] - 2, canvas.line(WIDE[1] - 2).index("✗"))[1].fg, scene.RED)


class Reading(unittest.TestCase):
    """The view of a sequence: its step, and the steps done, asked now and still to come."""

    def screens(self):
        return [
            Screen("name", "--name", lambda given: TextField("Project name", given.get("name") or "", lambda text: "")),
            Screen("base", "--base", lambda given: Choice.of("Base", (Item("main", "main"),))),
            Screen("confirm", "--yes", lambda given: Confirm("Summary", (), "Link?")),
        ]

    def test_lists_the_steps_done_now_and_later_with_the_answers_so_far(self):
        sequence = Sequence.start(self.screens(), {"name": "shop"}).press(ENTER)

        seen = scene.view_of(sequence, "1.0.0")

        self.assertEqual(seen.steps, (("Project name", scene.DONE), ("Base", scene.NOW), ("Summary", scene.LATER)))
        self.assertEqual((seen.number, seen.total), (2, 3))
        self.assertEqual(seen.answers, {"name": "shop"})
        self.assertEqual(seen.version, "1.0.0")

    def test_a_recap_says_what_the_answers_so_far_show(self):
        def recap(answers):
            return (("project", answers.get("name")), ("base", "main"), ("gate", None))

        sequence = Sequence.start(self.screens(), {"name": "shop"}, recap=recap).press(ENTER)

        seen = scene.view_of(sequence)
        text = screen_text(scene.compose(seen, *WIDE, 0))

        self.assertEqual(seen.answers, {"project": "shop", "base": "main", "gate": None})
        self.assertRegex(text, r"PROJECT +shop")
        self.assertRegex(text, r"BASE +main")
        self.assertNotIn("GATE", text, "an answer with no value is left out")
        self.assertEqual(sequence.press(DOWN).recap, recap, "the sequence keeps it from step to step")


if __name__ == "__main__":
    unittest.main()
