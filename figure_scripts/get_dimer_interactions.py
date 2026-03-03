"""
Script to extract top interacting residues for Dimer Docking.
Used to update the report text.
"""
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter
from dataclasses import dataclass

LINKED_ROOT = Path("data/dock/dimer_final_linked")
RECEPTORS = ("D1", "D2", "D3", "D4", "D5")
LIGANDS = ("PET_1", "PS_1", "PP_1", "PE_1")

@dataclass(frozen=True)
class ResidueKey:
    chain: str
    restype: str
    resnr: str
    
    def label(self):
        # Format: TRYP123A
        return f"{self.restype}{self.resnr}{self.chain}"

def parse_plip(xml_path):
    if not xml_path.exists(): return []
    tree = ET.parse(xml_path)
    root = tree.getroot()
    res = set()
    for inter in root.findall(".//bindingsite/interactions//*"):
        rn = inter.findtext("resnr")
        rt = inter.findtext("restype")
        rc = inter.findtext("reschain")
        if rn and rt and rc:
            res.add(ResidueKey(rc, rt, rn))
    return list(res)

def main():
    print("Top Dimer Interactions per Receptor:")
    print("-" * 50)
    
    final_strings = []
    
    for rec in RECEPTORS:
        counter = Counter()
        total_runs = 0
        
        for lig in LIGANDS:
            d = LINKED_ROOT / rec / lig
            for run_dir in d.glob("*"):
                xml = run_dir / "plip" / "report.xml"
                residues = parse_plip(xml)
                counter.update(residues)
                total_runs += 1
        
        # Select top frequent residues (e.g. appearing in >50% of runs or top 5)
        # Sort by count desc, then resnr
        top = sorted(counter.items(), key=lambda x: (-x[1], int(x[0].resnr) if x[0].resnr.isdigit() else 0))
        
        # Filter: keep if freq >= 3 (arbitrary threshold to clean noise)
        # or take top 5
        significant = [k.label() for k, v in top[:5]] 
        
        # Format: RES-RES-RES (D#)
        joined = "–".join(significant)
        print(f"{rec}: {joined}")
        final_strings.append(f"{joined} ({rec})")
        
    print("-" * 50)
    print("Formatted String for Report:")
    print("; ".join(final_strings))

if __name__ == "__main__":
    main()
