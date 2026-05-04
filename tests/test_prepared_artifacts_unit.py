from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from docking_app.prepared_artifacts import install, install_receptor_input, plan, plan_receptor_input
from docking_app.services import _parse_results_folder
from scripts.run_multi_ligand import _stage_output_path


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    receptor = tmp_path / "6CM4_rec_raw.pdb"
    receptor.write_text(
        "ATOM      1  C   ALA A   1       1.000   2.000   3.000  1.00  0.00           C\nEND\n",
        encoding="utf-8",
    )
    ligand = tmp_path / "lig.sdf"
    ligand.write_text("lig\n  DockUP\n\n  0  0  0  0  0  0            999 V2000\nM  END\n$$$$\n", encoding="utf-8")
    grid = tmp_path / "grid.txt"
    grid.write_text(
        "\n".join(
            [
                "center_x = 1.0",
                "center_y = 2.0",
                "center_z = 3.0",
                "size_x = 25.0",
                "size_y = 25.0",
                "size_z = 25.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return receptor, ligand, grid


def test_prepared_artifact_store_hits_across_runs_without_duplicate_ids(tmp_path: Path) -> None:
    receptor, ligand, grid = _write_inputs(tmp_path)
    prepared_root = tmp_path / "_prepared"

    first = plan(
        prepared_root=prepared_root,
        pdb_id="6CM4",
        chain="A",
        ligand_resname="LIG",
        receptor_pdb=receptor,
        ligand_sdf=ligand,
        grid_file=grid,
    )
    assert first["cache_hit"] == {"receptor": False, "ligand": False, "grid": False}

    generated = tmp_path / "generated"
    generated.mkdir()
    rigid = generated / "6CM4_receptor.pdbqt"
    ligand_pdbqt = generated / "6CM4_ligand.pdbqt"
    ligand_fixed = generated / "6CM4_ligand_fixed.sdf"
    rigid.write_text("RECEPTOR\n", encoding="utf-8")
    ligand_pdbqt.write_text("LIGAND\n", encoding="utf-8")
    ligand_fixed.write_text("FIXED\n", encoding="utf-8")

    install(
        first,
        sources={
            "rigid_pdbqt": str(rigid),
            "ligand_pdbqt": str(ligand_pdbqt),
            "ligand_fixed_sdf": str(ligand_fixed),
            "grid_file": str(grid),
        },
    )

    second = plan(
        prepared_root=prepared_root,
        pdb_id="6CM4",
        chain="A",
        ligand_resname="LIG",
        receptor_pdb=receptor,
        ligand_sdf=ligand,
        grid_file=grid,
    )
    assert second["receptor"]["id"] == first["receptor"]["id"]
    assert second["ligand"]["id"] == first["ligand"]["id"]
    assert second["grid"]["id"] == first["grid"]["id"]
    assert second["cache_hit"] == {"receptor": True, "ligand": True, "grid": True}


def test_result_parser_prefers_manifest_metadata_and_prepared_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "6CM4" / "Styrene" / "run2"
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(json.dumps({"run2": {"best_affinity": -7.2, "rmsd": None}}), encoding="utf-8")
    prepared = tmp_path / "_prepared" / "ligands" / "abc"
    prepared.mkdir(parents=True)
    fixed_sdf = prepared / "6CM4_ligand_fixed.sdf"
    fixed_sdf.write_text("SDF\n", encoding="utf-8")
    complex_pdb = run_dir / "6CM4_complex.pdb"
    complex_pdb.write_text("END\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "pdb_id": "6CM4",
        "chain": "A",
        "ligand_name": "Styrene",
        "run_id": 2,
        "prepared_artifacts": {"ligand": {"fixed_sdf": str(fixed_sdf)}},
        "files": {"complex_pdb": str(complex_pdb)},
    }
    (run_dir / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    parsed = _parse_results_folder(run_dir)

    assert parsed is not None
    assert parsed["pdb_id"] == "6CM4"
    assert parsed["chain"] == "A"
    assert parsed["run_id"] == 2
    assert parsed["ligand_display_name"] == "Styrene"
    assert parsed["complex_path"] == str(complex_pdb)
    assert parsed["prepared_artifacts"]["ligand"]["fixed_sdf"] == str(fixed_sdf)


def test_prepared_output_staging_uses_symlink_for_cache_artifacts(tmp_path: Path) -> None:
    prepared_root = tmp_path / "_prepared"
    artifact_dir = prepared_root / "receptors" / "abc"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "6CM4_receptor.pdbqt"
    artifact.write_text("RECEPTOR\n", encoding="utf-8")
    run_dir = tmp_path / "run1"
    target = run_dir / artifact.name

    _stage_output_path(artifact, target, prepared_root=prepared_root)

    assert target.is_symlink() or target.read_text(encoding="utf-8") == "RECEPTOR\n"
    assert artifact.exists()


def test_dock1_uses_prepared_cache_for_receptor_ligand_and_grid(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    receptor_pdb = tmp_path / "6CM4.pdb"
    atom_line = "ATOM      1  C   ALA A   1       1.000   2.000   3.000  1.00  0.00           C"
    receptor_pdb.write_text(atom_line + "\nEND\n", encoding="utf-8")
    rec_raw = tmp_path / "6CM4_rec_raw.pdb"
    rec_raw.write_text(atom_line + "\nEND\n", encoding="utf-8")
    ligand_sdf = tmp_path / "lig.sdf"
    ligand_sdf.write_text("lig\n  DockUP\n\n  0  0  0  0  0  0            999 V2000\nM  END\n$$$$\n", encoding="utf-8")
    grid = tmp_path / "grid.txt"
    grid.write_text(
        "center_x = 1\ncenter_y = 2\ncenter_z = 3\nsize_x = 25\nsize_y = 25\nsize_z = 25\n",
        encoding="utf-8",
    )
    prepared_root = tmp_path / "_prepared"
    prepared_plan = plan(
        prepared_root=prepared_root,
        pdb_id="6CM4",
        chain="A",
        ligand_resname="LIG",
        receptor_pdb=rec_raw,
        ligand_sdf=ligand_sdf,
        grid_file=grid,
        mkrec_allow_bad_res="1",
        mkrec_default_altloc="A",
        pdb2pqr_ph="7.4",
        pdb2pqr_ff="AMBER",
        pdb2pqr_ffout="AMBER",
        pdb2pqr_nodebump="1",
        pdb2pqr_keep_chain="1",
        ligand_source_name=ligand_sdf.name,
    )
    input_plan = plan_receptor_input(
        prepared_root=prepared_root,
        pdb_id="6CM4",
        chain="A",
        ligand_resname="LIG",
        source_pdb=receptor_pdb,
        pdb2pqr_ph="7.4",
        pdb2pqr_ff="AMBER",
        pdb2pqr_ffout="AMBER",
        pdb2pqr_nodebump="1",
        pdb2pqr_keep_chain="1",
    )
    generated = tmp_path / "generated"
    generated.mkdir()
    rigid = generated / "6CM4_receptor.pdbqt"
    lig_pdbqt = generated / "6CM4_ligand.pdbqt"
    lig_fixed = generated / "6CM4_ligand_fixed.sdf"
    rec_pqr = generated / "6CM4_rec.pqr"
    rigid.write_text("PREPARED_RECEPTOR\n", encoding="utf-8")
    lig_pdbqt.write_text("PREPARED_LIGAND\n", encoding="utf-8")
    lig_fixed.write_text("PREPARED_FIXED\n", encoding="utf-8")
    rec_pqr.write_text("PREPARED_PQR\n", encoding="utf-8")
    install_receptor_input(input_plan, sources={"raw_pdb": str(rec_raw), "pqr": str(rec_pqr)})
    install(
        prepared_plan,
        sources={
            "rigid_pdbqt": str(rigid),
            "ligand_pdbqt": str(lig_pdbqt),
            "ligand_fixed_sdf": str(lig_fixed),
            "grid_file": str(grid),
        },
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_vina = fake_bin / "vina"
    fake_vina.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
out=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--out" ]; then out="$2"; shift 2; continue; fi
  shift
done
[ -n "$out" ] || exit 2
cat > "$out" <<'EOF'
MODEL 1
ATOM      1  C   UNL L   1       1.000   2.000   3.000  1.00  0.00           C
ENDMDL
EOF
printf -- '-----+------------+----------+----------\\n'
printf -- '    1       -7.5      0.0      0.0\\n'
""",
        encoding="utf-8",
    )
    fake_vina.chmod(0o755)
    fake_pymol_python = fake_bin / "fake_pymol_python"
    fake_pymol_python.write_text("#!/usr/bin/env bash\nexit 90\n", encoding="utf-8")
    fake_pymol_python.chmod(0o755)
    env = {
        **os.environ,
        "DOCKUP_ROOT": str(tmp_path),
        "DOCKUP_PYTHON": str(repo / ".venv" / "bin" / "python"),
        "DOCKUP_PYMOL_PYTHON": str(fake_pymol_python),
        "DOCKUP_VINA": str(fake_vina),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "PYTHONPATH": str(repo),
    }

    completed = subprocess.run(
        [
            "bash",
            str(repo / "scripts" / "dock1.sh"),
            "6CM4",
            "A",
            "LIG",
            "--pdb_file",
            str(receptor_pdb),
            "--lig_spec",
            str(ligand_sdf),
            "--grid_file",
            str(grid),
            "--prepared_root",
            str(prepared_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout
    assert "Prepared receptor input raw cache hit" in completed.stdout
    assert "Prepared receptor input PQR cache hit" in completed.stdout
    assert "Prepared receptor cache hit" in completed.stdout
    assert "Prepared ligand cache hit" in completed.stdout
    out_receptor = tmp_path / "6CM4_results" / "6CM4_receptor.pdbqt"
    out_ligand = tmp_path / "6CM4_results" / "6CM4_ligand.pdbqt"
    assert out_receptor.is_symlink() or out_receptor.read_text(encoding="utf-8") == "PREPARED_RECEPTOR\n"
    assert out_ligand.is_symlink() or out_ligand.read_text(encoding="utf-8") == "PREPARED_LIGAND\n"
    assert Path(prepared_plan["receptor"]["rigid_pdbqt"]).exists()
    assert Path(prepared_plan["ligand"]["pdbqt"]).exists()
