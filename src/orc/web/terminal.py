"""WebSocket terminal server — bridges browser to tmux panes via PTY."""

import asyncio
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import termios
from urllib.parse import unquote

import websockets


def _tmux_target(project_name, room_name):
    """Build tmux target string for a room."""
    return f"orc:{project_name}-{room_name.lstrip('@')}"


def _tmux_alive(target):
    """Check if a tmux target window exists."""
    try:
        session, window = target.split(":", 1)
        r = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
            capture_output=True, text=True, timeout=2,
        )
        return r.returncode == 0 and window in r.stdout.strip().split("\n")
    except Exception:
        return False


def _read_pty(fd):
    """Read available data from PTY fd with short timeout."""
    r, _, _ = select.select([fd], [], [], 0.02)
    if r:
        try:
            return os.read(fd, 65536)
        except OSError:
            return None
    return None


async def _handle_connection(websocket):
    """Handle a WebSocket connection by bridging to a tmux pane via PTY."""
    raw_path = websocket.request.path if hasattr(websocket, "request") else ""
    parts = [unquote(p) for p in raw_path.strip("/").split("/")]
    if len(parts) != 3 or parts[0] != "terminal":
        await websocket.close(1008, "Invalid path. Use /terminal/{project}/{room}")
        return

    project_name = parts[1]
    room_name = parts[2]
    target = _tmux_target(project_name, room_name)

    if not _tmux_alive(target):
        await websocket.close(1008, f"Room '{room_name}' tmux window not found")
        return

    # Create PTY pair and spawn tmux attach
    master_fd, slave_fd = pty.openpty()
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    pid = os.fork()
    if pid == 0:
        # Child: become session leader, attach slave as controlling terminal
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.execvp("tmux", ["tmux", "attach-session", "-t", target])
        os._exit(1)

    os.close(slave_fd)

    # Non-blocking reads on master
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    loop = asyncio.get_event_loop()
    closed = False

    async def stream_output():
        """Read PTY output and send to browser as binary."""
        nonlocal closed
        while not closed:
            data = await loop.run_in_executor(None, _read_pty, master_fd)
            if data:
                try:
                    await websocket.send(data)
                except websockets.exceptions.ConnectionClosed:
                    break
            else:
                await asyncio.sleep(0.01)

    async def handle_input():
        """Receive browser input and write to PTY."""
        nonlocal closed
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    os.write(master_fd, message)
                elif isinstance(message, str):
                    # Check for resize command
                    if message.startswith("{"):
                        try:
                            msg = json.loads(message)
                            if msg.get("type") == "resize":
                                rows = msg.get("rows", 40)
                                cols = msg.get("cols", 120)
                                fcntl.ioctl(
                                    master_fd,
                                    termios.TIOCSWINSZ,
                                    struct.pack("HHHH", rows, cols, 0, 0),
                                )
                                os.kill(pid, signal.SIGWINCH)
                                continue
                        except (json.JSONDecodeError, TypeError, OSError):
                            pass
                    os.write(master_fd, message.encode())
        except (websockets.exceptions.ConnectionClosed, OSError):
            pass
        finally:
            closed = True

    try:
        await asyncio.gather(stream_output(), handle_input())
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        closed = True
        os.close(master_fd)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass


async def run_terminal_server(host="127.0.0.1", port=7778):
    """Start the WebSocket terminal server."""
    async with websockets.serve(_handle_connection, host, port):
        await asyncio.Future()  # run forever
