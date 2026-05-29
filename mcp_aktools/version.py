"""Package version helpers."""

from importlib.metadata import PackageNotFoundError, version as _package_version

APP_NAME = "mcp-aktools"
DEFAULT_VERSION = "0.1.14"


def get_version() -> str:
    """Return the installed package version, falling back to the source default."""
    try:
        return _package_version(APP_NAME)
    except PackageNotFoundError:
        return DEFAULT_VERSION

