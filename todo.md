# Greg TODO

## Bug fixes

- [x] Greg seeing every message twice. The current turn's messages were
      saved to history AND appended as the newest prompt. They are now
      excluded from history via their row id, so the model sees each
      message exactly once.
- [x] Duplicate/confused replies when two people typed at once.
      - Replies are now serialized through a single `turn_worker` and
        messages arriving within `TURN_DEBOUNCE` (1s) are answered as one turn.
      - Redelivered Matrix events are deduped by `event_id` so a message can
        never be saved or answered twice.

## Features

- [x] Persistent session: Greg saves his device id + access token to
      `greg.session` (mode 600) after login and restores it on every
      start via `restore_login` + `whoami`. Only one device is ever
      created, so the homeserver hard device limit can't be hit again.
- [x] Owner-only system commands (only `@emil_opsec:matrix.org`):
      `!save_to_memory <text>` (stores a top-importance memory),
      `!list_memory` (shows all memories with ids + importance),
      `!remove_from_memory <id or text>` (delete by id or fuzzy match),
      and `!bypass_sys_prompt <text>` (raw model call, no system prompt).
      Extendable via `handle_admin_command`; owner set by `GREG_ADMIN_USER`.
- [x] On startup Greg clears inactive Matrix sessions from his account
      (old device sessions from previous runs). Uses the two-step
      user-interactive auth flow (echoes the session id from the 401).
      On by default; disable with `GREG_CLEAR_SESSIONS=false`, age
      threshold via `GREG_SESSION_MAX_AGE_DAYS` (default 7).
- [x] All console output is also written to `greg.log` (tee), rotated to
      `greg.log.old` past `GREG_LOG_MAX_MB` (default 10).
- [x] Hot reload (beta): enable via `GREG_HOT_RELOAD=true`, unit at
      `greg.service`. Confirm a file edit restarts Greg via systemd.

## Ideas / backburner

- [ ] Persist processed event ids across restarts (currently in-memory only)
      so redelivered events after a reboot are still ignored.
- [ ] Make `TURN_DEBOUNCE` adaptive or per-room.
- [ ] Add a manual reload command for systemd without a file edit.
