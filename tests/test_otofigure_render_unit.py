from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from rdkit import Chem
from rdkit.Chem import AllChem

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from figure_scripts.otofigure import pipeline
from figure_scripts.otofigure import render_interaction_maps


def _make_run_dir(root: Path, receptor_id: str, run_name: str) -> Path:
    run_dir = root / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{receptor_id}_rec_raw.pdb").write_text("ATOM\n", encoding="utf-8")
    (run_dir / f"{receptor_id}_pose.pdb").write_text("HETATM\n", encoding="utf-8")
    return run_dir


@pytest.mark.unit
def test_otofigure_pipeline_stages_case_layout_and_copies_final_png(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    run_entries = []
    for run_name in ("run1", "run2", "run3", "run4", "run5"):
        run_entries.append((run_name, _make_run_dir(source_root, "3PBL", run_name)))

    def fake_find_python_with_modules(modules, *, env_var, extra_candidates=()):
        if modules == ["pymol"]:
            return "/usr/bin/python3"
        return "/home/sina/anaconda3/bin/python3"

    def fake_run_step(cmd: list[str], *, cwd: Path, env: dict[str, str], on_process_start=None, on_process_end=None) -> str:
        assert Path(cwd, "protein", "3pbl.pdb").exists()
        assert sorted(path.name for path in Path(cwd, "ligands").glob("*.pdb")) == [
            "run1.pdb",
            "run2.pdb",
            "run3.pdb",
            "run4.pdb",
            "run5.pdb",
        ]
        script_name = Path(cmd[1]).name
        if script_name == "final_dinamik.py":
            dpi_arg = cmd[cmd.index("--dpi") + 1]
            assert dpi_arg == "30"
            Image.new("RGBA", (400, 300), "white").save(Path(cwd, "results", "3pbl_run1_far.png"))
            Image.new("RGBA", (400, 300), "white").save(Path(cwd, "results", "3pbl_run1_close.png"))
        elif script_name == "render_interaction_maps.py":
            Image.new("RGBA", (500, 240), (255, 255, 255, 0)).save(Path(cwd, "interaction", "3pbl_run1_interaction.png"))
        elif script_name == "create_visualization.py":
            dpi_arg = cmd[cmd.index("--dpi") + 1]
            assert dpi_arg == "30"
            img = Image.new("RGBA", (1200, 320), (255, 255, 255, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle((150, 60, 260, 170), fill=(255, 255, 255, 255))
            img.save(Path(cwd, "final_results", "3pbl_run1_final.png"))
        elif script_name == "final_formatter.py":
            dpi_arg = cmd[cmd.index("--render_dpi") + 1]
            assert dpi_arg == "30"
            Image.new("RGBA", (1200, 320), "white").save(Path(cwd, "formatted_results", "formatted_figure_1.png"))
        else:
            raise AssertionError(f"Unexpected script: {cmd}")
        assert env["MPLBACKEND"] == "Agg"
        assert Path(env["MPLCONFIGDIR"]).name == ".matplotlib"
        return f"ok:{script_name}"

    monkeypatch.setattr(pipeline, "_find_python_with_modules", fake_find_python_with_modules)
    monkeypatch.setattr(pipeline, "_run_step", fake_run_step)

    output_png = tmp_path / "out" / "otofigure.png"
    result = pipeline.run(
        receptor_id="3PBL",
        ligand_name="Ethylene_trimer",
        run_entries=run_entries,
        output_png=output_png,
        work_dir=tmp_path / "work",
        dpi=30,
        preview_mode=False,
    )

    assert output_png.exists()
    assert result["ligand_name"] == "Ethylene_trimer"
    assert result["used_runs"] == ["run1", "run2", "run3", "run4", "run5"]
    assert Path(result["raw_final_png"]).exists()
    assert Path(result["formatted_png"]).exists()
    with Image.open(output_png) as copied_image:
        assert copied_image.convert("RGBA").getpixel((0, 0))[3] == 0
        assert copied_image.convert("RGBA").getpixel((200, 100))[3] == 255


@pytest.mark.unit
def test_otofigure_pipeline_limits_to_first_five_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "source"
    run_entries = []
    for run_name in ("run1", "run2", "run3", "run4", "run5", "run6"):
        run_entries.append((run_name, _make_run_dir(source_root, "6CM4", run_name)))

    monkeypatch.setattr(
        pipeline,
        "_find_python_with_modules",
        lambda modules, *, env_var, extra_candidates=(): "/usr/bin/python3",
    )

    def fake_run_step(cmd: list[str], *, cwd: Path, env: dict[str, str], on_process_start=None, on_process_end=None) -> str:
        ligands = sorted(path.name for path in Path(cwd, "ligands").glob("*.pdb"))
        assert ligands == ["run1.pdb", "run2.pdb", "run3.pdb", "run4.pdb", "run5.pdb"]
        script_name = Path(cmd[1]).name
        if script_name == "final_dinamik.py":
            dpi_arg = cmd[cmd.index("--dpi") + 1]
            assert dpi_arg == "30"
            Image.new("RGBA", (200, 150), "white").save(Path(cwd, "results", "6cm4_run1_far.png"))
            Image.new("RGBA", (200, 150), "white").save(Path(cwd, "results", "6cm4_run1_close.png"))
        elif script_name == "render_interaction_maps.py":
            Image.new("RGBA", (260, 180), (255, 255, 255, 0)).save(Path(cwd, "interaction", "6cm4_run1_interaction.png"))
        elif script_name == "create_visualization.py":
            dpi_arg = cmd[cmd.index("--dpi") + 1]
            assert dpi_arg == "30"
            Image.new("RGBA", (600, 180), "white").save(Path(cwd, "final_results", "6cm4_run1_final.png"))
        elif script_name == "final_formatter.py":
            dpi_arg = cmd[cmd.index("--render_dpi") + 1]
            assert dpi_arg == "30"
            Image.new("RGBA", (600, 180), "white").save(Path(cwd, "formatted_results", "formatted_figure_1.png"))
        return "ok"

    monkeypatch.setattr(pipeline, "_run_step", fake_run_step)

    result = pipeline.run(
        receptor_id="6CM4",
        ligand_name="Ethylene_trimer",
        run_entries=run_entries,
        output_png=tmp_path / "render.png",
        work_dir=tmp_path / "work",
        dpi=20,
        preview_mode=True,
    )

    assert result["used_runs"] == ["run1", "run2", "run3", "run4", "run5"]


@pytest.mark.unit
def test_otofigure_render_settings_scale_pixels_with_requested_dpi() -> None:
    assert pipeline._render_settings(120, preview_mode=False) == (400, 300, 120)
    assert pipeline._render_settings(300, preview_mode=False) == (1000, 750, 300)
    assert pipeline._render_settings(72, preview_mode=True) == (320, 240, 72)


@pytest.mark.unit
def test_prepare_draw_mol_normalizes_only_stereoany_double_bonds() -> None:
    mol = Chem.MolFromSmiles("CC=CC=C")
    assert mol is not None
    mol = Chem.AddHs(mol)
    AllChem.EmbedMolecule(mol, randomSeed=7)

    double_bonds = [bond for bond in mol.GetBonds() if bond.GetBondTypeAsDouble() == 2]
    assert len(double_bonds) == 2
    double_bonds[0].SetStereo(Chem.rdchem.BondStereo.STEREOANY)
    double_bonds[1].SetStereo(Chem.rdchem.BondStereo.STEREONONE)

    draw_mol = render_interaction_maps._prepare_draw_mol(mol)
    draw_double_bonds = [bond for bond in draw_mol.GetBonds() if bond.GetBondTypeAsDouble() == 2]

    assert len(draw_double_bonds) == 2
    assert str(draw_double_bonds[0].GetStereo()) == "STEREONONE"
    assert str(draw_double_bonds[1].GetStereo()) == "STEREONONE"
