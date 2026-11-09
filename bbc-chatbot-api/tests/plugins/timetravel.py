"""--as-of=YYYY-MM-DD: run the whole suite as if today were that date.

A test that hardcodes "next December" is green until December. CI runs the
suite once more, 120 days ahead, so a test that is about to expire fails
months before it would have failed on its own."""
import pytest

_freezer = None


def pytest_addoption(parser):
    parser.addoption(
        "--as-of",
        default=None,
        help="freeze 'today' for the session (YYYY-MM-DD)",
    )


def pytest_configure(config):
    global _freezer
    as_of = config.getoption("--as-of")
    if as_of:
        # Import pydantic.v1 before the freezer patches datetime: fastapi 0.134
        # imports it lazily at the first route, and its `class PastDate(date)`
        # then subclasses freezegun's FakeDate (metaclass conflict).
        import pydantic.v1  # noqa: F401
        from freezegun import freeze_time

        # Import the heavy third-party trees (langgraph → pydantic.v1) BEFORE
        # the clock is frozen. pydantic.v1 builds metaclasses at import; doing
        # that under a frozen clock raises a metaclass conflict. A normal run
        # imports them early anyway — this makes the --as-of run match.
        try:
            import app.graph.build  # noqa: F401
        except ModuleNotFoundError as e:
            # A module not installed: the run still works, but a later
            # metaclass conflict under the frozen clock would hide this
            # cause, so say it now. Anything else ("cannot import name …",
            # an error in the module) fails the run here.
            import warnings

            warnings.warn(f"timetravel: app.graph.build could not be imported before freezing: {e}")

        _freezer = freeze_time(f"{as_of} 12:00:00", tick=True)
        _freezer.start()


def pytest_unconfigure(config):
    global _freezer
    if _freezer:
        _freezer.stop()
        _freezer = None
