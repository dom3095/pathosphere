# Critical Points — Open Issues

## CP-001: Claude CLI availability in production (3a)

**Context**: The `claude` backend calls `claude -p "PROMPT"` via subprocess. This requires the `claude` CLI to be installed and authenticated on the machine running Pathosphere.

**Options**:
1. Document as a prerequisite (current approach — recommended)
2. Fall back to qwen-local if `claude` is not found in PATH
3. Use Anthropic API directly (breaks the "subscription credit" goal)

**Recommendation**: Option 1. The CLI is a single-user personal tool; the operator knows what's installed. If option 2 is ever needed, add a `_claude_available()` check in `LLMClient.__init__`.

**Action**: No code change needed now. Document in README when Fase 3 is complete.

---

## CP-002: `briefs` table not in schema.py yet (3b, pending)

**Context**: 3b spec says "save to briefs table (add to schema if missing)". The current `schema.py` DDL does not include a `briefs` table.

**Recommendation**: Add it as a `_MIGRATIONS` entry in `schema.py` so existing DBs are upgraded automatically. Do NOT modify the main DDL string (to avoid breaking init on fresh DBs — the migration approach handles both).

**Status**: To be resolved in session 3b.

---

## CP-003: `theses` table `trigger_event` vs `trigger_event_id` mismatch (3c, pending)

**Context**: The spec for 3c names the Pydantic field `trigger_event_id`, but the DB column is `trigger_event` (no `_id` suffix). The Pydantic model should use `trigger_event_id` internally and map to the DB column `trigger_event` on INSERT.

**Recommendation**: Keep Pydantic field as `trigger_event_id`; use explicit column mapping in the INSERT statement.

**Status**: To be resolved in session 3c.
