"""MCP server entry point - lets Claude Desktop and other MCP clients drive Lead Studio Lite.

    python leadstudio_mcp.py

Register it in the client's config, e.g. Claude Desktop:
  {"mcpServers": {"lead-studio-lite": {"command": "python",
      "args": ["C:/path/to/LeadStudioLite/leadstudio_mcp.py"]}}}
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from engine import mcp  # noqa: E402


def main():
    return mcp.main()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
