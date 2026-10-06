# DockUP molecular workbench

Implemented 2026-10-06. This supersedes the preparation-tab layout proposed in
`modeling_integration_plan_2026-10-06.md`: quantum calculations now have their own
native `/quantum` page. Homology is a dialog in the Docking receptor section.
Existing docking/redocking, results, figures and research reports are unchanged.

## Layout and workflow

One page, four stage tabs, one shared NGL viewer, library, result table and queue:

```text
Build → Poses (optional) → Calculate → selected geometry → Docking
             Experiments orchestrates a matrix and optional xTB screen

Docking → Receptors → Homology → template → reviewed alignment → model
                                                   ↓
                                    assessment → Add to receptors
```

1. Build PE/PET/PP/PS oligomers or 2 × 2 surface proxies; alternatively build
   dopamine microstates or a custom SMILES/ChEMBL molecule. Choose a seed and
   conformer count. Custom input protonation is not inferred from the pH label.
2. Poses combines a saved polymer with dopamine. The inherited sampler is
   dopamine-specific, not a generic hormone or receptor docking engine.
3. Select library structures and Calculate with xTB, PySCF, ORCA or CREST.
   Unsupported engine/calculation combinations are rejected, not simulated.
4. Open a job and explicitly select **original**, **xtb_optimized** or
   **dft_optimized**. **Use for next calculation** saves that geometry under a
   new model ID; choose this to run DFT on xTB-optimized coordinates. A DFT
   single point otherwise uses the selected library model's coordinates.
5. **Send this geometry to Docking** exports a content-addressed file. SDF
   bond orders, formal charges and atom identity are preserved through XYZ
   optimization. Disconnected surface/adsorbate complexes are deliberately
   rejected as single Vina ligands. DockUP preparation remains separate.
6. Experiments accepts fresh composition/length matrices, selected saved
   polymers, or the opened pose set. Turn off Generate surface poses for a
   model-only matrix. Representative poses are the deterministic first N,
   not selected retrospectively for favorable energies.

NGL provides ball-and-stick, licorice, spacefill and line modes, fit/spin and
an overlay of up to 16 selected structures. Overlays use a shared coordinate
frame, not an automatic structural alignment. Desktop and mobile layout are
responsive; large tables scroll within their cards.

## Available providers and scientific boundaries

| Provider | Implemented protocol | Local verification |
| --- | --- | --- |
| RDKit | ETKDGv3 with converged MMFF/UFF conformer selection | Fresh PE and water; polymer–dopamine pose generation |
| xTB | GFN2, ALPB water or gas; optimization/SP; full or fixed-surface; frozen-fragment cycle | Actual PE optimization and three-component interaction cycle |
| PySCF | PBE0-D3(BJ)/def2-TZVP, density fitting, PCM water or gas; SP/cycle | Actual water SP with positive SCF convergence |
| ORCA | r2SCAN-3c, CPCM water or gas; Opt/SP/Freq/cycle | Adapter implemented; scientific executable not installed/configured, so not execution-tested |
| CREST | Saved complex pose, fixed-polymer constrained GFN-FF quick refinement | Executable/version probe; full refinement not claimed as tested here |
| ProMod3 | Chain-aware template reconstruction, optional official reconstruction/minimization pipeline | Actual single-chain mutation smoke model; ProMod3 3.4.0 / OST 2.5.0 |
| PSP / Polyply | Existing Studio optional builders | Unavailable until their dependencies are configured |

The system never identifies `/usr/bin/orca` as chemistry ORCA: that installation
is the GNOME screen reader. Configure the scientific executable explicitly.
PySCF and ORCA protocols are different and must not be pooled as equivalent.
PySCF does not currently provide optimization or frequency in this integration.

Interaction energy is computed from the same supplied complex geometry:
`E(complex) − E(polymer fragment) − E(adsorbate fragment)`, using a common engine
and solvent protocol and the mapped fragment charges. It is not a binding free
energy, Kd or biological activity. PySCF counterpoise/basis sensitivity is not
implemented; ORCA r2SCAN-3c contains its own gCP correction. Raw total energies
of unequal compositions are not an affinity ranking. The UI keeps total energy
and interaction energy in separate columns and records incomplete cycles as
failures rather than emitting a numeric interaction energy.

Homology accepts one-model PDB/mmCIF templates and explicit target chains,
template chain mappings, amino-acid sequences, subtype labels and numbering
starts. Chain name B does not imply a B-type subunit. Global linear-gap alignment
is an initial preview, not a substitute for expert review of divergent loops;
reviewed explicit alignments can be provided. Runtime identity/coverage cutoffs
are workflow safeguards, not universal scientific acceptance thresholds.

Model outputs contain sequence checks, peptide-link distances, severe
inter-chain proximity pairs, residue mapping, template hash and reconstruction
diagnostics. These are **basic geometric checks**, not comprehensive structural
validation. MolProbity/QMEAN/MD/experimental validation are not silently claimed.
Adding a receptor does not automatically select its binding pocket or grid.

## Durable execution and provenance

SQLite stores queued/running/succeeded/partially-succeeded/failed/cancelled/
timed-out/interrupted states. One compute worker runs at a time. Each job has an
isolated input snapshot, hashes, protocol, code fingerprints, source backend
fingerprints, events, raw logs and indexed/checksummed downloadable artifacts.
Model library inputs are never overwritten by an optimization or retry.

Cancel terminates the dedicated compute process group. A server shutdown marks
its running jobs interrupted and terminates its compute groups; queued jobs
can recover on restart. Retry creates a new job and records its parent: it is
not an engine checkpoint resume. Per-calculation timeouts bound SCF work; the
whole-job deadline is capped at 24 hours. Multi-stage interaction cycles contain
three calculations, each with its own timeout.

The default library is `docking_app/workspace/data/modeling` (resolved through
DockUP's DATA_DIR). Set `DOCKUP_MODELING_DATA_DIR` for a separate workspace.
Historical Studio models are copied only when explicitly selected in the
archive importer. Historical optimized results are not silently relabeled as
new calculations; imported library coordinates are the original SDF geometry.

## Configuration

Run DockUP normally and visit `/quantum`; the homology button is in Receptors.
The UI and CLI use the same native `/api/modeling/*` routes and schemas.

```bash
.venv/bin/python -m pip install '.[docking,quantum]'
.venv/bin/python -m uvicorn docking_app.app:app --host 127.0.0.1 --port 8000
```

Environment overrides:

| Variable | Purpose |
| --- | --- |
| DOCKUP_STUDIO_SOURCE | Directory containing Studio `__init__.py`, `builder.py`, `xtb.py`, etc. |
| DOCKUP_MODELING_DATA_DIR | Isolated job database/library/output root |
| DOCKUP_MODELING_PYTHON | Worker interpreter with Studio/RDKit and desired optional DFT dependencies |
| XTB_BIN / CREST_BIN | Existing engine overrides, honored before local archive fallbacks |
| DOCKUP_ORCA_BIN | Explicit scientific ORCA native executable |
| DOCKUP_PROMOD3_ROOT | Installed local OpenStructure/ProMod3 bundle root |
| DOCKUP_HOMOLOGY_PYTHON | Interpreter for the OpenStructure runtime |

Current local fallbacks locate Studio next to the docking workspace, packaged
xTB/CREST in the private Studio archive and ProMod3 under
`serotonin/software/promod3_ubuntu_local`. These are local adapters, not binaries
redistributed in the DockUP package. Other computers must configure their own
installations. Studio code/licensing and ORCA license requirements remain
separate; the integration does not assume redistribution rights.

## CLI

From the DockUP checkout with the server running:

```bash
.venv/bin/python -m docking_app.cli modeling status
.venv/bin/python -m docking_app.cli modeling build --payload '{"polymer":"PE","repeats":3,"conformers":6}' --wait
.venv/bin/python -m docking_app.cli xtb --model-id mdl_ID --threads 2 --seconds 300
.venv/bin/python -m docking_app.cli dft --engine pyscf --type singlepoint --model-id mdl_ID
.venv/bin/python -m docking_app.cli modeling show job_ID
.venv/bin/python -m docking_app.cli modeling derive job_ID --artifact 3
.venv/bin/python -m docking_app.cli modeling publish job_ID --artifact 3
.venv/bin/python -m docking_app.cli modeling cancel job_ID
.venv/bin/python -m docking_app.cli modeling retry job_ID
.venv/bin/python -m docking_app.cli modeling export --output quantum_results.csv
.venv/bin/python -m docking_app.cli modeling archive --source private
.venv/bin/python -m docking_app.cli homology template /absolute/path/template.pdb
.venv/bin/python -m docking_app.cli homology alignment --file homology_request.json
.venv/bin/python -m docking_app.cli homology build --file homology_request.json
```

IDs and artifact indices above are placeholders: use the job/model response,
not a hardcoded example index. `modeling poses`, `quantum` and `experiment` accept
`--payload` or `--file`. `--base-url` is before the modeling subcommand, e.g.
`modeling --base-url http://127.0.0.1:8127 status`. CLI waiting can time out without
cancelling a tracked server job. The UI shows the JSON-equivalent CLI command.

## Reproducible checks

```bash
.venv/bin/python -m pytest tests/test_modeling_workbench.py -q
node --check docking_app/static/quantum.js
node --check docking_app/static/homology.js
```

`scripts/modeling_browser_smoke.py` drives local Chrome through CDP. Use an
isolated `DOCKUP_MODELING_DATA_DIR` test server: it creates fresh PE/xTB jobs,
derives optimized geometry, checks NGL, desktop/mobile overflow and the
homology template/alignment UI. It does not modify manuscripts.

## Original feature inventory and remaining extensions

See `nanoplastic_studio_feature_inventory.md` for source-by-source mappings and
explicitly retained limitations. Original arbitrary composed multi-work-item
experiment plans and private Pilot2-specific bootstrap/rank/length graphics
are not reproduced as generic validated analyses. The native screen currently
provides a single matrix/saved-input experiment per job and filterable raw
results/CSV. Generic matched-cohort statistics, automated template search,
full homology quality-assessment adapters and QM/MM remain separate extensions.
