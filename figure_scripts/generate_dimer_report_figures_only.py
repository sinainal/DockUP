"""
Dimer Report Generator (FIGURES ONLY)
Creates 'dimer_report.docx' from 'report_DOPAMINE.docx'.

CRITICAL: 
- NO TEXT REPLACEMENT.
- ONLY IMAGE REPLACEMENT.
"""
import sys
from pathlib import Path
from docx import Document

# --- Configuration ---
SOURCE_DOC = Path("report_DOPAMINE.docx")
OUT_DOC = Path("dimer_report.docx")

DIMER_ALL = Path("dimer_all/dimer")
PANELS_DIR = DIMER_ALL / "panels"

# Image Mapping
IMAGE_MAP = {
    "image2.png": DIMER_ALL / "affinity_boxplot.png",
    "image3.png": PANELS_DIR / "7X2F.png", # D1
    "image4.png": PANELS_DIR / "6CM4.png", # D2
    "image5.png": PANELS_DIR / "3PBL.png", # D3
    "image6.png": PANELS_DIR / "5WIU.png", # D4
    "image7.png": PANELS_DIR / "8IRV.png", # D5
    "image8.png": DIMER_ALL / "run_frequency_heatmap.png",
    "image9.png": DIMER_ALL / "common_residue_heatmap.png",
    "image10.png": DIMER_ALL / "interaction_stacked_bar.png",
}

def replace_images_by_filename(doc):
    """Replaces image blobs identified by internal filename."""
    print("Scanning images...")
    replaced = 0
    
    for rel in doc.part.rels.values():
        if "image" in rel.target_ref:
            target_ref = rel.target_ref
            
            # Check map
            found_match = False
            for filename, new_path in IMAGE_MAP.items():
                if target_ref.endswith(filename):
                    found_match = True
                    if new_path.exists():
                        print(f"Replacing {filename} with {new_path.name}")
                        with open(new_path, "rb") as f:
                            new_bytes = f.read()
                        rel.target_part._blob = new_bytes
                        replaced += 1
                    else:
                        print(f"Warning: Missing {new_path}")
                    break
            
    print(f"Replaced {replaced} images.")

def main():
    if not SOURCE_DOC.exists():
        print(f"Error: {SOURCE_DOC} missing.")
        return

    doc = Document(SOURCE_DOC)
    
    # NO TEXT REPLACEMENT CALL
    
    replace_images_by_filename(doc)
    
    doc.save(OUT_DOC)
    print(f"--- Saved {OUT_DOC} ---")

if __name__ == "__main__":
    main()
