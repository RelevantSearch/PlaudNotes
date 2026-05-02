"""Smoke tests for the plaud-notes-mcp package.

The team-mode test is deliberately RED in this phase — `plaud_notes_mcp.team`
doesn't exist yet. It will go green when Phase 8 (composition) lands. This
proves CI is running and would catch regressions.
"""


def test_package_importable():
    import plaud_notes_mcp.server  # noqa: F401


def test_team_mode_module_importable():
    """Imports the team-mode entrypoint added in Plan 2 Phase 8.

    Currently RED — module doesn't exist until composition phase. Don't
    skip; let it fail loudly so we know CI is honest.
    """
    from plaud_notes_mcp import team  # noqa: F401
