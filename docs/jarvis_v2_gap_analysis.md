# JARVIS V2.0 — Full System Gap Analysis

**Audit date:** 2026-08-19
**Audited baseline:** commit `a3d664e` + desktop-pivot WIP (working tree).
**Verification:** 131 tests / 22 files green, `ruff` clean, `mypy` clean (63 source
files), `build_runtime()` smoke test passes, 65 tools registered.

---

## 1. Executive Summary

JARVIS is no longer a collection of loose tools: it has a real orchestrator, a
risk-gated executor, an approval manager, a persistent SQLite memory, a scheduler,
RAG retrieval, a coding agent, an autonomous agent loop, vision, productivity tools,
and a substantial tkinter desktop HUD. The remaining work is **integration and
coherence**, not raw feature count.

The five highest-value integration bottlenecks, in priority order:

1. **No unified tool contract.** Every tool returns ad-hoc dicts (`message`,
   `result_text`, `opened`, `count`, ...). Consumers (`orchestrator._compose_response`,
   `agent._snippet`, `build_agent_observation`) each guess which keys exist. There is
   no `category`, no read/write classification, no external-action flag, no per-tool
   timeout, no consistent `success`/`data`/`metadata`/`requires_approval` shape.
   (Phase B)
2. **No unified agent system.** `AutonomousAgent`, `CodeAgent`, and `JobAgent` have
   three different interfaces and are selected by brittle string prefixes in the
   orchestrator. There is no intent-based agent selection and no shared agent
   abstraction for System/Knowledge/Research/Vision/Productivity/Browser work.
   (Phase C)
3. **No task execution engine.** Command execution is fire-and-forget. There are no
   task IDs, task graphs, checkpoints, retry policies, or resumable multi-step goals
   outside the agent loop. The scheduler fires a command and forgets it.
   (Phase D)
4. **Memory is fragmented.** Five separate stores (SQLite `runtime_state`,
   `command_history`, RAG index, calendar DB, scheduler jobs state) with no unified
   view, no episodic/procedural layers, and **no way for the user to edit or inspect
   memory through JARVIS**. (Phase E)
5. **11 tools are reachable only through the LLM planner** (never routed, never in the
   GUI), and the permission model has bypass points (GUI calls the registry directly;
   the coding agent auto-approves its own medium-risk verification; several low-risk
   tools have real side effects). (Phase B + N)

---

## 2. Repository Architecture (as inspected)

```
app/
  bootstrap.py          build_runtime(): wires everything; single composition root
  cli.py, gui.py        interactive surfaces (tkinter HUD is the primary desktop UI)
  minihud.py, tray.py, toast.py, hotkey.py   desktop chrome
  agents/
    code_agent.py       edit -> verify(tests+lint) -> fix loop
    job_agent.py        threaded browser job search/apply/pause/resume
  core/
    orchestrator.py     execute_command / approve / reject / metrics (677 lines)
    agent.py            AutonomousAgent plan->execute->observe loop
    executor.py         risk-gated execution + retries
    approvals.py        in-memory pending approval registry
    router.py           deterministic direct-command routing (typo-tolerant)
    memory.py           SQLite: history, tool history, state, cache, metrics, jobs
    rag.py              SQLite vector/lexical knowledge index
    scheduler.py        persistent recurring/one-shot scheduler thread
    vision.py           VisionAnalyzer (openai/groq/ollama/litellm)
    retrieval_planner.py RAG -> planner prompt decorator
    suggest.py, idle.py, monitor.py, gpu.py, calendar.py, events.py,
    redact.py, safe_paths.py, prompts.py, palette.py, logging.py, utils.py
  llm/planner.py        OpenAIGroqOllamaLiteLLM planners + NullPlanner
  models/schemas.py     Pydantic contract types
  tools/                15 registration modules -> 65 tools (see §5)
  voice/                loop.py (controller), listener.py (mic/STT/VAD), speaker.py (SAPI)
```

The desktop pivot WIP (working tree) removed the web dashboard / embedded server
(`app/main.py`, `app/api/`, `app/web/`, `embedded_server.py`, `core/resources.py`) and
grew `app/gui.py` (1809 lines) plus new `core/palette.py`. **These changes were left
untouched.**

---

## 3. What Already Works Well (verified)

- Direct-command fast path + planner fallback + agent fallback (`orchestrator`).
- Risk-gated execution with retries, pending approvals, approve/reject/resume.
- Secret redaction across memory, cache, HUD feed, and responses.
- Deterministic router with typo tolerance and context-aware YouTube follow-ups.
- SQLite WAL memory with prune, planner cache, metrics, job tracking.
- Scheduler: local-time semantics, no-immediate-first-fire, race-free tick.
- RAG: Ollama embeddings with offline hashed fallback, secret redaction on insert,
  retrieval injected into planner prompts.
- Coding agent with deterministic verification gate and approval resume.
- Vision across 4 providers; OCR + screen + image analysis.
- Productivity: calendar, reminders, email (SMTP), clipboard, TTS announcements.
- Voice: VAD gating, Groq Whisper STT, SAPI TTS, mic auto-pick, follow-up mode,
  WhatsApp send confirmation.
- Proactive: idle detection + rule-based follow-up suggestions.
- 131 tests, ruff/mypy clean, Windows CI.

---

## 4. Complete Feature Inventory with Status

Registry source of truth: `docs/jarvis_feature_registry.yaml`. The header counts
(90 implemented / 41 partial / 61 missing = 192) are **cumulative roadmap totals**;
the YAML body tracks 65 curated features. This audit reconciles both by re-verifying
every feature against the code. Feature registry counts will be corrected in
`docs/jarvis_v2_final_audit.md` (Phase Q).

| Area | Feature | Status (verified) | Notes |
|---|---|---|---|
| core | orchestrator | implemented | Direct + planner + agent paths, approvals |
| core | planner | implemented | Cached; Ollama-first, cloud fallback |
| core | router | implemented | Deterministic, typo-tolerant |
| core | executor | implemented | Risk-gated, retries (no timeout/cancellation) |
| core | tool_registry | implemented | No duplicate detection, no metadata |
| core | approvals | implemented | In-memory; expires; resume plumbing |
| core | secret_redaction | implemented | Env secrets + assignment/bearer patterns |
| core | event_hub | implemented | In-process streaming to HUD |
| core | scheduler | implemented | Local-time, race-free tick |
| core | memory | implemented | SQLite; not user-editable via tools |
| core | safe_paths | implemented | Safe-root restrictions |
| core | shell_policy | implemented | Prefix allowlist, rejects compound |
| voice | microphone_capture | **partial** | PyAudio declared; mic auto-pick; device list/set |
| voice | speech_to_text | implemented | Groq Whisper / OpenAI / Google fallback |
| voice | text_to_speech | implemented | SAPI via pywin32 |
| voice | wake_word | **partial** | Phrase-matching only (no offline hotword model) |
| voice | speaker_diarization | missing | Requires diarization model/hardware |
| desktop | launch_apps / open_url / find_files / list_processes / system_status / gpu_status / screenshots / startup_shortcuts / window_management | implemented | |
| browser | youtube_control / whatsapp / read_webpage / search_web / browser_dsl | implemented | |
| browser | ad_blocking | missing | Browser-level; N/A for desktop HUD scope |
| agent | multi_tool_planning / approve_and_continue / tool_history_recap / known_facts_injection / agent_done_signal / coding_agent / verify_loop / vision | implemented | |
| jobs | job_search / job_pipeline / job_resume | implemented | |
| jobs | job_apply | **partial** | Pauses on login/CAPTCHA; resume; no dedupe guard on job level beyond URL, no interview-stage tracking |
| jobs | application_tracking | **partial** | `jobs_applied` table + status update tool; no next-action/deadline/interview stage, not linked to reminders/calendar |
| rag | learned_facts / semantic_retrieval / document_indexing / planner_injection / recap | implemented | |
| productivity | notes / email / calendar / reminders / clipboard | implemented | |
| proactive | smart_alerts / scheduled_runs / idleness_detection / intent_prediction | implemented | Rule-based only |
| quality | evaluation_metrics / unit_tests / ci_linux / ci_windows / smoke_test | implemented | |
| quality | rate_limiting | **N/A** | Web API layer removed in desktop pivot |

---

## 5. Tool Audit (65 tools)

### 5.1 Disconnected tools (reachable ONLY through the LLM planner catalog)

No router rule, GUI call, or scheduler path invokes them:

| Tool | Risk | Gap |
|---|---|---|
| `read_webpage` | low | Core research primitive — not routed, no research agent consumes it |
| `browser_action` | medium | General browser DSL — not routed; error path is **dead code** (builds a failure dict then `raise RuntimeError`, losing the dict) |
| `list_windows` | low | Not routed |
| `index_file` / `index_notes` | medium/low | RAG indexing only via LLM |
| `get_runtime_context` | low | Debug tool only via LLM |
| `job_update_status` | medium | Application tracking only via LLM |
| `job_pipeline` | low | Only via LLM |
| `schedule` / `list_schedules` / `cancel_schedule` | medium/low | Scheduler only via LLM — no "list schedules" router rule even though suggestions recommend it |

### 5.2 Return-format inconsistencies

- Three "success message" families: `result_text` (24 tools), `message`
  (open/close application), and **neither** (32 tools) → the orchestrator falls back to
  the generic `"Completed: <tool>."`.
- URL keys vary: `opened` vs `url` vs `navigated_to` (same tool `seek_youtube` differs
  by engine path).
- `_execution` metadata is injected into `output` by the executor — framework data mixed
  with tool data.
- `browser_action` uses `status` as an output key, colliding with `ToolExecutionResult.status`.
- Errors are exceptions (consistent at the executor boundary) but several handlers can
  leak raw library exceptions (list_processes, click_mouse, analyze_screen, ...).

### 5.3 Permission / approval bypass points

1. **GUI calls `registry.execute()` directly** for `job_status`, `job_applied`,
   `job_cancel`, `job_resume`, `list_known_apps` — bypassing the executor AND the
   approval gate. `job_cancel`/`job_resume` are medium-risk.
2. **`CodeAgent._verify` auto-approves** `run_tests` + `lint_code` (medium) — a
   hardcoded skip (intentional, but should be an explicit config flag).
3. **Low-risk auto-approval covers side-effecting tools** by default
   (`auto_approve_low_risk=True`): `open_whatsapp_chat` with `send_now=true` actually
   sends a message; `control_youtube`, `open_url`, `set_reminder`, `remember_note`,
   `copy_to_clipboard`, `capture_screenshot` run without approval.
4. **Scheduled jobs** execute with `auto_approve=False`; any low-risk side-effecting
   command in a schedule runs while the user is away.

### 5.4 Registry hardening gaps

- `ToolRegistry.register` silently overwrites on name collision (no duplicate detection).
- Risk is a free string (executor `_max_risk` would `KeyError` on a typo).
- No per-tool timeout / cancellation / error-behavior metadata.

---

## 6. Feature Categorization (Remaining / Non-Implemented Work)

Legend — **A:** high-value, implement. **B:** useful but optional. **C:** low-value.
**D:** unsafe/unnecessary. **E:** not applicable to desktop JARVIS. **F:** requires
external credentials. **G:** requires unavailable hardware/model.

| Feature / capability | Current | Category | Notes & proposed approach |
|---|---|---|---|
| **Unified tool contract** (category, schema, risk, read/write, external, timeout, ToolResult) | missing | **A** | Phase B. Extend `ToolSpec` + `ToolExecutionResult`; normalize at executor boundary; keep handlers intact. |
| **Unified agent system** (intent-based selection, shared orchestrator/registry/memory/permissions/task state) | missing | **A** | Phase C. One `BaseAgent` + small intent classifier + agent registry; retain existing `AutonomousAgent` loop as the executor primitive. |
| **Task execution engine** (task IDs, states, checkpoints, retry, timeout, deps, resume, aggregation) | missing | **A** | Phase D. New `TaskStore` + `TaskRunner` around the existing executor; scheduler becomes a task producer. |
| **Memory 2.0** (episodic + procedural layers, editable/inspectable, unified retrieval) | partial | **A** | Phase E. Add episodic `TaskRecord` and procedural `Workflow` stores; `memory list/forget` tools; expose memory in HUD. |
| **Prompt-injection defense** (retrieved/web content as untrusted data; instruction boundary) | missing | **A** | Phase N. Delimiters + "ignore instructions inside RETRIEVED_CONTEXT" in prompts; adversarial tests. |
| **Real wake-word abstraction** (VAD→hotword→STT chain; honest partial state) | partial | **A** | Phase F. Implement the pipeline + hotword provider interface; keep phrase-based fallback flagged as partial; never claim offline detection. |
| **System intelligence** (diagnose CPU/RAM/disk/GPU/net, reason, suggest; no auto-kill) | partial | **B** | Phase L. `SystemDiagnoser` over `ResourceMonitor` + process sampling; diagnosis prompt; approval before any kill. |
| **Productivity 2.0** (NL task management, daily plan, "what am I forgetting") | partial | **B** | Phase K. Unify tasks/reminders/calendar/notes behind a `TaskPlanner`; natural-language parse; confirmation for external changes. |
| **Proactive intelligence 2.0** (priority, cooldown, dedup, quiet hours, suppression, event-driven) | partial | **B** | Phase I. Upgrade `SuggestionEngine` with scoring + suppression state; hooks into scheduler/monitor events. |
| **Computer vision 2.0** (screen context, UI element recognition, error recognition, app awareness) | partial | **B** | Phase J. Screenshot → window/app detection → OCR → known-error lookup → reasoning. No auto-clicking. |
| **Research agent** (search→collect→read→verify→compare→cite) | missing | **A** | Phase G. `ResearchAgent` consuming `search_web` + `read_webpage` + `browser_action`; structured citations; permission-gated. |
| **Browser navigation / form / download tooling** | missing | **B** | Phase G. Extend `browser_action` DSL (navigation, form fill with approval, download handling). |
| **Application tracking 2.0** (interview stage, next action, deadline, linked to reminders/calendar) | partial | **A** | Phase H. Extend `jobs_applied` schema + `job_update_status`; wire follow-up reminders; "pending applications" router rule. |
| **Job apply safety** (login detect, CAPTCHA pause, resume, dedupe, state) | partial | **A** | Phase H. Already pauses on login/CAPTCHA; add explicit approval-resume gate and per-job dedupe checks. |
| **Voice: barge-in / continuous conversation** | missing | **B** | Phase F. STT/TTS concurrency + stop-event barge-in; hardware-dependent. |
| **Speaker diarization** | missing | **G** | No local diarization model/hardware available. Record as not-applicable-to-build-now. |
| **Ad-blocking** | missing | **E** | Browser extension scope; low value for a desktop assistant. Not building. |
| **Rate limiting** | missing | **E** | Web API layer removed in desktop pivot; HUD inputs are local. Not building. |
| **Ambient application monitoring** (active project, repeated workflows, Git state) | missing | **B** | Phase I. Small watcher over git status + active window. |
| **Performance pass** (startup, planner latency, embedding cache, redundant LLM calls) | partial | **B** | Phase O. Already has planner cache; add RAG embedding cache + startup timings. |
| **Security adversarial tests** (prompt injection, path traversal, command injection, permission denial) | missing | **A** | Phase N + P. Add security test suite. |
| **GUI integration of task/approval/status surfaces** | partial | **B** | Phase M. The HUD already shows feed/approvals; add task progress + memory panels. Preserve WIP. |
| **End-to-end scenario test** | missing | **A** | Phase R. Deterministic multi-domain smoke using NullPlanner + stub tools. |
| **Feature registry accuracy** (192-feature re-audit) | stale | **A** | Phase Q. `docs/jarvis_v2_final_audit.md` with honest counts. |

### Final category roll-up (of the non-implemented / non-tested work above)

- **A — high-value, implement:** 12
- **B — useful but optional:** 9
- **C — low-value:** 0
- **D — unsafe/unnecessary:** 0
- **E — not applicable to desktop JARVIS:** 2 (ad-blocking, rate limiting)
- **F — requires external credentials:** 0 (all present or graceful)
- **G — requires unavailable hardware/model:** 1 (speaker diarization)

---

## 7. Architectural Bottlenecks (ranked)

1. **No tool metadata / contract** — blocks coherent agent reasoning, safe permission
   derivation, timeouts, and consistent UX. *Fix: Phase B.*
2. **Ad-hoc agent routing by string prefix** — `_is_agent_worthy` /
   `_is_code_worthy` / `JobAgent` / voice are separate, inconsistent entry points.
   *Fix: Phase C.*
3. **No persistent task lifecycle** — every goal either completes synchronously or is
   forgotten. *Fix: Phase D.*
4. **Five storage silos with no unified memory view** — runtime_state vs RAG vs
   calendar vs scheduler_jobs vs jobs_applied. *Fix: Phase E.*
5. **Permission gaps** (GUI registry bypass, auto-approved medium verification, default
   low-risk side effects, scheduler while-away). *Fix: Phase B + N.*
6. **Brittle result-key guessing** in `_compose_response` / `_snippet` /
   `build_agent_observation`. *Fix: Phase B.*
7. **Single monolithic orchestrator** (677 lines) mixing routing, planning, memory,
   metrics, approvals, and response composition. *Fix: Phase C/D decomposition.*

---

## 8. Duplicate / Dead / Inconsistent Code Findings

- **Dead code:** `browser_action` error payload discarded on raise (`browser_action.py`).
- **Duplicated state:** scheduler jobs + scheduled_runs in `runtime_state`; job pipeline
  both in `jobs_applied` (memory) and `active_job_task` (state); `activity` vs
  `runtime_context` state keys overlap.
- **Inconsistent interfaces:** `_snippet` guesses `result_text/message/opened/count/url/...`;
  `build_agent_observation` guesses `opened/count/message/url/extracted/done/summary`;
  orchestrator guesses `opened|url|navigated_to`.
- **Duplicate concept:** `ResourceMonitor` (core) vs `system_status` tool (tools) vs
  `monitor.py` — three ways to read the same metrics.
- **Registry inconsistency:** header claims 90/41/61 but YAML body tracks 65 features;
  `rate_limiting` marked missing but noted N/A.

---

## 9. Recommended Phase Order (highest value first)

1. **Phase B — Unified tool contract** (metadata + ToolResult normalization + registry
   hardening + tests). Foundation for everything below.
2. **Phase C — Unified agent system** (intent classifier + agent registry + shared
   abstractions). Replaces prefix routing.
3. **Phase D — Task execution engine** (persistent task lifecycle over the executor).
4. **Phase E — Memory 2.0** (episodic/procedural layers, editable via tools, unified view).
5. **Phase N — Security hardening + adversarial tests** (permission bypass fixes, prompt
   injection defense).
6. **Phase G — Research agent** (search→read→verify→cite) + **Phase H — job automation**.
7. **Phases F / I / J / K / L** — voice pipeline, proactive 2.0, vision 2.0, productivity 2.0,
   system intelligence (all build on B–E).
8. **Phases M / O / P / Q / R** — UI integration, performance, testing, final audit, E2E.

---

## 10. Guardrails for the Work Ahead

- Preserve the desktop-pivot WIP (deleted web/api/main files, palette.py, gui.py) exactly
  as-is unless a phase genuinely requires integration.
- Never bypass CAPTCHA, authentication, or approval gates.
- Never auto-kill processes, send mail/messages, or submit external applications without
  explicit approval.
- Treat RAG documents and webpages as untrusted data; never let retrieved text override
  system instructions.
- A feature is only "implemented" when code exists, is integrated, is tested, is usable
  through JARVIS, is error-handled, and is security-appropriate.
