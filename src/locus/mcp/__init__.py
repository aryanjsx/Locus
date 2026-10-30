"""MCP (Model Context Protocol) layer for Locus."""

from locus.mcp.client import MCPClient
from locus.mcp.protocol import MCPError, MCPNotification, MCPRequest, MCPResponse
from locus.mcp.server import MCPServer
from locus.mcp.transport import (
    InProcessTransport,
    MCPTransport,
    SSETransport,
    StdioTransport,
    StreamableHTTPTransport,
)

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPNotification",
    "MCPRequest",
    "MCPResponse",
    "MCPServer",
    "MCPTransport",
    "InProcessTransport",
    "SSETransport",
    "StdioTransport",
    "StreamableHTTPTransport",
]
