# M7 — Event emission

| | |
|---|---|
| Owner | **B** |
| Priority | **P0** |
| Status | **Not started** — placeholder so the tree merges cleanly |

Serialise ConflictEvent (<=400 bytes) and POST to /api/events. Buffer to SQLite on failure.

Only owner B edits files in this directory (PRD Section 8). Anyone else
needing a change here goes through B.
