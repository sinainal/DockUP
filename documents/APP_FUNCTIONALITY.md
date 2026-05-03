# DockUP -- Full Function Reference

> **Created:** 2026-03-03  
> **Updated:** 2026-05-03  
> **Version:** 0.2.0  
> **Purpose:** Canonical living reference for the current DockUP runtime surface. Use this document for onboarding, debugging, regression checks, and implementation updates.
>
> **Archive note:** The local-only 0.1.0 reference is kept hidden at `docs/.APP_FUNCTIONALITY_0.1.0.md`.

---

## Table of Contents

1. [What DockUP Is](#1-what-dockup-is)
2. [Project Layout](#2-project-layout)
3. [Runtime Directories and Persistence](#3-runtime-directories-and-persistence)
4. [Configuration](#4-configuration)
5. [State Model](#5-state-model)
6. [Helpers](#6-helpers)
7. [Manifest and Queue Format](#7-manifest-and-queue-format)
8. [Services Layer](#8-services-layer)
9. [Sessions and Run Resume](#9-sessions-and-run-resume)
10. [API Surface](#10-api-surface)
11. [Agent Layer](#11-agent-layer)
12. [CLI and Scripts](#12-cli-and-scripts)
13. [Frontend Surfaces](#13-frontend-surfaces)
14. [Operational Rules](#14-operational-rules)
15. [Dependencies](#15-dependencies)

---

## 1. What DockUP Is

DockUP is a FastAPI-based molecular docking workstation. It combines:

- receptor and ligand asset management
- workspace selection and gridbox creation
- queue building and run orchestration
- results scanning and PLIP parsing
- report generation and figure compilation
- agent-driven docking automation through Ollama
- optional extension management for external docking backends
- a lightweight ligand 3D viewer app

The current application is stateful in memory, but it also persists critical runtime state to disk so hot reloads and restarts do not wipe active selections, queues, or agent-related context.

---

## 2. Project Layout

```text
DockUP/
├── README.md
├── start.sh
├── documents/
│   └── APP_FUNCTIONALITY.md
├── docs/
│   └── .APP_FUNCTIONALITY_0.1.0.md   # local-only archive copy
├── figure_scripts/
│   └── final_plots/
├── scripts/
│   ├── run1.sh
│   ├── run_multi_ligand.py
│   └── agent_tests/
├── docking_app/
│   ├── app.py
│   ├── cli.py
│   ├── config.py
│   ├── state.py
│   ├── helpers.py
│   ├── manifest.py
│   ├── sessions.py
│   ├── services.py
│   ├── models.py
│   ├── routes/
│   ├── extensions/
│   ├── agent/
│   ├── pocket_finder/
│   ├── ligand_3d/
│   ├── static/
│   ├── templates/
│   └── workspace/
└── tests/
```

Key runtime areas:

- `docking_app/workspace/data/ligand/` stores loaded ligand files
- `docking_app/workspace/data/receptor/` stores loaded receptor files
- `docking_app/workspace/data/dock/` stores manifests, runs, sessions, and results
- `docking_app/workspace/.pocket_finder/` stores pocket finder runtime data
- `docking_app/workspace/plip-2.4.0/` contains the bundled PLIP checkout

---

## 3. Runtime Directories and Persistence

### Directory roots

**File:** `docking_app/config.py`

| Constant | Meaning |
|---|---|
| `BASE` | Repository root |
| `PACKAGE_DIR` | `docking_app/` package directory |
| `WORKSPACE_DIR` | Runtime workspace root |
| `DATA_DIR` | Shared runtime data root |
| `LIGAND_DIR` | Ligand storage directory |
| `RECEPTOR_DIR` | Receptor storage directory |
| `DOCK_DIR` | Docking output directory |
| `POCKET_FINDER_DIR` | Pocket finder runtime directory |
| `PLIP_DIR` | Bundled PLIP installation |
| `TEMPLATES_DIR` | Jinja2 templates |
| `STATIC_DIR` | Static assets |

Relative paths are resolved under `WORKSPACE_DIR` first. If the route allows it, a fallback to `BASE` is used for compatibility.

### Persistent state files

- `docking_app/workspace/data/dock/.state_cache.json` stores the DockUP runtime state
- `docking_app/workspace/data/dock/_run_sessions/index.json` stores run session metadata
- `docking_app/workspace/data/dock/.docking_meta/` stores manifest-adjacent run metadata
- report metadata is stored alongside report sources in `.docking_app_meta.json`

---

## 4. Configuration

**File:** `docking_app/config.py`

This module defines the canonical filesystem layout and creates the workspace folders at startup.

It also establishes the practical rule used across the app:

1. Prefer workspace-relative paths.
2. Resolve to absolute paths immediately before file access.
3. Reject path traversal and cross-root access.

This rule is enforced by helpers and route-level validation rather than by UI convention alone.

---

## 5. State Model

**File:** `docking_app/state.py`

DockUP keeps three main runtime state dictionaries in memory:

- `STATE`
- `RUN_STATE`
- `REPORT_STATE`

### STATE

| Key | Meaning |
|---|---|
| `mode` | Active UI mode |
| `selected_receptor` | Active receptor ID |
| `selected_ligand` | Active ligand name |
| `selected_chain` | Active chain selection |
| `selected_ids` | Selected item IDs used by UI flows |
| `active_ligands` | Ligands currently marked active for the selected receptor set |
| `grid_file_path` | Active grid file path |
| `agent_grid_data` | Grid data produced by agent-driven gridbox flows |
| `queue` | Current queue rows |
| `runs` | Run count per job |
| `grid_pad` | Grid padding |
| `docking_config` | Current docking config |
| `out_root` | Output root |
| `out_root_path` | Parent path for output root |
| `out_root_name` | Output folder name |
| `receptor_meta` | Receptor metadata cache |
| `selection_map` | Receptor/chain/ligand selection map |
| `results_root_path` | Results scan root |

### RUN_STATE

| Key | Meaning |
|---|---|
| `status` | `idle`, `running`, `stopping`, `stopped`, `error`, or `done` |
| `returncode` | Process exit code |
| `log_lines` | Recent run log lines |
| `command` | Executed command |
| `out_root` | Output path for the active run |
| `start_time` | Unix timestamp |
| `total_runs` | Planned run count |
| `completed_runs` | Finished run count |
| `batch_log_path` | Batch log path |

### REPORT_STATE

Report generation is also stateful. `REPORT_STATE` tracks:

- current task and progress
- expected total work
- cancel requests
- current receptor, ligand, and run being processed
- active subprocess labels and PIDs

### Default docking config

`DOCKING_CONFIG_DEFAULTS` currently includes:

- `docking_engine`
- `docking_mode`
- `ligand_binding_mode`
- `pdb2pqr_ph`
- `pdb2pqr_ff`
- `pdb2pqr_ffout`
- `pdb2pqr_nodebump`
- `pdb2pqr_keep_chain`
- `mkrec_allow_bad_res`
- `mkrec_default_altloc`
- `vina_exhaustiveness`
- `vina_num_modes`
- `vina_energy_range`
- `vina_cpu`
- `vina_seed`

### Persistence behavior

`STATE` is cached to disk so it survives hot reloads. Large receptor text blobs are intentionally not persisted, but the app can lazily reload them from the stored receptor file.

---

## 6. Helpers

**File:** `docking_app/helpers.py`

### Validation and coercion

- `boolish(value, default)` converts common truthy/falsey strings to `bool`
- `to_optional_int(value, min, max)` returns `None` for empty or invalid integer input
- `to_optional_float(value, min, max)` returns `None` for empty or invalid float input
- `normalize_docking_config(raw)` sanitizes raw docking config dictionaries
- `restore_manifest_value(raw)` restores the `__EMPTY__` sentinel to an empty string

### Ligand and receptor helpers

- `build_flex_residue_spec(...)`
- `normalize_flex_residue_list(...)`
- `normalize_ligand_name_list(...)`
- `normalize_ligand_db_filename(...)`
- `find_identical_file_by_bytes(...)`
- `next_available_ligand_path(...)`

### Path helpers

- `to_display_path(path)` converts absolute paths into workspace-relative display paths
- `relative_to_base(path)` returns a base-relative path or `None`
- `resolve_dock_directory(path_text, default, allow_create)` resolves dock directories safely
- `safe_mtime(path)` returns file modification time or `0.0`
- `read_json(path, default)` reads JSON safely
- `write_json(path, payload)` writes JSON atomically
- `timestamp_token()` returns a compact timestamp token

These helpers are the main guardrails preventing path drift, invalid state, and malformed config values.

---

## 7. Manifest and Queue Format

**File:** `docking_app/manifest.py`

The manifest is the canonical TSV representation of queued docking jobs.

### Row schema

Each row contains:

- receptor ID
- chain
- ligand label
- ligand specification
- receptor PDB file path
- grid padding
- grid file path
- forced run ID
- flexible residue specification
- docking config columns
- job type

### Important functions

| Function | Responsibility |
|---|---|
| `config_to_manifest_values(cfg)` | Serializes a normalized docking config into TSV-friendly values |
| `manifest_values_to_config(cols)` | Deserializes TSV values back into a docking config |
| `append_docking_config_args(args, cfg_raw)` | Adds config flags to a preview command |
| `parse_manifest_rows(manifest_path)` | Reads manifest rows into structured dictionaries |
| `write_manifest(queue, manifest_path)` | Writes the active queue to `manifest.tsv` |
| `build_preview_command(queue, out_root)` | Builds a human-readable run command preview |
| `normalize_ligand_folder_name(name)` | Produces safe folder names for ligand output |
| `persist_root_run_meta(...)` | Stores metadata for later result/report discovery |
| `materialize_queue_runs(...)` | Expands queue rows into concrete run jobs |
| `resolve_out_root_path(...)` | Resolves output roots safely |

### Job behavior

- `build_queue` can append to an existing queue when `replace_queue=False`
- `manifest.tsv` is written before a run is launched
- `run_count` means repeated runs per job, not total combinations

---

## 8. Services Layer

**File:** `docking_app/services.py`

The services layer contains most of the business logic behind the routes.

### Receptors

- fetches PDB text from RCSB
- parses chains and non-water ligands
- stores receptor files locally
- builds receptor metadata rows
- reloads receptor text lazily when needed

### Ligands

- saves uploaded ligand files
- fetches ligands by name / CID / explicit form
- normalizes ligand filenames
- deduplicates or resolves collisions
- keeps `active_ligands` consistent with files on disk

### Selection and grid

- builds the receptor/chain/ligand `selection_map`
- parses uploaded grid files
- validates grid-box payloads
- aligns selections with receptor metadata

### Results and PLIP

- scans docking result folders
- parses PLIP XML reports
- extracts interaction and residue summaries
- supports multi-ligand site loading
- resolves result files safely

### Run startup

- writes manifests
- expands queue rows into concrete run work
- launches background run scripts
- updates `RUN_STATE`
- handles run lifecycle details

Key internal helpers include `_load_receptor_meta`, `_summarize_receptors`, `_build_queue`, `_start_run`, `_scan_results`, `_parse_results_folder`, `_load_multi_ligand_sites`, and `_parse_plip_report`.

---

## 9. Sessions and Run Resume

**File:** `docking_app/sessions.py`

DockUP stores run sessions under:

- `DOCK_DIR/_run_sessions/index.json`

The session layer supports:

- loading saved sessions
- registering new sessions
- scanning incomplete runs
- preparing a resume queue
- continuing an existing run
- deleting a saved session entry

This is the mechanism that keeps recent run recovery separate from the live queue state.

---

## 10. API Surface

DockUP exposes a compact REST surface used by the UI, the agent, and the extension layer.

### 10.1 Core API

**File:** `docking_app/routes/core.py`

Core endpoints:

- `GET /api/state`
- `POST /api/mode`
- `POST /api/ligands/upload`
- `GET /api/ligands/list`
- `POST /api/ligands/fetch`
- `POST /api/ligands/delete`
- `POST /api/ligands/clear_all`
- `GET /api/ligands/active`
- `POST /api/ligands/active/add`
- `POST /api/ligands/active/remove`
- `POST /api/ligands/active/clear`
- `GET /api/ligands/content/{name}`
- `POST /api/receptors/upload`
- `POST /api/receptors/store`
- `GET /api/receptors/list`
- `POST /api/receptors/delete`
- `POST /api/receptors/clear_all`
- `POST /api/receptors/add`
- `POST /api/receptors/load`
- `POST /api/receptors/remove`
- `GET /api/receptors/summary`
- `POST /api/receptors/select`
- `GET /api/receptors/{pdb_id}`
- `GET /api/receptors/{pdb_id}/ligands`
- `POST /api/ligands/select`
- `POST /api/grid/upload`
- `GET /api/grid`
- `POST /api/queue/build`
- `POST /api/queue/remove_batch`
- `POST /api/run/start`
- `POST /api/run/stop`
- `GET /api/run/status`
- `GET /api/run/recent`
- `POST /api/run/recent/prepare`
- `POST /api/run/recent/continue`
- `POST /api/run/recent/delete`

Practical notes:

- queue building requires a populated `selection_map` and matching `grid_data`
- run start writes a manifest and launches the run script in the background
- recent-run endpoints support resume and deletion of saved session entries

### 10.2 Results API

**File:** `docking_app/routes/results.py`

Endpoints:

- `POST /api/results/scan`
- `GET /api/results/dock-folders`
- `POST /api/results/detail`
- `GET /api/results/file`
- `POST /api/paths/resolve`

Behavior notes:

- result roots are resolved through workspace-aware path logic
- result files are only served if they stay inside the allowed roots
- `dock-folders` is used to populate result-source pickers in the UI

### 10.3 Config API

**File:** `docking_app/routes/config_routes.py`

Endpoints:

- `POST /api/config/docking`
- `POST /api/config/save`
- `GET /api/config/load`

Config document behavior:

- JSON config files are accepted directly
- Excel config files are parsed from the `Configuration` sheet
- the config document stores selection map, grid data, run count, padding, output root, and docking config
- `ligand_binding_mode="multi_ligand"` is normalized back into standard docking mode with multi-ligand behavior

### 10.4 Pocket Finder API

**File:** `docking_app/routes/pocket.py`

Endpoints:

- `POST /api/pockets/run`
- `GET /api/pockets/status`
- `GET /api/pockets/results`
- `GET /api/pockets/file`
- `POST /api/pockets/gridbox`
- `POST /api/pockets/clear`

Behavior notes:

- pocket finder runs against the selected receptor and chain
- cached pocket results can be reused
- gridbox generation supports pocket-derived coordinates
- the route layer exposes the computed grid data back to the UI

### 10.5 Report API

**File:** `docking_app/routes/report.py`

Endpoints:

- `GET /api/reports/list`
- `GET /api/reports/preview`
- `GET /api/reports/images`
- `GET /api/reports/root-metadata`
- `GET /api/reports/doc-config`
- `POST /api/reports/doc-config`
- `POST /api/reports/root-metadata`
- `POST /api/reports/source/delete`
- `POST /api/reports/images/delete-all`
- `POST /api/reports/image/delete`
- `GET /api/reports/image/{path:path}`
- `GET /api/reports/doc`
- `POST /api/reports/graphs`
- `POST /api/reports/render`
- `POST /api/reports/render/stop`
- `POST /api/reports/compile`
- `GET /api/reports/status`

Behavior notes:

- report root/source/output paths are resolved independently but safely
- report metadata is stored beside the source folder
- the report engine supports image listing, deletion, graph generation, render jobs, and DOCX compilation
- preview and rendering are driven by receptor and ligand discovery inside the selected source folder

### 10.6 Extensions API

**File:** `docking_app/routes/extensions.py`

Endpoints:

- `GET /api/extensions/vina-gpu-21/status`
- `POST /api/extensions/vina-gpu-21/install`
- `POST /api/extensions/vina-gpu-21/test`
- `POST /api/extensions/vina-gpu-21/uninstall`
- `POST /api/extensions/vina-gpu-21/use-default`
- `POST /api/extensions/vina/use-default`
- `GET /api/extensions/ollama/status`
- `POST /api/extensions/ollama/connect`
- `POST /api/extensions/ollama/models`
- `POST /api/extensions/ollama/offload`
- `POST /api/extensions/ollama/shutdown`
- `POST /api/extensions/ollama/chat`
- `POST /api/extensions/ollama/request-usage`
- `POST /api/extensions/ollama/autonomous-docking`
- `POST /api/extensions/ollama/chat/stream`
- `GET /api/extensions/gemini/status`
- `POST /api/extensions/gemini/save`
- `POST /api/extensions/gemini/models`
- `POST /api/extensions/gemini/chat/stream`

Behavior notes:

- Vina GPU 21 routes control installation and default engine selection
- Ollama routes manage the local model backend, request usage preview, and autonomous docking flow
- Gemini routes persist Gemini-backed settings and stream chat responses

---

## 11. Agent Layer

**Files:** `docking_app/agent/*`, `docking_app/extensions/ollama_agent.py`

### Agent runtime

`docking_app/agent/agent_runtime.py` defines the compact agent memory model:

- `AGENT_SYSTEM_PROMPT`
- `build_agent_working_memory(...)`
- `record_attempt(...)`
- `verify_tool_effect(...)`
- `recent_attempts(...)`
- `was_failed_attempt(...)`
- `normalize_attempt_signature(...)`

The working memory that is passed to the model typically includes:

- the user goal
- a short state summary
- queue batch summaries
- recent tool attempts
- compact memory notes

### Agent state

`docking_app/agent/autonomous_docking.py` keeps `AGENT_STATE`, which is used for:

- inventory of loaded assets
- setup rows
- grid data
- batch config
- batch ID
- workflow stage
- recent actions
- memory summary

### Available agent tools

The main DockUP agent tools are:

- `get_dockup_state`
- `fetch_assets`
- `inspect_assets`
- `select_workspace`
- `set_gridbox`
- `set_docking_config`
- `build_or_run_queue`
- `read_tool_details`
- `delete_ligands`
- `delete_receptors`
- `delete_queue_batches`
- `show_in_viewer`
- `show_residues`
- `run_agent`

### Agent behavior rules

The current agent guidance is optimized for short, goal-driven execution:

- inspect state before guessing
- preserve user-provided receptor and ligand names
- retry a failed fetch once with the cleanest obvious correction
- prefer the main native ligand for gridboxes when it exists
- ignore helper ions and solvent-like residues when a better native ligand is available
- fall back to P2Rank/gridfinder when no usable native ligand exists
- keep multi-ligand docking in `dock_ligands="all"` unless the user restricts it
- treat `run_count` as repeated runs per job, not as combination count
- append queue batches when `replace_queue=False`

### Ollama agent backend

`docking_app/extensions/ollama_agent.py` manages the local model backend.

Current behavior highlights:

- agent temperature is fixed at `0.8`
- `num_predict` is computed as `max(1024, num_ctx // 2)`
- the backend exposes a request-usage preview endpoint
- state is persisted under `.venv/dockup_extensions/ollama_agent/state.json`
- selected models, keep-alive, GPU count, and other runtime settings are normalized and persisted

The UI uses the request-usage information to display a closer approximation of the actual request payload size.

### Autonomous docking script flow

The agent CLI and the autonomous docking layer support:

- asset planning and fetch
- workspace setup
- gridbox construction
- batch submission
- queue validation
- queue building
- run launch

This is the backbone of the agentic workflow used both by the UI and the standalone CLI.

---

## 12. CLI and Scripts

**Files:** `docking_app/cli.py`, `scripts/*`

### CLI entry points

The CLI supports three main modes:

1. Legacy docking execution
2. Agent asset fetching
3. Agent workflow execution without an LLM

#### Legacy docking CLI

Typical flags:

- `--mode Docking|Redocking`
- `--receptors`
- `--ligands`
- `--chain`
- `--grid-cx`, `--grid-cy`, `--grid-cz`
- `--grid-sx`, `--grid-sy`, `--grid-sz`
- `--padding`
- `--runs`
- `--out-root`
- `--out-root-name`
- `--test-mode`

#### `agent-assets`

Fetches receptor and ligand assets, prints a compact inventory, and is useful for quick validation.

#### `agent-workflow`

Executes the DockUP agent flow without an LLM and exposes:

- `--receptors`
- `--ligands`
- `--rows`
- `--box-size`
- `--runs`
- `--padding`
- `--out-root-name`
- `--replace-queue`
- `--test-mode`
- `--pretty`

### Script surface

Useful scripts include:

- `scripts/run1.sh`
- `scripts/run_multi_ligand.py`
- `scripts/agent_tests/run_hard10.py`
- `scripts/agent_tests/run_hard30.py`
- `scripts/autogrid.py`
- `scripts/build_interaction_map.py`

These scripts are part of the operational surface and are referenced by the manifest and report tooling.

---

## 13. Frontend Surfaces

DockUP has multiple frontend surfaces that share the same backend state:

- the main docking web UI at `/`
- the static UI bundle under `docking_app/static/`
- the ligand 3D mini-app mounted at `/ligand-3d`
- the pocket finder UI and overlay assets
- the report builder and report preview flows

The main UI surface includes:

- receptor and ligand selection
- queue and run controls
- token usage and agent context indicators
- gridbox editing and visualization
- result scanning and report generation
- extension controls for Ollama, Gemini, and Vina GPU 21

---

## 14. Operational Rules

These are the most important runtime rules to keep in mind:

1. Relative paths are resolved under `WORKSPACE_DIR` first.
2. Queue build requires both selection data and grid data.
3. Run start always writes a manifest before launching the background process.
4. Recent runs and session resume are handled separately from the live queue.
5. Results and report routes only serve paths that stay inside the allowed workspace roots.
6. The agent should verify state after each meaningful action instead of assuming success.
7. Gridbox creation should prefer a meaningful native ligand and only fall back to P2Rank/gridfinder when needed.
8. Multi-ligand jobs should preserve `dock_ligands="all"` unless the user explicitly restricts the set.
9. `replace_queue=False` is the append mode for multi-batch experiments.
10. The 0.1.0 archive is intentionally local-only and should not be committed.

---

## 15. Dependencies

### Runtime

- `fastapi`
- `starlette`
- `uvicorn`
- `pydantic`
- `python-multipart`
- `requests`
- `httpx`
- `pandas`
- `openpyxl`
- `python-docx`
- `lxml`
- `beautifulsoup4`
- `numpy`
- `jinja2`
- `matplotlib`
- `opencv-python-headless`

### Docking toolchain extras

- `pdb2pqr`
- `meeko`
- `scipy`
- `gemmi`
- `vina`
- `rdkit`
- `openbabel-wheel`

### Optional

- `pymol-open-source`

---

Update this document whenever the runtime surface, routes, state model, or agent workflow changes.
