"""Tests of the configuration file: what it accepts, what it refuses and how it is written."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalbox import config

ONE = """
[projects.shop]
path = "~/Projects/shop"
preset = "recommended"
gate = "make check"
"""

FULL = """
[defaults]
preset = "minimal"

[projects.shop]
path = "~/Projects/shop"
base = "trunk"
preset = "recommended"
gate = "pnpm verify"
gate_env = { TEST_WORKERS = "4", CI = "1" }
gate_failed_step = 'FAILED at "(?P<step>[^"]+)"'
feature_docs = ["docs/features/*.md"]
build_output = [".turbo", "dist"]
merge_method = "squash"
merge_worktree = "~/scratch/shop-merge"
legacy_locks = ["old-lane.lock"]
gate_files = ["ci/*.yml"]

[projects.blog]
path = "/srv/blog"
features = ["sweep", "board"]
"""


def problems(text):
    with unittest.TestCase().assertRaises(config.ConfigError) as caught:
        config.parse(text)
    return caught.exception.problems


class Parse(unittest.TestCase):
    def test_reads_one_project_with_a_preset(self):
        shop = config.parse(ONE).projects[0]

        self.assertEqual(shop.name, "shop")
        self.assertEqual(shop.path, "~/Projects/shop")
        self.assertEqual(shop.directory, Path.home() / "Projects" / "shop")
        self.assertEqual(shop.features, tuple(config.FEATURES))
        self.assertEqual(shop.gate, "make check")
        self.assertIsNone(shop.base, "the base branch is read from the repository when not given")
        self.assertEqual(shop.merge_method, "merge")
        self.assertEqual(shop.feature_docs, ())
        self.assertEqual(shop.build_output, config.BUILD_OUTPUT, "the default build output")
        self.assertEqual(shop.gate_environment, {})
        self.assertIsNone(shop.merge_worktree)

    def test_reads_every_key_and_several_projects(self):
        parsed = config.parse(FULL)

        self.assertEqual([project.name for project in parsed.projects], ["shop", "blog"])
        shop, blog = parsed.projects
        self.assertEqual(shop.base, "trunk")
        self.assertEqual(shop.gate_environment, {"TEST_WORKERS": "4", "CI": "1"})
        self.assertEqual(shop.gate_failed_step, 'FAILED at "(?P<step>[^"]+)"')
        self.assertEqual(shop.feature_docs, ("docs/features/*.md",))
        self.assertEqual(shop.build_output, (".turbo", "dist"))
        self.assertEqual(shop.merge_method, "squash")
        self.assertEqual(shop.merge_worktree, "~/scratch/shop-merge")
        self.assertEqual(shop.legacy_locks, ("old-lane.lock",))
        self.assertEqual(shop.gate_files, ("ci/*.yml",))
        self.assertEqual(blog.features, ("board", "sweep"), "features keep the order of the feature table")
        self.assertTrue(blog.has("sweep"))
        self.assertFalse(blog.has("merge-lane"))
        self.assertEqual(parsed.defaults.features, config.PRESETS["minimal"])
        self.assertEqual(parsed.project("blog"), blog)
        self.assertIsNone(parsed.project("nope"))

    def test_a_file_without_projects_is_valid_and_empty(self):
        self.assertEqual(config.parse("").projects, ())
        self.assertIsNone(config.parse("").defaults.features)

    def test_the_presets(self):
        self.assertEqual(config.PRESETS["minimal"], ("board", "sweep"))
        self.assertEqual(config.PRESETS["recommended"], tuple(config.FEATURES))
        self.assertEqual(
            tuple(config.FEATURES), ("board", "session-brief", "merge-lane", "merge-guard", "sweep", "metrics")
        )

    def test_what_each_feature_needs(self):
        self.assertEqual(config.FEATURES["session-brief"].needs, ("board",))
        self.assertEqual(config.FEATURES["merge-guard"].needs, ("merge-lane",))
        self.assertEqual(config.FEATURES["metrics"].needs, ("board",))
        self.assertEqual(config.tools(("board", "sweep")), ("git",))
        self.assertEqual(config.tools(tuple(config.FEATURES)), ("git", "gh", "claude"))


class Refuse(unittest.TestCase):
    def test_a_file_that_is_no_toml(self):
        found = problems("[projects.shop\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "")

    def test_a_project_without_path_or_features(self):
        found = problems("[projects.shop]\n")
        self.assertIn(("projects.shop.path", "missing"), found)
        self.assertIn(("projects.shop", "give a preset or a list of features"), found)

    def test_both_a_preset_and_features(self):
        found = problems('[projects.shop]\npath = "/s"\npreset = "minimal"\nfeatures = ["board"]\n')
        self.assertIn(("projects.shop", "give either preset or features, not both"), found)

    def test_an_unknown_preset_or_feature(self):
        self.assertIn(
            ("projects.shop.preset", 'unknown preset "all"; one of: minimal, recommended'),
            problems('[projects.shop]\npath = "/s"\npreset = "all"\n'),
        )
        self.assertIn(
            ("projects.shop.features", 'unknown feature "radar"'),
            problems('[projects.shop]\npath = "/s"\nfeatures = ["radar"]\n'),
        )

    def test_a_feature_without_what_it_needs(self):
        self.assertIn(
            ("projects.shop.features", '"session-brief" needs "board"'),
            problems('[projects.shop]\npath = "/s"\nfeatures = ["session-brief"]\n'),
        )

    def test_the_merge_lane_needs_a_gate(self):
        self.assertIn(
            ("projects.shop.gate", "missing: merge-lane runs it on each pull request"),
            problems('[projects.shop]\npath = "/s"\nfeatures = ["merge-lane"]\n'),
        )

    def test_values_of_the_wrong_shape(self):
        text = """
[projects.shop]
path = "/s"
features = ["board"]
base = "-x"
gate_env = { "1BAD" = "x", GOOD = 3 }
gate_failed_step = "no group"
feature_docs = "docs/*.md"
build_output = [1]
merge_method = "octopus"
colour = "blue"
"""
        found = problems(text)
        self.assertIn(("projects.shop.base", "a branch name, such as main"), found)
        self.assertIn(("projects.shop.gate_env.1BAD", "not a variable name"), found)
        self.assertIn(("projects.shop.gate_env.GOOD", "must be text"), found)
        self.assertIn(("projects.shop.gate_failed_step", 'needs a group named "step": (?P<step>...)'), found)
        self.assertIn(("projects.shop.feature_docs", "must be a list of text"), found)
        self.assertIn(("projects.shop.build_output", "must be a list of text"), found)
        self.assertIn(("projects.shop.merge_method", 'unknown method "octopus"; one of: merge, squash, rebase'), found)
        self.assertIn(("projects.shop.colour", "unknown key"), found)

    def test_a_regular_expression_that_does_not_compile(self):
        found = problems('[projects.shop]\npath = "/s"\nfeatures = ["board"]\ngate_failed_step = "(?P<step>"\n')
        self.assertEqual(found[0][0], "projects.shop.gate_failed_step")
        self.assertTrue(found[0][1].startswith("not a regular expression: "))

    def test_a_bad_project_name(self):
        self.assertIn(
            ("projects.Shop", "a project's name has lowercase letters, digits and hyphens, at most 32"),
            problems('[projects.Shop]\npath = "/s"\npreset = "minimal"\n'),
        )

    def test_two_projects_in_one_folder(self):
        text = '[projects.a]\npath = "/s"\npreset = "minimal"\n\n[projects.b]\npath = "/s/"\npreset = "minimal"\n'
        self.assertIn(("projects.b.path", 'the project "a" already uses this folder'), problems(text))

    def test_unknown_keys_at_the_top_and_in_defaults(self):
        found = problems('colour = "blue"\n[defaults]\ngate = "x"\n')
        self.assertIn(("colour", "unknown key"), found)
        self.assertIn(("defaults.gate", "unknown key"), found)


GROUPED = """
[projects.api]
path = "/w/api"
preset = "minimal"

[projects.web]
path = "/w/web"
preset = "minimal"

[projects.shop]
path = "/w/shop"
preset = "minimal"

[groups.platform]
projects = ["api", "web", "shop"]
"""


class Groups(unittest.TestCase):
    def test_reads_a_group_in_its_order_and_finds_a_projects_group(self):
        parsed = config.parse(GROUPED)
        self.assertEqual(parsed.groups, (config.Group("platform", ("api", "web", "shop")),))
        self.assertEqual(parsed.group_of("web"), parsed.groups[0])
        self.assertIsNone(config.parse(ONE).group_of("shop"))
        self.assertEqual(config.parse(ONE).groups, ())

    def test_refuses_what_is_wrong_with_a_group_by_key(self):
        text = GROUPED + """
[groups.second]
projects = ["web", "blog"]

[groups.lonely]
projects = ["api"]

[groups.odd]
projects = ["shop", "api"]
colour = "blue"

[groups.Bad]
projects = ["api", "web"]

[groups.shapeless]
projects = "api"

[groups.empty]
"""
        found = problems(text)
        self.assertEqual(
            found,
            [
                ("groups.second.projects", 'the project "web" is already in the group "platform"'),
                ("groups.second.projects", 'unknown project "blog"'),
                ("groups.lonely.projects", 'the project "api" is already in the group "platform"'),
                ("groups.lonely.projects", "name at least two projects"),
                ("groups.odd.projects", 'the project "shop" is already in the group "platform"'),
                ("groups.odd.projects", 'the project "api" is already in the group "platform"'),
                ("groups.odd.colour", "unknown key"),
                ("groups.Bad", "a group's name has lowercase letters, digits and hyphens, at most 32"),
                ("groups.shapeless.projects", "must be a list of project names"),
                ("groups.empty.projects", "missing"),
            ],
        )

    def test_a_project_named_twice_in_one_group(self):
        text = GROUPED.replace('["api", "web", "shop"]', '["api", "web", "api"]')
        self.assertEqual(problems(text), [("groups.platform.projects", 'the project "api" is already in the group "platform"')])

    def test_groups_that_are_not_a_table(self):
        self.assertIn(("groups", "must be a table of groups"), problems('groups = ["a"]\n'))

    def test_dump_writes_the_groups_after_the_projects_and_reads_back_the_same(self):
        parsed = config.parse(GROUPED)
        text = config.dump(parsed)
        self.assertTrue(text.endswith('\n[groups.platform]\nprojects = ["api", "web", "shop"]\n'), text)
        self.assertEqual(config.parse(text), parsed)


class Write(unittest.TestCase):
    def test_dump_reads_back_as_the_same_configuration(self):
        parsed = config.parse(FULL)
        self.assertEqual(config.parse(config.dump(parsed)), parsed)

    def test_dump_names_a_preset_when_the_features_are_one(self):
        text = config.dump(config.parse(ONE))
        self.assertIn('preset = "recommended"', text)
        self.assertNotIn("build_output", text, "the default build output is not written")
        self.assertNotIn("merge_method", text, "the default method is not written")

    def test_an_empty_configuration_is_an_empty_file(self):
        self.assertEqual(config.dump(config.Config()), "")

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "folder" / "config.toml"
            self.assertEqual(config.load(path), config.Config(), "an absent file is no project")
            parsed = config.parse(FULL)
            config.save(parsed, path)
            self.assertEqual(config.load(path), parsed)
            self.assertEqual(sorted(entry.name for entry in path.parent.iterdir()), [".signalbox", "config.toml"])

    def test_the_default_path_and_the_variable_that_moves_it(self):
        with mock.patch.dict(os.environ, {"SIGNALBOX_CONFIG": ""}):
            self.assertEqual(
                config.default_path(), Path.home() / "Library" / "Application Support" / "signalbox" / "config.toml"
            )
        with mock.patch.dict(os.environ, {"SIGNALBOX_CONFIG": "/elsewhere/c.toml"}):
            self.assertEqual(config.default_path(), Path("/elsewhere/c.toml"))


if __name__ == "__main__":
    unittest.main()
