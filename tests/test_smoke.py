"""Smoke tests for the plaud-notes-mcp package.

Both tests should pass — the team-mode entrypoint went green at Plan 2
Phase 5. They guard against regressions on the core import surface.
"""


def test_package_importable():
    import plaud_notes_mcp.server  # noqa: F401


def test_team_mode_module_importable():
    """The team-mode entrypoint must import cleanly even with no team env vars.

    Lazy GCP-client construction means importing plaud_notes_mcp.team in
    single-tenant local mode doesn't require team-mode setup.
    """
    from plaud_notes_mcp import team  # noqa: F401
