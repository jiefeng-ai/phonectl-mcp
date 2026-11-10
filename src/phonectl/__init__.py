"""phonectl: an MCP server that drives a real iPhone over USB."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("phonectl-mcp")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0+unknown"
