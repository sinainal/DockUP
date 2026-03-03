"""
Script to calculate and print Dimer docking statistics.
"""
import statistics
import json
import math
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple

# Re-use config
RECEPTORS = ("D1", "D2", "D3", "D4", "D5")
LIGANDS = ("PET_1", "PS_1", "PP_1", "PE_1")
LINKED_ROOT = Path("data/dock/dimer_final_linked")

@dataclass
class Obs:
    receptor: str
    ligand: str
    affinity: float

def get_affinity(path: Path) -> float | None:
    try:
        data = json.loads(path.read_text())
        val = data.get("best_affinity") or list(data.values())[0].get("best_affinity")
        return float(val)
    except:
        return None

def main():
    obs = []
    for rec in RECEPTORS:
        for lig in LIGANDS:
            d = LINKED_ROOT / rec / lig
            if not d.exists(): continue
            for run in d.glob("*"):
                res = run / "docking" / "results.json"
                if not res.exists():
                    res = run / "results.json"
                if res.exists():
                    v = get_affinity(res)
                    if v is not None:
                        obs.append(Obs(rec, lig, v))
    
    # Calculate stats
    print(f"{'Rec':<5} {'Lig':<10} {'Mean':<10} {'SD':<10}")
    print("-" * 40)
    for rec in RECEPTORS:
        for lig in LIGANDS:
            vals = [o.affinity for o in obs if o.receptor == rec and o.ligand == lig]
            if vals:
                mean = np.mean(vals)
                sd = statistics.stdev(vals) if len(vals)>1 else 0
                print(f"{rec:<5} {lig:<10} {mean:<10.2f} {sd:<10.2f}")

if __name__ == "__main__":
    main()
