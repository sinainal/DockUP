# Publication figure workflow

Three optional entries in **Reports → Graphs** generate (1) all-run scores and a
summary table, (2) a composite of the three interaction summaries, and (3) a
newly ray-traced close-up matrix. They are unchecked by default because close-up
rendering takes longer than plotting. The existing plot options are unchanged.

The new graph outputs include a 600 dpi PNG, a PDF and an SVG. Graph generation
retains source CSVs, provenance JSON and, for close-ups, individual PNGs, camera
views and editable PyMOL scenes in the adjacent `*_assets` directory. The PDF/SVG
close-up panels embed the actual molecular renders; labels remain vector text.

## Reproducible CLI

Run from the DockUP repository with `.venv/bin/python -m` and one of:

```
figure_scripts.final_plots.publication_scores
figure_scripts.final_plots.publication_interactions
figure_scripts.final_plots.publication_closeups
```

All accept `--root RESULTS_DIRECTORY --out OUTPUT_DIRECTORY`, optionally
`--batch-manifest MANIFEST.tsv`, `--dpi 600`, and `--all-ligands`.
Close-ups also accept `--rerender`. Select a source directory containing the
intended receptor/ligand/run folders. When all five dopamine structures and all
four polymer trimers are present, the default dopamine profile excludes native
ligand controls; `--all-ligands` disables that filter. The batch manifest is the
strongest selection mechanism: missing or unexpected run identities fail the
export rather than silently changing the sample size.

To generate all three figures from a single validated in-memory dataset, use
`figure_scripts.final_plots.publication_suite` with the same arguments. For the
identified 100-run dopamine batch, run this command from DockUP:

```bash
.venv/bin/python -m figure_scripts.final_plots.publication_suite \
  --root docking_app/workspace/data/dock/Manuel_trimer_dopamine_cpu_35_5_run \
  --batch-manifest docking_app/workspace/data/dock/Manuel_trimer_dopamine_cpu_35_5_run/.docking_meta/manifest_20260609_195142_698624.tsv \
  --out '../interim final/publication_stage1/figures'
```

## Definitions

- Scores use the first `REMARK VINA RESULT` in the docked PDBQT. If that file is
  absent, DockUP `results.json` is used. JSON/PDBQT differences above 0.00501
  kcal/mol fail validation. Smaller differences reflect Vina stdout rounding;
  both values and the actual source path are exported.
- Every run appears once. Horizontal offsets separate coincident scores and do
  not change values. Diamonds and error bars are horizontally separated from
  the points. SD is the sample standard deviation (`ddof=1`).
- PLIP XML files are parsed strictly. Missing/malformed reports fail the
  interaction export. A valid empty report contributes zero contacts and is
  included in the denominator. Recurrence counts each residue once per run.
- Common residues occur in **every** run for that receptor–ligand pair. Their
  dominant class is selected by total PLIP contact count. Ties follow DockUP's
  `KIND_ORDER`. The stacked bars count **unique common residues by dominant
  class**, not total atom-level contacts.
- Residues ascend by number, then chain and residue name. No chain is discarded.
- Each close-up selects the score nearest that group's median, breaking ties
  by the lowest run number. Original complex coordinates and PLIP endpoints
  supply the scene; no re-docking or inferred distance contacts are added.
  Camera orientation is optimized per panel, not shared as a structural
  alignment. Contacting side chains are shown; backbone atoms are also retained
  wherever PLIP explicitly places a contact endpoint on a backbone atom.
  SHA-256 signatures prevent reusing a render after source changes.

## Dopamine Stage 1 source

Batch: `Manuel_trimer_dopamine_cpu_35_5_run`, 2026-06-09 19:51:42,
manifest `manifest_20260609_195142_698624.tsv` (100 polymer runs).
The later 150 native-ligand runs in the same folder are excluded.

This batch records a 35 Å grid and Vina exhaustiveness 32. It does **not**
reproduce the score table or 30 Å/exhaustiveness 16 settings in the supplied
Word manuscript. The generated Stage 1 review package records this discrepancy
and does not overwrite that manuscript or present the two datasets as identical.

Validation: `pytest tests/test_publication_figures_unit.py
tests/test_report_render_modes_unit.py -q`. The Stage 1 report builder also
compares all 100 JSON/log scores and docked-pose/complex ligand coordinates.
