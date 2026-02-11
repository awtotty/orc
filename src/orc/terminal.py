"""WebSocket terminal server — bridges browser connections to tmux panes."""

import asyncio
import json
import subprocess

import websockets


def _tmux_target(project_name, room_name):
    """Build tmux target string for a room."""
    return f"orc:{project_name}-{room_name.lstrip('@')}"


def _tmux_alive(target):
    """Check if a tmux target window exists."""
    try:
        # Extract session and window from target like "orc:proj-room"
        session, window = target.split(":", 1)
        r = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
            capture_output=True, text=True, timeout=2,
        )
        return r.returncode == 0 and window in r.stdout.strip().split("\n")
    except Exception:
        return False


def _capture_pane(target, scrollback=False):
    """Capture tmux pane content with ANSI escapes."""
    cmd = ["tmux", "capture-pane", "-t", target, "-p", "-e"]
    if scrollback:
        cmd.extend(["-S", "-500"])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _send_to_tmux(target, text, literal=True):
    """Send keys to a tmux pane."""
    cmd = ["tmux", "send-keys", "-t", target]
    if literal:
        cmd.extend(["-l", text])
    else:
        cmd.append(text)
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except Exception:
        pass


async def _handle_connection(websocket):
    """Handle a single WebSocket connection."""
    # Parse path: /terminal/{project_name}/{room_name}
    path = websocket.request.path if hasattr(websocket, 'request') else ""
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "terminal":
        await websocket.close(1008, "Invalid path. Use /terminal/{project}/{room}")
        return

    project_name = parts[1]
    room_name = parts[2]
    target = _tmux_target(project_name, room_name)

    if not _tmux_alive(target):
        await websocket.close(1008, f"Room '{room_name}' tmux window not found")
        return

    # Send initial scrollback
    scrollback = _capture_pane(target, scrollback=True)
    await websocket.send(json.dumps({"type": "init", "data": scrollback}))

    last_content = scrollback

    async def stream_output():
        """Poll tmux pane and send updates."""
        nonlocal last_content
        while True:
            await asyncio.sleep(0.1)
            if not _tmux_alive(target):
                await websocket.send(json.dumps({"type": "disconnected", "data": "tmux window closed"}))
                break
            content = _capture_pane(target)
            if content != last_content:
                last_content = content
                await websocket.send(json.dumps({"type": "output", "data": content}))

    async def handle_input():
        """Receive input from client and forward to tmux."""
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            msg_type = msg.get("type")
            data = msg.get("data", "")
            if msg_type == "input":
                _send_to_tmux(target, data, literal=True)
            elif msg_type == "key":
                _send_to_tmux(target, data, literal=False)

    try:
        await asyncio.gather(stream_output(), handle_input())
    except websockets.exceptions.ConnectionClosed:
        pass


async def run_terminal_server(host="127.0.0.1", port=7778):
    """Start the WebSocket terminal server."""
    async with websockets.serve(_handle_connection, host, port):
        await asyncio.Future()  # run forever
