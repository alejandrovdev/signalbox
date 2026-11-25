"""Tests of the railway signal drawn beside the setup screens: the picture at each moment,
computed without curses."""

import unittest

from signalbox.ui import signal


def lamp_color(grid, aspect):
    x, y = signal.LAMPS[aspect]
    return grid[y][x]


def glowing(grid):
    """How many pixels a lamp's light reaches: those of a lit level of a glow."""
    lit = {color for glow in signal.GLOW.values() for color in glow[1:]}
    return sum(1 for row in grid for color in row if color in lit)


class Picture(unittest.TestCase):
    def test_is_a_grid_of_palette_colors_two_pixels_to_each_cell_it_covers(self):
        self.assertEqual((signal.WIDTH, signal.HEIGHT), (signal.COLUMNS, 2 * signal.ROWS))
        for held in (None,) + signal.ASPECTS:
            for tick in range(0, signal.CYCLE, 7):
                grid = signal.pixels(tick, held)
                self.assertEqual(len(grid), signal.HEIGHT)
                for row in grid:
                    self.assertEqual(len(row), signal.WIDTH)
                    self.assertTrue(all(0 <= color < 256 for color in row), (held, tick))

    def test_the_same_moment_draws_the_same_picture(self):
        self.assertEqual(signal.pixels(41), signal.pixels(41))
        self.assertEqual(signal.pixels(41, signal.GREEN), signal.pixels(41, signal.GREEN))

    def test_the_lit_lamp_glows_and_its_glow_breathes(self):
        frames = [signal.pixels(tick, signal.RED) for tick in range(signal.BREATH)]

        self.assertGreater(len({str(frame) for frame in frames}), 3)
        self.assertTrue(all(glowing(frame) > 60 for frame in frames))

    def test_a_picture_needs_few_color_pairs(self):
        # A curses without extended colors carries at most 255 pairs; the rest of the screen
        # takes a few dozen.
        for held in (None,) + signal.ASPECTS:
            for tick in range(0, 2 * signal.CYCLE, 9):
                grid = signal.pixels(tick, held)
                pairs = {(grid[row][column], grid[row + 1][column]) for row in range(0, signal.HEIGHT, 2) for column in range(signal.WIDTH)}
                self.assertLessEqual(len(pairs), 150, (held, tick))


class Aspects(unittest.TestCase):
    def test_the_lamps_are_red_on_top_amber_then_green_below(self):
        self.assertEqual(signal.ASPECTS, (signal.RED, signal.AMBER, signal.GREEN))
        rows = [signal.LAMPS[aspect][1] for aspect in signal.ASPECTS]
        self.assertEqual(rows, sorted(rows))
        self.assertEqual(len({signal.LAMPS[aspect][0] for aspect in signal.ASPECTS}), 1, "one above the other")

    def test_changes_aspect_slowly_red_amber_green_as_a_signal_does(self):
        shown = []
        for tick in range(2 * signal.CYCLE):
            aspect = signal.aspect(tick)
            if not shown or shown[-1] != aspect:
                shown.append(aspect)

        self.assertEqual(shown[:4], [signal.RED, signal.AMBER, signal.GREEN, signal.RED])
        held = [sum(1 for tick in range(signal.CYCLE) if signal.aspect(tick) == aspect) for aspect in signal.ASPECTS]
        self.assertTrue(all(ticks * signal.TICK >= 2 for ticks in held), "each aspect shows for seconds")
        self.assertEqual(signal.aspect(5), signal.aspect(5 + signal.CYCLE))

    def test_only_the_lamp_of_the_aspect_is_lit_and_the_others_stay_dim(self):
        for tick in range(0, signal.CYCLE, 3):
            aspect = signal.aspect(tick)
            lights = signal.lights(tick)
            if max(lights.values()) < 1:
                continue  # changing aspect: one lamp fades as the next one warms
            grid = signal.pixels(tick)
            self.assertEqual(lights[aspect], 1, tick)
            self.assertIn(lamp_color(grid, aspect), signal.LIT[aspect], tick)
            for other in signal.ASPECTS:
                if other != aspect:
                    self.assertEqual(lights[other], 0, tick)
                    self.assertEqual(lamp_color(grid, other), signal.DIM[other], (tick, other))

    def test_a_change_of_aspect_fades_one_lamp_out_as_the_next_one_warms(self):
        changes = [tick for tick in range(1, signal.CYCLE) if signal.aspect(tick) != signal.aspect(tick - 1)]
        self.assertEqual(len(changes), 2)
        warming = signal.lights(changes[0])

        self.assertGreater(warming[signal.AMBER], 0)
        self.assertLess(warming[signal.AMBER], 1)
        self.assertGreater(warming[signal.RED], 0)
        self.assertLess(warming[signal.RED], 1)

    def test_a_held_aspect_stays_lit_at_every_moment(self):
        for held in signal.ASPECTS:
            for tick in range(0, 2 * signal.CYCLE, 5):
                grid = signal.pixels(tick, held)
                self.assertEqual(signal.lights(tick, held), {aspect: 1 if aspect == held else 0 for aspect in signal.ASPECTS})
                self.assertIn(lamp_color(grid, held), signal.LIT[held], (held, tick))
                for other in signal.ASPECTS:
                    if other != held:
                        self.assertEqual(lamp_color(grid, other), signal.DIM[other], (held, tick, other))


if __name__ == "__main__":
    unittest.main()
