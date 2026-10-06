# Nanoplastic Modeling Studio: inspected feature inventory

2026-10-06. Sources inspected read-only:

- `/home/sina/Downloads/nanoplastic-modeling-studio`: general application,
  top-level Python package files and `static/` screens.
- `/home/sina/Downloads/nanoplastic-modeling-studio-private/modeling/app`:
  application plus study-specific analysis/batch scripts.
- Private DFT research plan: a planned workflow, not a implemented DFT runner.
- DockUP native FastAPI/vanilla-JS/NGL UI and existing receptor/ligand transfer.

Neither example application, historical model library nor research manuscript
was modified. Functions below are grouped by externally visible behavior;
private study scripts are explicitly separated from product capabilities.

| Original source / behavior | DockUP implementation / decision |
| --- | --- |
| `catalog.py`, `/api/catalog`: PE/PP/PET/PS, repeat chemistry, dopamine states, parameter/engine catalog | Build controls + capabilities API; actual catalog limits used (PE ≤18, others ≤15) rather than broader API declaration |
| `builder.py`: ETKDG conformers, converged MMFF/UFF selection, deterministic seed, formula/atoms/charge/validation/warnings/version records | Same source backend through isolated adapter; native Build UI, models API, CLI |
| Single oligomer or disconnected 2 × 2 surface proxy | Build model-level choice; disconnected proxies not exported as one docking ligand |
| Dopamine four microstates / pH-selected representative | Explicit microstate controls; pH documented as selection/metadata, not pH simulation |
| Custom SMILES or ChEMBL ID/name | Native Build fields; mutually exclusive inputs; supplied protonation retained |
| `external_engines.py`: optional PSP/Polyply | Capability-driven controls; unavailable dependencies disabled; no fake fallback claimed as that engine |
| Model storage, list/detail, PDB/SDF/XYZ download | Immutable native library, metadata panel, file links, explicit archive import |
| `pose_sets.py`: surface scan/systematic/CREST-assisted/manual-seed strategy, SASA/probe/distance/sites/orientations/seed | Native Poses form and reusable CLI; inherited strategy semantics, not new manual-coordinate editor |
| Dopamine orientation templates (ring/amine/catechol), surface atom mapping | Preserved; sampler not offered for custom molecules or silently generalized to serotonin |
| Persisted child pose models and pose-set manifests | Library + saved-set selector; import selected set members into Calculate |
| NGL overlay, pose paging, color/representation changes | Shared NGL viewer, library scrolling/filtering, overlay ≤16, fit/spin, four representations; no automatic superposition |
| `xtb.py`: GFN2 optimization/SP, ALPB/gas, charge/UHF, fixed-surface/full, timeout, logs/results | Native Calculate UI/API/CLI, real convergence tests, bounded worker execution |
| Optimized XYZ → display PDB | Added topology-preserving optimized SDF/PDB/XYZ with atom-order/element/finite-coordinate/bond/stereochemistry guards |
| Batch xTB and job polling | Shared durable SQLite queue, member results, cancellation, independent retry history, explicit partial-failure state |
| `crest.py`: constrained fixed-polymer GFN-FF quick refinement, compatible CREST 2.12 choice, top-k coordinate variants | Optional Calculate provider; saved pose maps retained; logs/variants available; full execution test still pending |
| `experiments.py`: saved plan/title/stage/notes | Native Experiment request is persisted with job inputs/events/results; prior plans can be inspected and retried |
| Model matrix / pose generation / end-to-end / saved surfaces / saved pose sets / xTB-ready stages | Fresh matrix, saved polymers or opened pose set; model-only toggle, pose-only or optional xTB progression |
| Arbitrary composed multi-work-item plan (`api.py` `_run_composed_experiment`) | Not ported wholesale: one native experiment per job; multiple jobs can be queued; this is an explicit remaining extension |
| Raw analysis: model/polymer/status search, energy/gap/runtime/atoms, optimized viewer, result/log download | Native result/library filtering, protocol/solvent/charge/atoms/runtime metadata, raw logs and CSV; HOMO–LUMO values remain in xTB raw result when available, no dedicated gap chart |
| Private `/api/analysis/cohorts`: Pilot2-specific medians/IQR/bootstrap/QC/ranks and length graphics | Identified as historical study-specific logic; not copied into a generic screen or populated with invented results |
| Original in-memory ThreadPool job dictionaries | Replaced with durable job database and snapshots; restart/interruption/cancel lineage retained |
| Original xtb/pose/crest CLI | Reusable native modeling/xtb/dft/homology CLI, same schemas as UI, status/show/cancel/retry/export/derive/publish/archive |
| `refined_pilot.py`, `pilot2.py`, `analyze_pilot246.py`, `complete_pilot.py`, `extend_pilot_pet9.py` | Study drivers, historical cohort IDs and filesystem assumptions; inventoried, not run or redistributed as universal methods |
| `verify_fresh_flow.py`, `test_refined_pilot.py`, browser smoke | New isolated integration tests and native browser checks, without rerunning old cohorts |
| DFT proposed in private research plan | New actual PySCF provider + explicit ORCA provider, separate methods, convergence and charge/spin checks |
| Homology absent from the Studio product | New native Docking dialog and ProMod3 provider with chain/subtype separation, reviewed alignment, residue mapping and basic assessment |

## Deliberate improvements

- Optimizing a model no longer overwrites the library geometry or makes an
  initial `structure.sdf` look optimized. An explicit geometry is derived into
  a new model before another calculation.
- Jobs are not marked successful merely because a batch loop ended.
- A fragment interaction energy requires three successful matched calculations;
  incompatible protocols or raw total energies are not silently compared.
- Raw inputs, code fingerprints and artifact hashes are retained separately
  from docking preparation; export requires an explicit geometry choice.
- The NGL renderer ignores stale asynchronous loads so a quick geometry switch
  cannot overwrite the newly selected structure's labels/actions.
- Scientific ORCA is never inferred from an unrelated executable of that name.
- Geometric homology assessment is not presented as experimental or full
  MolProbity/QMEAN validation.

Implementation and usage: `modeling_workbench.md`. Remaining extensions are
listed there; this inventory is not a claim that every private research script
or original study-specific chart has been integrated or validated.
