# Modeling workbench verification — 2026-10-06

Isolated job data: `output/modeling_verification_20261006`.
Browser evidence: `output/modeling_browser_20261006/browser_audit.json` and PNGs.
Original Studio repositories remained clean; frozen dopamine/serotonin reports
and result sets were not edited or rerun.

## Executed checks

- 47 tests passed: new modeling integration plus existing ligand popup,
  receptor metadata, ligand naming and live CLI regression tests.
- Real RDKit PE dimer generation and real GFN2-xTB optimization completed.
  The optimized SDF hash matched the selected job artifact; the original
  library SDF hash was unchanged after deriving the optimized geometry.
- Real PE–dopamine pose generation and an actual three-component xTB cycle
  completed. The reported interaction energy equaled the code-checked
  subtraction of the complex and frozen fragment energies.
- Actual water PySCF PBE0-D3(BJ)/def2-TZVP PCM SP converged. This proves provider
  execution and parsing, not interaction accuracy or an affinity benchmark.
- ProMod3 built a 46-residue, single-mutation 1CRN smoke model; sequence
  matched, no outstanding gaps, no abnormal peptide links or severe
  inter-chain proximity pairs. ProMod3 3.4.0 / OST 2.5.0. Template CA
  displacement RMS was 0.052772 Å. This is not validation of a new receptor
  assembly or a comprehensive quality assessment.
- Native browser: fresh PE build → xTB optimization → optimized artifact →
  new library geometry for the next calculation; NGL rendering; desktop
  1440 × 1080 and mobile 390 × 844 without horizontal document overflow;
  homology template upload and alignment preview; no uncaught runtime errors.
- Python compilation, JS syntax checks and `git diff --check` passed.
- Live CLI capability request identified xTB, PySCF and ProMod3; ORCA and
  optional PSP/Polyply correctly reported unavailable.

## Explicitly not execution-validated

Scientific ORCA jobs, full CREST refinement, optional PSP/Polyply builders,
multi-chain receptor reconstruction, MolProbity/QMEAN/MD, generic cohort
statistics and biological affinity. Their absence is not disguised as a
successful check. Original study-specific statistical graphics and arbitrary
multi-work-item experiment composition remain documented extensions.

## Issues found and handled during testing

- Programmatic form submission can have no `submitter`: safe submit-button
  fallback added.
- A partial live JSONL event line must not break job polling: incomplete line
  ignored until the next read.
- Engine name alone is insufficient to identify chemistry ORCA: explicit
  executable configuration and scientific banner check.
- PySCF PCM wrapper must attach to the SCF method: native method.PCM() used.
- NGL loads/job requests can arrive out of order: stale responses cannot
  overwrite the selected geometry or its actions.
- Overlay uniform color must not override element colors on a single model.
- Optimized XYZ is not a chemical topology source: original SDF graph retained
  with element/count/order/finite-coordinate/bond/stereochemistry guards.
- CREST needs mapped surface/adsorbate dependencies: snapshots include them;
  variant sets are forked rather than overwriting the original library set.
- Total electronic energies and interaction energies are distinct quantities:
  separate table columns and interpretation warnings.
- Advanced homology assessment was not run: output explicitly distinguishes
  basic checks from comprehensive structural validation.
