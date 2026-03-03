"""
Orchestrator for Dimer Docking Analysis
Links dimer data to a standard structure and runs plot generation.
"""
import sys
import os
import shutil
import subprocess
from pathlib import Path

# --- Configuration ---
SOURCE_ROOT = Path("data/dock/dimer_full")
LINKED_ROOT = Path("data/dock/dimer_final_linked") # Standardized structure
OUTPUT_PLOTS = Path("plots/dimer")

# Correct Mapping (Verified)
PDB_TO_D = {
    "7X2F": "D1",
    "6CM4": "D2",
    "3PBL": "D3",
    "5WIU": "D4",
    "8IRV": "D5",
}

LIG_MAP = {
    "ethylene_terephthalate_dimer": "PET_1",
    "styrene_dimer": "PS_1",
    "propylene_dimer": "PP_1",
    "ethylene_dimer": "PE_1",
}

def setup_linked_data():
    """Symlink raw dimer folders into D#/LIG/runX structure."""
    if LINKED_ROOT.exists():
        shutil.rmtree(LINKED_ROOT)
    LINKED_ROOT.mkdir(parents=True, exist_ok=True)
    
    if not SOURCE_ROOT.exists():
        print(f"Error: Source root {SOURCE_ROOT} not found!")
        sys.exit(1)

    count = 0
    # Expected folder format: PDB_ligandname_runX
    for run_dir in SOURCE_ROOT.iterdir():
        if not run_dir.is_dir(): continue
        
        parts = run_dir.name.split("_")
        # heuristic: PDB is parts[0], runX is parts[-1], middle is ligand
        if len(parts) < 3: continue
        
        pdb = parts[0]
        run_tag = parts[-1]
        lig_raw = "_".join(parts[1:-1])
        
        d_label = PDB_TO_D.get(pdb)
        short_lig = LIG_MAP.get(lig_raw)
        
        if not d_label or not short_lig:
            # print(f"Skipping unknown: {run_dir.name} (PDB={pdb}, Lig={lig_raw})")
            continue
            
        # Target: LINKED_ROOT/D1/PET_1/runX
        target = LINKED_ROOT / d_label / short_lig / run_tag
        target.parent.mkdir(parents=True, exist_ok=True)
        
        os.symlink(run_dir.absolute(), target)
        count += 1
        
    print(f"Linked {count} runs to {LINKED_ROOT}")

def run_plots():
    """Run the 4 plot scripts against the linked data."""
    scripts = [
        "figure_scripts.final_plots.interacted_residue_plots",
        "figure_scripts.final_plots.interaction_plots",
        "figure_scripts.final_plots.affinity_variants",
        "figure_scripts.final_plots.common_residue_interactions"
    ]
    
    OUTPUT_PLOTS.mkdir(parents=True, exist_ok=True)
    
    for script_mod in scripts:
        print(f"\n>>> Running {script_mod}...")
        cmd = [
            sys.executable, "-m", script_mod,
            "--root", str(LINKED_ROOT),
            "--out", str(OUTPUT_PLOTS)
        ]
        res = subprocess.run(cmd, capture_output=False)
        if res.returncode != 0:
            print(f"!!! Failed: {script_mod}")

def main():
    print("--- Dimer Analysis Orchestrator ---")
    setup_linked_data()
    run_plots()
    print("\n--- Done. Plots in plots/dimer/ ---")

if __name__ == "__main__":
    main()
