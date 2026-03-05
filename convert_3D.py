from __future__ import annotations

from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdDistGeom, rdForceFieldHelpers, rdPartialCharges


def build_oligomer_smiles(monomer_smiles: str, count: int) -> str:
    monomer = str(monomer_smiles or "").strip()
    if not monomer:
        raise ValueError("Monomer SMILES is empty.")

    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(10, n))

    sequential = monomer * n
    if Chem.MolFromSmiles(sequential) is not None:
        return sequential

    dotted = ".".join([monomer] * n)
    if Chem.MolFromSmiles(dotted) is not None:
        return dotted

    return sequential


def smiles_to_3d_sdf(smiles: str, output_sdf_file: str | Path) -> str:
    smiles_text = str(smiles or "").strip()
    if not smiles_text:
        raise ValueError("SMILES is empty.")

    mol = Chem.MolFromSmiles(smiles_text)
    if mol is None:
        raise ValueError("Invalid SMILES provided.")

    mol = Chem.AddHs(mol)

    params = rdDistGeom.ETKDGv3()
    params.useRandomCoords = True
    embed_status = rdDistGeom.EmbedMolecule(mol, params)
    if embed_status < 0:
        raise RuntimeError("Failed to generate 3D coordinates from SMILES.")

    rdPartialCharges.ComputeGasteigerCharges(mol)
    rdForceFieldHelpers.UFFOptimizeMolecule(mol, maxIters=1000)

    output_path = Path(output_sdf_file).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = Chem.SDWriter(str(output_path))
    writer.write(mol)
    writer.close()
    return str(output_path)


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Convert SMILES to 3D SDF.")
    parser.add_argument("--smiles", required=True, help="Input SMILES string")
    parser.add_argument("--out", required=True, help="Output SDF file path")
    args = parser.parse_args()

    path = smiles_to_3d_sdf(args.smiles, args.out)
    print(path)


if __name__ == "__main__":
    _main()

