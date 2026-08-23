"""Console entrypoint: ``uv run deepseek-mcp`` or ``python -m deepseek_mcp``.

Runs the MCP server over stdio. All diagnostics go to stderr; stdout carries
only MCP protocol framing.
"""

from __future__ import annotations

import logging
import sys

from deepseek_mcp.config.loader import ConfigError, load_config
from deepseek_mcp.server import create_app


def _configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.handlers = [handler]


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"deepseek-mcp: {exc}", file=sys.stderr)
        sys.exit(1)
    _configure_logging(config.logging.level)
    app = create_app(config)
    app.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
