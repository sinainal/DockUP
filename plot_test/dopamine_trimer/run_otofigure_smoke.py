from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from figure_scripts.otofigure.pipeline import run as run_otofigure


def build_case(receptor: str, ligand: str) -> tuple[list[tuple[str, Path]], Path]:
    base = (
        REPO_ROOT
        / "docking_app"
        / "workspace"
        / "data"
        / "dock"
        / "dopamine_trimer"
        / receptor
        / ligand
    )
    if not base.exists():
        raise FileNotFoundError(f"Missing dopamine_trimer sample: {base}")
    run_entries = [(run_dir.name, run_dir.resolve()) for run_dir in sorted(base.iterdir()) if run_dir.is_dir()]
    output_dir = Path(__file__).resolve().parent / "out" / f"{receptor}_{ligand}"
    return run_entries, output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a DockUP OtoFigure smoke render on dopamine_trimer data.")
    parser.add_argument("--receptor", default="3PBL")
    parser.add_argument("--ligand", default="Ethylene_trimer")
    parser.add_argument("--dpi", type=int, default=30)
    args = parser.parse_args()

    run_entries, output_dir = build_case(args.receptor, args.ligand)
    result = run_otofigure(
        receptor_id=args.receptor,
        ligand_name=args.ligand,
        run_entries=run_entries,
        output_png=output_dir / f"{args.receptor}_{args.ligand}_otofigure.png",
        work_dir=output_dir / "work",
        dpi=args.dpi,
        preview_mode=False,
    )
    print(f"Output directory: {output_dir}")
    print(f"Final image: {result['final_png']}")
    print(f"Formatted image: {result['formatted_png']}")
    print(f"Used runs: {', '.join(result['used_runs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
