# DockUP Agent System Plan

## Scope

DockUP Local AI is a docking-focused assistant. The first release is intentionally simple: connect to a local Ollama model, read a compact DockUP state summary, and answer user questions without taking actions.

## Short Term

- Build a strong, safe foundation for local AI in DockUP.
- Keep the agent system modular under `docking_app/agent`.
- Show every local Ollama model as a selectable model card.
- Warm up the selected model with clear loading feedback.
- Keep tool calling disabled.
- Pass only a compact docking state context: selected receptor, selected ligand, active ligands, queue count, run status, output root, and docking config.
- Make the UI feel professional and calm: extension setup in the Extensions popup, then a bottom-right assistant launcher when Ollama is connected.

## Medium Term

- Add function calling for docking workflows after the safe foundation is stable.
- Candidate tools:
  - fetch ligand by name or PubChem id
  - load receptor by PDB id
  - select receptor chain and ligand
  - write docking config
  - build queue
  - start/stop docking with explicit confirmation
  - summarize results
- Split responsibilities into small docking sub-agents only if the workflows become complex:
  - receptor setup agent
  - ligand setup agent
  - config agent
  - run supervisor
  - result interpreter
- Keep every action schema-validated and auditable.

## Long Term

- Build a multi-agent autonomous docking system that can plan, execute, monitor, and summarize complete studies.
- Support multiple coordinated sub-agents with bounded permissions and shared run memory.
- Add project-level memory for receptors, ligands, failed runs, preferred configs, and report style.
- Add safety gates for GPU-heavy work, destructive file operations, long jobs, and external downloads.
- Move from single-run help toward full autonomous experiment orchestration.

## Non-Goals For The First Release

- No molecule fetching by AI.
- No docking start by AI.
- No config mutation by AI.
- No shell execution by AI.
- No research/report analysis agent coupling.
