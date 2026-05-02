# DockUP Agent System Plan

## Goal

DockUP Local AI should behave like a compact operation agent, not a fixed docking macro. The same local model observes DockUP state, chooses one tool call, reads the compact result, then decides the next step.

This is tuned for local models such as Gemma 4 26B: few tools, flat arguments, short results, and detailed instructions only on demand.

## Model-Visible Tools

1. `get_dockup_state()`
   - Returns compact loaded assets, selections, gridbox readiness, queue job count, run status, and allowed next tools.

2. `fetch_assets(receptors, ligands)`
   - Fetches/loads receptor PDB IDs and ligand specs.
   - Ligands are semicolon-separated strings.
   - Supports explicit polymer/oligomer forms such as `ethylene[1,3]`, meaning monomer and trimer.
   - If a direct ligand name fails, backend retries simple name variants before reporting failure.

3. `inspect_assets()`
   - Returns compact receptor chain/native-ligand inventory and active ligand filenames.
   - Does not expose PDB text, atom lists, molecule content, or long file paths.

4. `select_workspace(receptor, chain, native_ligand, dock_ligands)`
   - Selects receptor, chain, native ligand for grid choice, and dock ligands.
   - Updates DockUP state and viewer-facing selection.
   - Use `auto` for chain/native ligand when the model should choose from inspection results.
   - Use `all` for simple multi-receptor or all-ligand jobs.

5. `set_gridbox(method, size, padding, center)`
   - Sets gridbox from `native_ligand`, `current_selection`, or `manual`.
   - Backend computes coordinates; the model should not calculate atom centroids.
   - `center` is only for manual `x,y,z`.

6. `set_docking_config(engine, mode, run_count, padding, out_root_name, exhaustiveness, num_modes, energy_range, cpu, seed, ph, advanced)`
   - Controls docking settings.
   - `run_count` means repeats per receptor-ligand job.
   - `job_count` means receptor x ligand combinations.
   - Rare settings go into `advanced` as `key=value;key=value`.

7. `build_or_run_queue(action)`
   - `build_only`: validate and build queue.
   - `build_test`: build and materialize/test without heavy docking.
   - `run_full`: start the real run.

8. `read_tool_details(topic)`
   - Optional detail reader so normal context stays small.
   - Topics: `workflow`, `ligand_ranges`, `asset_resolution`, `workspace`, `gridbox`, `settings`, `setting_catalog`, `counts`, `queue_actions`, `tools`.

## Agent Policy

- Always prefer one tool call at a time.
- Start with `get_dockup_state` unless a tool result just returned enough state.
- Never invent loaded files, receptors, ligands, chains, native ligands, gridboxes, queue rows, or run status.
- Fetch missing assets before inspecting.
- Inspect assets before choosing chain/native ligand unless the state is already known.
- Use native ligand grid centers when biologically meaningful.
- Use `read_tool_details` only when the model needs extra rules for settings, polymer ranges, counts, queue actions, or tool usage.
- Default to standard Vina GPU, `run_count=1`, `padding=0`, and `build_test` unless the user explicitly asks for a full run.

## Compact Context Rule

Tool calls may return full data to backend trace/debug, but the model-visible tool message must stay compact:

- summaries
- loaded receptor IDs
- saved ligand filenames
- short failure lists
- short chain/native ligand lists
- selected workspace rows
- gridbox centers/sizes
- compact config and validation counts
- allowed next tools

Never put these into normal model context:

- full PDB text
- atom lists
- raw molecule content
- full queue rows
- long logs
- report payloads
- large inventories

## UI Display

Tool activity should be shown as plain professional blocks:

```text
Function call
fetch_assets(receptors="5MOZ", ligands="aspirin;ethylene[1,3]")

Result
Loaded 1 receptor(s), saved 3 ligand file(s).
```

Avoid colorful progress chips or toy-like status cards. Raw debug can be kept for expandable developer views later, but the default chat should show compact plain text.
