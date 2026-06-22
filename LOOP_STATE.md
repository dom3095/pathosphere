# Pathosphere — Loop State

## Current subtask
**3c** — `pathosphere/agent/thesis.py` (thesis generator)

## Completed subtasks
- [x] **3a** — `pathosphere/llm/client.py` + `pathosphere/llm/__init__.py`
- [x] **3b** — `pathosphere/agent/brief.py` (morning brief generator)

## Pending subtasks
- [ ] 3c — `pathosphere/agent/thesis.py`
- [ ] 3d — Approval flow CLI
- [ ] 3e — `pathosphere/trading/portfolio.py`
- [ ] 3f — `pathosphere/trading/predictions.py`

## Session notes (3b)

### What was done
- Added `briefs` table via `_MIGRATIONS` in `schema.py` (resolves CP-002)
- Created `pathosphere/agent/__init__.py` exporting `generate_brief`, `BriefResult`
- Created `pathosphere/agent/brief.py` with:
  - `_query_divergences()` — events with `divergence_score > 0.5` within lookback window
  - `_query_hub_entities()` — entities by total co-occurrence degree (CTE UNION ALL approach)
  - `_query_recent_anomalies()` — recent portwatch/usgs/firms/ioda events
  - `_build_prompt()` — constructs chat-message list for LLM
  - `_save_brief_file()` — writes `data/briefs/YYYY-MM-DD.md`
  - `_save_brief_db()` — upserts into `briefs` table on `date` conflict
  - `generate_brief()` — async orchestrator returning `BriefResult`
- Added `pathos brief` CLI command (options: `--date`, `--lookback-days`, `--model`, `--dry-run`)
- Wired `_phase_brief()` in `pathosphere/cycle/orchestrator.py` (replaced stub)
- Created `tests/test_brief.py` (33 tests)
- Updated `tests/test_db.py` to include `briefs` in EXPECTED_TABLES
- Full suite: 230/230 passing

### Architectural decisions
- `briefs` table added via `_MIGRATIONS` (not main DDL) so existing DBs are upgraded automatically
- `generate_brief` accepts `briefs_dir: Path | None` for testability without touching settings
- Hub-entity query uses `UNION ALL` CTE to count both directions of `entity_links` efficiently
- `datetime.now(timezone.utc)` used throughout (replaces deprecated `utcnow()`)
- `pathos brief --dry-run` prints signal counts without making any LLM call (useful for debugging)

## Session notes (3a)

### What was done
- Added `reasoning_model: str = "claude"` field to `pathosphere/config.py`
- Created `pathosphere/llm/__init__.py` exporting `LLMClient`
- Created `pathosphere/llm/client.py` with:
  - `LLMClient(backend="claude"|"qwen-local")` class
  - `async def complete(messages, *, model=None, json_mode=False) -> str`
  - Claude backend: calls `claude -p "PROMPT"` via `subprocess.run` in thread pool
  - Qwen-local backend: async HTTP POST to `http://localhost:11434/v1/chat/completions`
  - `json_mode=True` injects a JSON-enforcement system message
  - `_messages_to_text()` flattens multi-turn chat for the Claude CLI
- Created `tests/test_llm_client.py` (16 tests, all passing)
- Full suite: 197/197 passing

### Architectural decisions
- Claude SDK called via subprocess (`claude -p`) as specified — avoids API costs, uses subscription credit
- `asyncio.to_thread` wraps the blocking subprocess call so the async interface is uniform
- Qwen backend uses `httpx.AsyncClient` for native async; raises descriptive `RuntimeError` on `ConnectError`
- `json_mode` merges JSON-enforcement into existing system message rather than adding a duplicate role
