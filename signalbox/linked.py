"""Which linked project a folder belongs to: the one whose repository shares the folder's git
common directory, so every worktree of a linked repository belongs to its project, wherever
the worktree is."""

import os

from .atoms import worktrees


def real(path):
    return os.path.realpath(str(path))


def project_for(configuration, folder, git):
    """(the project, where the folder is in its repository); (None, where) when no project is
    the folder's, and (None, None) when the folder is in no repository."""
    where = worktrees.locate(git, folder)
    if where is None:
        return None, None
    common = real(where.common_dir)
    for project in configuration.projects:  # the usual layout first: no git to run
        if real(project.directory / ".git") == common:
            return project, where
    for project in configuration.projects:
        if not project.directory.is_dir():
            continue
        other = worktrees.locate(git, project.directory)
        if other is not None and real(other.common_dir) == common:
            return project, where
    return None, where
