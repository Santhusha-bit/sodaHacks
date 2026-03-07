"""
ws_server.py — WebSocket server that broadcasts agent state to the dashboard.

Runs alongside the main agent loop. The dashboard (dashboard/index.html)
connects to ws://localhost:8765 to receive live JSON state updates.
"""

import asyncio
import json
import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from websockets.server import WebSocketServer

logger = logging.getLogger(__name__)

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    logger.warning("[WS] websockets not installed — dashboard disabled")


class DashboardServer:
    """Broadcasts state JSON to all connected WebSocket clients."""

    def __init__(self, port: int = 8765):
        self.port = port
        self._clients: set = set()
        self._server: Optional["WebSocketServer"] = None
        self._last_state: dict = {}

    async def start(self):
        if not WS_AVAILABLE:
            return
        self._server = await websockets.serve(self._handler, "localhost", self.port)
        logger.info(f"[WS] Dashboard server on ws://localhost:{self.port}")

    async def stop(self):
        server = self._server
        if server is not None:
            server.close()
            await server.wait_closed()

    async def broadcast(self, state: dict[str, Any]):
        """Send state update to all connected dashboard clients."""
        self._last_state = state
        if not WS_AVAILABLE or not self._clients:
            return
        message = json.dumps(state)
        disconnected = set()
        for ws in self._clients:
            try:
                await ws.send(message)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    async def _handler(self, websocket):
        self._clients.add(websocket)
        try:
            # Send current state immediately to new client
            if self._last_state:
                await websocket.send(json.dumps(self._last_state))
            async for _ in websocket:
                pass  # We only push; ignore client messages
        finally:
            self._clients.discard(websocket)
