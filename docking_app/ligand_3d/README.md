# Ligand 3D Prototype

This folder contains a standalone prototype app for:

- manual ligand search from ChEMBL + PubChem (default limit: 5)
- selecting results into a builder queue
- generating monomer/oligomer (`1-10` copies: monomer..decamer) SDF files
- previewing generated SDF in NGL viewer
- staging selected files into local prototype ligands folder

## Run

From repository root:

```bash
uvicorn ligand_3d.app:app --reload --port 8090
```

Open:

```text
http://127.0.0.1:8090
```

## Notes

- Generated files are saved under `ligand_3d/generated/`.
- "Add to ligands" copies files into `ligand_3d/ligands/` only (prototype staging).
- ChEMBL API is queried via `https://www.ebi.ac.uk/chembl/api/data`.
- PubChem API is queried via `https://pubchem.ncbi.nlm.nih.gov/rest`.
- This is intentionally separate from the main Docking App UI for quick iteration.
