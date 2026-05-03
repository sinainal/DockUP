# DockUP -- Full Function Reference

> **Created:** 2026-03-03
> **Last updated:** 2026-05-03
> **Version:** 0.2.0
> **Purpose:** Living reference for DockUP's application surface. Use this document for debugging, regression checks, onboarding, and implementation updates.

---

## Table of Contents

1. [Directory Structure](#1-directory-structure)
2. [Runtime State](#2-runtime-state)
3. [Configuration](#3-configuration)
4. [Helpers](#4-helpers)
5. [Manifest](#5-manifest)
6. [Services](#6-services)
7. [Sessions](#7-sessions)
8. [Core API](#8-core-api)
9. [Pocket API](#9-pocket-api)
10. [Results API](#10-results-api)
11. [Report API](#11-report-api)
12. [Config API](#12-config-api)
13. [Extensions API](#13-extensions-api)
14. [Agent Layer](#14-agent-layer)
15. [Frontend / Backend Flow](#15-frontend--backend-flow)
16. [Known Issues and Notes](#16-known-issues-and-notes)
17. [Dependencies](#17-dependencies)

---

## 1. Directory Structure

```text
DockUP/
├── README.md
├── start.sh
├── documents/
├── figure_scripts/
│   └── final_plots/
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
│   ├── static/
│   ├── templates/
│   └── workspace/
├── scripts/
└── tests/
```

Key runtime areas:

- `docking_app/workspace/data/ligand/`: loaded ligand files
- `docking_app/workspace/data/receptor/`: loaded receptor files
- `docking_app/workspace/data/dock/`: docking output, sessions, and manifests
- `scripts/`: run scripts and queue execution helpers

---

## 2. Runtime State

**Files:** `docking_app/state.py`, `docking_app/agent/state_context.py`, `docking_app/agent/agent_runtime.py`

The application keeps a process-local `STATE` dictionary and a `RUN_STATE` dictionary. The agent layer also keeps a compact `AGENT_STATE`. All of them reset when the server restarts.

### STATE keys

| Key | Meaning |
|---|---|
| `mode` | Active UI mode |
| `selected_receptor` | Active receptor ID |
| `selected_ligand` | Active ligand name |
| `selected_chain` | Active chain selection |
| `grid_file_path` | Active grid file path |
| `queue` | Current queue rows |
| `runs` | Run count per job |
| `out_root` | Output root |
| `out_root_path` | Parent path for output root |
| `out_root_name` | Output folder name |
| `receptor_meta` | Receptor metadata cache |
| `selection_map` | Receptor / chain / ligand selection map |
| `results_root_path` | Results scan root |
| `docking_config` | Current docking config |
| `grid_pad` | Grid padding |
| `agent_grid_data` | Gridboxes computed by the agent |

### RUN_STATE keys

| Key | Meaning |
|---|---|
| `status` | `idle`, `running`, `stopping`, `stopped`, `error`, or `done` |
| `returncode` | Process exit code |
| `log_lines` | Recent log lines |
| `command` | Executed command |
| `out_root` | Output path for the active run |
| `start_time` | Unix timestamp |
| `total_runs` | Planned run count |
| `completed_runs` | Finished run count |
| `batch_log_path` | Batch log path |

### Agent memory

The agent runtime keeps a short working memory, not a full transcript. It tracks:

- recent actions
- last tool
- last error
- last answer
- workflow stage
- compact state summary

### Default docking config

```python
{
    "pdb2pqr_ph": 7.4,
    "pdb2pqr_ff": "AMBER",
    "pdb2pqr_ffout": "",
    "pdb2pqr_nodebump": True,
    "pdb2pqr_keep_chain": True,
    "mkrec_allow_bad_res": False,
    "mkrec_default_altloc": "A",
    "vina_exhaustiveness": 8,
    "vina_num_modes": None,
    "vina_energy_range": None,
    "vina_cpu": None,
    "vina_seed": None,
}
```

---

## 3. Configuration

**File:** `docking_app/config.py`

| Constant | Description |
|---|---|
| `BASE` | Repository root |
| `PACKAGE_DIR` | `docking_app/` package root |
| `WORKSPACE_DIR` | Runtime data root |
| `DATA_DIR` | Shared data root |
| `LIGAND_DIR` | Ligand storage directory |
| `RECEPTOR_DIR` | Receptor storage directory |
| `DOCK_DIR` | Docking output directory |
| `PLIP_DIR` | PLIP installation directory |
| `TEMPLATES_DIR` | Jinja2 template directory |
| `STATIC_DIR` | Static assets directory |

Path resolution rule:

- Relative paths are resolved under `WORKSPACE_DIR` first.
- If not found there, `BASE` is used as fallback when the route allows it.

---

## 4. Helpers

**File:** `docking_app/helpers.py`

Core helpers:

- `boolish(value, default)` converts common truthy/falsey strings to `bool`
- `to_optional_int(value, min, max)` returns `None` for empty or invalid values
- `to_optional_float(value, min, max)` returns `None` for empty or invalid values
- `normalize_docking_config(raw)` sanitizes raw docking config dictionaries
- `restore_manifest_value(raw)` restores the `__EMPTY__` sentinel to an empty string
- `normalize_ligand_db_filename(filename)` normalizes ligand filenames for the workspace

Path helpers:

- `to_display_path(path)` converts an absolute path into a workspace-relative display path
- `relative_to_base(path)` returns a base-relative path or `None`
- `resolve_dock_directory(path_text, default, allow_create)` resolves a docking output path safely
- `safe_mtime(path)` returns file modification time or `0.0`
- `read_json(path, default)` reads JSON safely
- `write_json(path, payload)` writes JSON atomically
- `timestamp_token()` returns a compact timestamp token

Workspace helpers:

- `build_flex_residue_spec(...)`
- `normalize_flex_residue_list(...)`
- `normalize_ligand_name_list(...)`
- `normalize_selection_map(...)`

---

## 5. Manifest

**File:** `docking_app/manifest.py`

The manifest stores docking jobs as TSV rows.

Important helpers:

- `config_to_manifest_values(cfg)`
- `manifest_values_to_config(cols)`
- `append_docking_config_args(args, cfg_raw)`
- `parse_manifest_rows(manifest_path)`
- `write_manifest(path, queue, global_cfg)`
- `build_preview_command(queue, out_root, runs)`
- `normalize_ligand_folder_name(name)`
- `persist_root_run_meta(...)`

Manifest rows represent receptor, chain, ligand, grid, output, and docking settings.

---

## 6. Services

**File:** `docking_app/services.py`

This module contains the backend business logic used by the API routes.

Major responsibilities:

- receptor loading and summarization
- ligand file management
- workspace selection
- gridbox parsing and creation
- queue building and validation
- run startup and lifecycle management
- results scanning and PLIP report parsing
- run script generation

---

## 7. Sessions

**File:** `docking_app/sessions.py`

Run sessions are stored under `DOCK_DIR/_run_sessions/index.json`.

The session layer supports:

- loading stored sessions
- registering new sessions
- scanning recent incomplete runs
- preparing a resume queue
- continuing an existing run
- deleting a saved session entry

---

## 8. Core API

The core router covers the main docking workspace.

### App and asset endpoints

- `GET /api/state`
- `POST /api/mode`
- `GET /api/ligands/list`
- `POST /api/ligands/upload`
- `POST /api/ligands/fetch`
- `POST /api/ligands/select`
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

### Grid and queue endpoints

- `POST /api/grid/upload`
- `GET /api/grid`
- `POST /api/queue/build`
- `POST /api/queue/remove_batch`

Practical notes:

- Queue build requires a populated `selection_map` and matching `grid_data`.
- `replace_queue` is supported through the queue build and resume flow.
- Batch IDs are used to target queue slices without rebuilding the entire queue.

### Run lifecycle endpoints

- `POST /api/run/start`
- `POST /api/run/stop`
- `GET /api/run/status`
- `GET /api/run/recent`
- `POST /api/run/recent/prepare`
- `POST /api/run/recent/continue`
- `POST /api/run/recent/delete`

Run start writes a manifest and launches the run script in the background.

---

## 9. Pocket API

The pocket finder routes generate pocket-based gridboxes and status data.

- `POST /api/pockets/run`
- `GET /api/pockets/status`
- `GET /api/pockets/results`
- `GET /api/pockets/file`
- `POST /api/pockets/gridbox`
- `POST /api/pockets/clear`

These routes are used when the agent or the UI needs a pocket-based gridbox instead of a native-ligand or manual grid.

---

## 10. Results API

The results router is used for result browsing and path resolution.

- `POST /api/results/scan`
- `GET /api/results/dock-folders`
- `POST /api/results/detail`
- `GET /api/results/file`
- `POST /api/paths/resolve`

Result routes are path-sensitive and should always be fed workspace-aware paths.

---

## 11. Report API

The report router covers discovery, rendering, graph generation, and document compilation.

### Listing and metadata

- `GET /api/reports/list`
- `GET /api/reports/preview`
- `GET /api/reports/images`
- `GET /api/reports/root-metadata`
- `GET /api/reports/doc`
- `GET /api/reports/doc-config`
- `POST /api/reports/doc-config`
- `POST /api/reports/root-metadata`

### Cleanup and file serving

- `POST /api/reports/source/delete`
- `POST /api/reports/images/delete-all`
- `POST /api/reports/image/delete`
- `GET /api/reports/image/{path:path}`

### Render and publish

- `POST /api/reports/graphs`
- `POST /api/reports/render`
- `POST /api/reports/render/stop`
- `POST /api/reports/compile`
- `GET /api/reports/status`

Report routes support listing, rendering, graph generation, compilation, and cleanup.

---

## 12. Config API

Docking config documents are supported through the config router.

- `POST /api/config/docking`
- `POST /api/config/save`
- `POST /api/config/load`
- `POST /api/config/update`

Config payloads include:

- `run_count`
- `padding`
- `out_root_path`
- `out_root_name`
- `selection_map`
- `grid_data`
- `docking_config`

---

## 13. Extensions API

The extensions router exposes the local AI and installer surfaces.

### Vina GPU 21

- `GET /api/extensions/vina-gpu-21/status`
- `POST /api/extensions/vina-gpu-21/install`
- `POST /api/extensions/vina-gpu-21/test`
- `POST /api/extensions/vina-gpu-21/uninstall`
- `POST /api/extensions/vina-gpu-21/use-default`
- `POST /api/extensions/vina/use-default`

### Ollama

- `GET /api/extensions/ollama/status`
- `POST /api/extensions/ollama/connect`
- `POST /api/extensions/ollama/models`
- `POST /api/extensions/ollama/offload`
- `POST /api/extensions/ollama/shutdown`
- `POST /api/extensions/ollama/chat`
- `POST /api/extensions/ollama/request-usage`
- `POST /api/extensions/ollama/autonomous-docking`
- `POST /api/extensions/ollama/chat/stream`

### Gemini

- `GET /api/extensions/gemini/status`
- `POST /api/extensions/gemini/save`
- `POST /api/extensions/gemini/models`
- `POST /api/extensions/gemini/chat/stream`

The Ollama extension now reports exact request usage for the current payload and uses a context-sized thinking budget.

---

## 14. Agent Layer

**Files:** `docking_app/agent/autonomous_docking.py`, `docking_app/agent/state_context.py`, `docking_app/extensions/ollama_agent.py`

The local agent is not a fixed macro. It is meant to observe the current state, choose tools, read compact results, and refine its next step.

### Agent-facing tools

- `get_dockup_state()`
- `fetch_assets(receptors, ligands)`
- `inspect_assets()`
- `select_workspace(receptor, chain, native_ligand, dock_ligands)`
- `set_gridbox(method, size, padding, center, pocket_rank)`
- `set_docking_config(engine, mode, run_count, padding, out_root_name, exhaustiveness, num_modes, energy_range, cpu, seed, ph, advanced)`
- `build_or_run_queue(action, replace_queue)`
- `read_tool_details(topic)`
- `delete_ligands(target)`
- `delete_receptors(target)`
- `delete_queue_batches(batch_id)`
- `show_in_viewer(receptor, chain, native_ligand)`
- `show_residues(receptor, residue, chain)`
- `run_agent(...)`

### Agent behavior

- Uses compact state context instead of the full transcript.
- Keeps tool results short and actionable.
- Supports exact request usage previews for the UI.
- Allows queue append mode for multi-config runs.
- Handles gridbox creation from native ligand, current selection, or pocket fallback.
- Uses `num_predict = max(1024, num_ctx // 2)` for the thinking budget.

---

## 15. Frontend / Backend Flow

The UI drives DockUP through a compact sequence:

1. Load state.
2. Fetch receptors and ligands.
3. Select workspace and native ligand when needed.
4. Create or validate the gridbox.
5. Update docking config.
6. Build the queue.
7. Start or resume the run.
8. Scan results and render reports.

The agent and the manual UI both use the same backend state, so the backend must stay consistent after every action.

---

## 16. Known Issues and Notes

Keep this section short and actionable.

- If queue build returns an empty queue, inspect the frontend payload first.
- If a run starts but does not appear in the UI, check the status polling path and the active run registry.
- If results are missing, verify that path resolution is using `WORKSPACE_DIR` instead of `BASE`.
- If session resume fails, inspect the stored paths inside `_run_sessions/index.json`.
- If the agent seems to loop, check the compact working memory and the request usage preview.

---

## 17. Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | HTTP API |
| `uvicorn` | ASGI server |
| `pydantic` | Validation models |
| `pandas` | Tabular data and Excel handling |
| `requests` | Remote PDB fetches |
| `python-docx` | DOCX report generation |
| `python-multipart` | File uploads |

---

Update this document whenever the runtime surface, routes, agent behavior, or state model changes.
