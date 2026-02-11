# Room Status Monitoring Plan

## Problem

Room statuses in orc are **self-reported**: agents write their own `status.json` (active, ready, blocked, done, exited). There is no system to detect when reality diverges from the declared status — a room can claim "active" while its tmux window is dead, or an agent can be idle for hours without updating its status. The orchestrator and dashboard have no way to know this without manually checking.

## Current Architecture

### How statuses work today

1. **Room.set_status(status)** — writes `{"status": "..."}` to `.orc/{room}/status.json`
2. **Room.read_status()** — reads the JSON file
3. **Status is set in two places:**
   - `project.py:154` — sets "active" when attaching/launching an agent
   - Agents themselves — write status.json via filesystem (the worker role instructs agents to update their own status)
4. **Status is read in three places:**
   - `project.py:214` (list_rooms) — CLI `orc list`
   - `web.py:84` (get_rooms) — dashboard API
   - `dashboard.py:41` (collect_rooms) — TUI dashboard
5. **tmux liveness** — checked separately via `RoomSession.is_alive()` / `_tmux_alive()`, which runs `tmux list-windows` and checks if the window name exists. This is reported alongside status but never reconciled with it.

### Key observation

Status and tmux liveness are **independent signals** that are never correlated. A room can be `status=active, tmux=dead` (crashed agent) or `status=done, tmux=alive` (finished but window lingers). The dashboard shows both but doesn't flag contradictions.

## Design Questions & Recommendations

### 1. What should trigger a status check?

| Approach | Pros | Cons |
|----------|------|------|
| **Timer (polling)** | Simple, predictable, works everywhere | Slight delay, unnecessary work when idle |
| **Filesystem watch (inotify)** | Instant reaction to status.json changes | Doesn't detect tmux death, platform-specific, complex |
| **tmux hooks** | Can fire on window death | Only covers tmux events, not agent idleness |

**Recommendation: Timer-based polling** at ~5s intervals. It's simple, catches all signal types (file changes, tmux state, idle detection), and matches the dashboard's existing 5s refresh cycle. Filesystem watching could be added later as an optimization for instant status.json change detection, but polling alone is sufficient for v1.

### 2. What should be monitored?

Four signals, in order of implementation priority:

#### Signal A: tmux window alive/dead
- **How:** `tmux list-windows -t orc -F '#{window_name}'` (already implemented in `_tmux_alive`)
- **Meaning:** If tmux is dead but status is "active", the agent has crashed or been killed
- **Action:** Set status to "exited", notify orchestrator

#### Signal B: Agent idle detection
- **How:** Capture pane content (`tmux capture-pane -t target -p -S -5`) and check if the last few lines show a prompt waiting for input (e.g., `$`, `>`, or Claude Code's idle prompt pattern). Compare pane content hash across polls — if unchanged for N cycles, agent is idle.
- **Meaning:** Agent finished its work but didn't update status, or is waiting for input
- **Action:** After threshold (e.g., 30s of identical pane content), flag as "idle" or auto-set "ready"

#### Signal C: Stuck detection
- **How:** Track when a room's status last changed (add `updated_at` to status.json). If status has been "active" for longer than a configurable threshold (e.g., 15 minutes) without any pane content change, flag as potentially stuck.
- **Meaning:** Agent may be in an infinite loop, waiting on a hung process, or experiencing an error it can't recover from
- **Action:** Notify orchestrator, optionally flag in dashboard

#### Signal D: status.json consistency
- **How:** Read status.json and compare against tmux state
- **Contradiction matrix:**

| status.json | tmux | Meaning | Action |
|-------------|------|---------|--------|
| active | alive | Normal | None |
| active | dead | Crashed | Set "exited", notify |
| ready | alive | Waiting | None |
| ready | dead | Stopped | None (expected after clean shutdown) |
| blocked | alive | Stuck on dependency | None |
| blocked | dead | Crashed while blocked | Set "exited", notify |
| done | alive | Finished, window lingers | None |
| done | dead | Clean completion | None |
| exited | alive | Unexpected restart? | Log warning |
| exited | dead | Normal post-exit | None |

### 3. How should status changes be propagated?

Three channels, all should be implemented:

#### Channel A: Update status.json (always)
The monitor directly writes status.json when it detects a contradiction. This is the source of truth that all other consumers read.

#### Channel B: Notify orchestrator via inbox
When a meaningful status change is detected (crash, stuck, idle), append a message to `.orc/@main/inbox.json`:
```json
{
  "from": "monitor",
  "message": "Room 'worker-1' crashed (tmux dead, was active). Status set to exited.",
  "read": false,
  "ts": "2026-02-11T16:30:00Z"
}
```

#### Channel C: WebSocket push to dashboard
The web server already has a WebSocket server (terminal.py on port 7778). Add a separate "events" WebSocket endpoint (e.g., `ws://localhost:7778/events`) that broadcasts status change events as JSON. The dashboard frontend subscribes and updates cards in real-time instead of (or in addition to) polling every 5s.

Event format:
```json
{
  "type": "status_change",
  "project": "myproject",
  "room": "worker-1",
  "old_status": "active",
  "new_status": "exited",
  "reason": "tmux_dead",
  "ts": "2026-02-11T16:30:00Z"
}
```

### 4. Where should the monitoring loop live?

| Approach | Pros | Cons |
|----------|------|------|
| **Background thread in web server** | Co-located with WebSocket push, single process | Ties monitoring to dashboard being running |
| **Separate CLI command (`orc monitor`)** | Independent lifecycle, can run without dashboard | Another process to manage |
| **Tmux watchdog (shell script)** | Very lightweight | Limited logic, hard to extend |
| **Integrated into existing dashboard refresh** | Zero new processes | Only works when dashboard is open |

**Recommendation: Background thread in the web server** (`web.py:run_server`). Rationale:
- The web server already runs persistently in a tmux window (`.orc-dash`)
- It already has access to all project/room data
- It's the natural home for WebSocket event broadcasting
- Adding a `threading.Thread(target=monitor_loop, daemon=True)` is trivial
- If the dashboard dies, monitoring stops too — but the dashboard dying is itself a problem worth noticing

The monitor thread should be a simple loop:
```python
def monitor_loop(interval=5):
    while True:
        for project_name, project_path in discover_projects().items():
            check_rooms(project_name, project_path)
        time.sleep(interval)
```

### 5. Integration with the web dashboard

#### Server-side changes
- New API endpoint: `GET /api/projects/{name}/events` — SSE (Server-Sent Events) stream for real-time updates. SSE is simpler than WebSocket for one-way server→client push and works over the existing HTTP server.
- Alternative: Add an events WebSocket endpoint to the existing terminal WebSocket server.

#### Client-side changes
- Subscribe to SSE/WebSocket on page load
- On status change event, update the specific room card without full page refresh
- Add visual indicators for detected issues:
  - Pulsing red border on cards with contradictory status (status=active but tmux=dead)
  - "idle" badge (new status color, e.g., blue/gray)
  - "stuck" warning icon with tooltip showing duration
- Add a notification toast/banner for critical events (crashes, stuck agents)

#### Dashboard polling optimization
With push notifications in place, the 5s polling interval can be increased to 30s as a fallback, reducing server load.

### 6. Edge cases

| Edge case | Detection | Handling |
|-----------|-----------|----------|
| **Agent crashes mid-write** | status.json is corrupt/partial | `_read_json` already returns `{}` on error; treat as "unknown" |
| **tmux window dies** | `is_alive()` returns False | Set status to "exited" if was "active" or "blocked" |
| **Network issues (sandbox)** | tmux commands time out | Already has 2s timeout; treat as "unknown", don't change status |
| **Race condition: agent updates status while monitor reads** | File partially written | Use atomic writes (write to tmp + rename) in Room.set_status |
| **Monitor restarts** | Previous state lost | Rebuild state from status.json + tmux on startup; no persistent monitor state needed |
| **Multiple monitors running** | Duplicate notifications | Use file locking or a pidfile to ensure single instance |
| **Rapid status flapping** | Agent bouncing between states | Debounce notifications: only notify if status stable for 2 poll cycles |

## Implementation Plan

### Phase 1: Core monitoring loop (new file: `src/orc/monitor.py`)

1. Create `monitor.py` with:
   - `MonitorState` class — tracks per-room state (last pane hash, last status change time, last notification time)
   - `check_room(project_name, project_path, room_name, state)` — runs all signal checks
   - `monitor_loop(interval=5)` — iterates all projects/rooms
   - `reconcile_status(room, tmux_alive)` — applies the contradiction matrix
2. Add `updated_at` field to status.json writes in `Room.set_status()`
3. Add orchestrator notification helper (append to `@main/inbox.json`)

### Phase 2: Web server integration

4. Start monitor thread in `run_server()` alongside the WebSocket terminal server
5. Add event queue (thread-safe `queue.Queue`) for monitor→server communication
6. Add SSE endpoint `GET /api/events` that streams from the event queue

### Phase 3: Dashboard frontend

7. Connect to SSE stream on page load
8. Update room cards on events without full refresh
9. Add visual indicators (crash border, idle badge, stuck warning)
10. Add notification toasts for critical events

### Phase 4: Hardening

11. Atomic writes for status.json (tmp + rename)
12. Debounce notifications (suppress duplicate alerts)
13. Pidfile or lock to prevent multiple monitor instances
14. Configurable thresholds (idle timeout, stuck timeout, poll interval)

## Files to modify

| File | Changes |
|------|---------|
| `src/orc/monitor.py` | **New** — monitoring loop, state tracking, reconciliation |
| `src/orc/room.py` | Add `updated_at` to status.json; atomic writes |
| `src/orc/web.py` | Start monitor thread; add SSE endpoint; add event queue |
| `src/orc/static/index.html` | SSE subscription; real-time card updates; visual indicators |
| `src/orc/cli.py` | Optional: `orc monitor` command for standalone mode |
