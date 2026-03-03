import argparse
import time
import datetime
from pathlib import Path

try:
    from .app import (
        _start_run,
        _existing_files,
        _load_receptor_meta,
        DOCK_DIR,
        BASE,
        LIGAND_DIR,
        RECEPTOR_DIR,
        RUN_STATE,
    )
except ImportError:
    from docking_app.app import (
        _start_run,
        _existing_files,
        _load_receptor_meta,
        DOCK_DIR,
        BASE,
        LIGAND_DIR,
        RECEPTOR_DIR,
        RUN_STATE,
    )

def run_docking_cli(args):
    """
    Main execution logic for CLI.
    Generates grid files, manifest.tsv and triggers _start_run.
    """
    out_root_name = args.out_root_name
    if not out_root_name:
        out_root_name = datetime.datetime.now().strftime("docking_%Y_%m_%d_%H%M%S")
        
    out_root_path = Path(args.out_root)
    out_root = out_root_path / out_root_name
    
    pdb_ids = [r.strip() for r in args.receptors if r.strip()]
    if not pdb_ids:
        print("Error: No receptors provided.")
        return
        
    pdb_files = _existing_files(RECEPTOR_DIR, (".pdb",))
    meta_list = _load_receptor_meta(pdb_ids, pdb_files)
    
    if not meta_list:
        print("Error: Could not load any receptor metadata. Ensure PDB files exist.")
        return
        
    ligands_in_dir = _existing_files(LIGAND_DIR, (".sdf",))
    
    batch_id = int(time.time() * 1000)
    queue = []
    
    for meta in meta_list:
        pdb_id = meta["pdb_id"]
        
        grid_file_path = DOCK_DIR / f"{pdb_id}_{batch_id}_gridbox.txt"
        grid_file_path.write_text(
            f"center_x = {args.grid_cx}\n"
            f"center_y = {args.grid_cy}\n"
            f"center_z = {args.grid_cz}\n"
            f"size_x = {args.grid_sx}\n"
            f"size_y = {args.grid_sy}\n"
            f"size_z = {args.grid_sz}\n"
        )
        
        target_ligands = []
        if args.mode == "Redocking":
            target_ligs = args.ligands if args.ligands else [""]
            for lig_name in target_ligs:
                target_ligands.append({"name": lig_name, "path": ""})
        else:
            if not args.ligands or "all_set" in args.ligands:
                target_ligands = [{"name": l.name, "path": str(l)} for l in ligands_in_dir]
            else:
                for lig_obj in ligands_in_dir:
                    if lig_obj.name in args.ligands or lig_obj.stem in args.ligands:
                        target_ligands.append({"name": lig_obj.name, "path": str(lig_obj)})
        
        for lig_obj in target_ligands:
            ligand_label = lig_obj["name"]
            ligand_resname = ligand_label
            if args.mode == "Redocking" and ligand_label:
                ligand_resname = ligand_label.split()[0]
                
            queue.append({
                "pdb_id": pdb_id,
                "chain": args.chain,
                "ligand_name": ligand_label,
                "ligand_resname": ligand_resname,
                "lig_spec": lig_obj["path"],
                "pdb_file": meta.get("pdb_file", ""),
                "grid_pad": args.padding,
                "grid_file": str(grid_file_path),
            })
            
    if not queue:
        print("Warning: Job queue is empty. Check inputs.")
        return
        
    manifest_path = DOCK_DIR / "manifest.tsv"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in queue:
            ligand_val = row.get("ligand_resname") or row.get("ligand_name") or ""
            values = [
                row.get("pdb_id", ""),
                row.get("chain", ""),
                ligand_val,
                row.get("lig_spec", ""),
                row.get("pdb_file", ""),
                row.get("grid_pad", ""),
                row.get("grid_file", ""),
            ]
            values = ["__EMPTY__" if v is None or str(v) == "" else str(v) for v in values]
            handle.write("\t".join(values) + "\n")
            
    total_runs = len(queue) * args.runs
    print(f"Starting {args.mode} jobs... {len(queue)} configurations * {args.runs} runs = {total_runs} total runs")
    print(f"Output Directory: {out_root}")

    _start_run(
        manifest_path=manifest_path,
        runs=args.runs, 
        out_root=str(out_root),
        total_runs=total_runs,
        initial_command="CLI Executed",
        is_test_mode=args.test_mode
    )
    
    last_idx = 0
    while RUN_STATE["status"] == "running":
        new_lines = RUN_STATE["log_lines"][last_idx:]
        for line in new_lines:
            print(line)
        last_idx = len(RUN_STATE["log_lines"])
        time.sleep(0.5)
        
    for line in RUN_STATE["log_lines"][last_idx:]:
        print(line)
        
    print(f"Batch completed with status: {RUN_STATE['status']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Docking Automation CLI - Seamlessly integrates with the backend")
    parser.add_argument("--mode", choices=["Docking", "Redocking"], default="Docking", help="Job Type")
    parser.add_argument("--receptors", nargs="+", required=True, help="PDB IDs to process (e.g. 7X2F 6CM4)")
    parser.add_argument("--ligands", nargs="*", default=[], help="Ligands. Docking: SDF filenames or 'all_set'. Redocking: Native ligand name")
    parser.add_argument("--chain", default="all", help="Chain specification. Default: all")
    parser.add_argument("--grid-cx", type=float, default=0.0, help="Grid Center X")
    parser.add_argument("--grid-cy", type=float, default=0.0, help="Grid Center Y")
    parser.add_argument("--grid-cz", type=float, default=0.0, help="Grid Center Z")
    parser.add_argument("--grid-sx", type=float, default=20.0, help="Grid Size X")
    parser.add_argument("--grid-sy", type=float, default=20.0, help="Grid Size Y")
    parser.add_argument("--grid-sz", type=float, default=20.0, help="Grid Size Z")
    parser.add_argument("--padding", type=float, default=0.0, help="Grid padding around ligand")
    parser.add_argument("--runs", type=int, default=10, help="Number of runs per setup")
    parser.add_argument("--out-root", default="data/dock", help="Root folder for outputs")
    parser.add_argument("--out-root-name", default="", help="Optional subfolder name. Auto-generated if empty.")
    parser.add_argument("--test-mode", action="store_true", help="Execute mock blank docking without actual Vina processing")

    args = parser.parse_args()
    
    run_docking_cli(args)
