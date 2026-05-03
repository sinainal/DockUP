import argparse
import json
import sys
import time
import datetime
from pathlib import Path
from typing import Any


def _legacy_backend() -> dict[str, Any]:
    try:
        from .app import _existing_files, _load_receptor_meta, _start_run
        from .config import BASE, DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR
        from .state import RUN_STATE
    except ImportError:
        from docking_app.app import _existing_files, _load_receptor_meta, _start_run
        from docking_app.config import BASE, DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR
        from docking_app.state import RUN_STATE
    return {
        "_start_run": _start_run,
        "_existing_files": _existing_files,
        "_load_receptor_meta": _load_receptor_meta,
        "BASE": BASE,
        "DOCK_DIR": DOCK_DIR,
        "LIGAND_DIR": LIGAND_DIR,
        "RECEPTOR_DIR": RECEPTOR_DIR,
        "RUN_STATE": RUN_STATE,
    }


def _live_client(args: argparse.Namespace):
    try:
        from .live import DockUPClient
    except ImportError:
        from docking_app.live import DockUPClient
    return DockUPClient(base_url=args.base_url, timeout=float(args.timeout))


def _print_payload(payload: dict[str, Any], *, as_json: bool = False, pretty: bool = False) -> None:
    if as_json or pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
        return
    message = str(payload.get("message") or "").strip()
    if message:
        print(message)
    else:
        print(json.dumps(payload, ensure_ascii=False))


def _live_envelope(
    action: str,
    data: dict[str, Any],
    *,
    message: str = "",
    ui_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = data.get("error") or data.get("detail")
    return {
        "ok": not bool(error),
        "action": action,
        "message": message or (str(error) if error else f"{action} completed."),
        "data": data,
        "ui_hints": ui_hints or {},
        "error": {"message": str(error), "recoverable": True} if error else None,
    }

def run_docking_cli(args):
    """
    Main execution logic for CLI.
    Generates grid files, manifest.tsv and triggers _start_run.
    """
    backend = _legacy_backend()
    _start_run = backend["_start_run"]
    _existing_files = backend["_existing_files"]
    _load_receptor_meta = backend["_load_receptor_meta"]
    DOCK_DIR = backend["DOCK_DIR"]
    LIGAND_DIR = backend["LIGAND_DIR"]
    RECEPTOR_DIR = backend["RECEPTOR_DIR"]
    RUN_STATE = backend["RUN_STATE"]

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


def cmd_live_state(args: argparse.Namespace) -> int:
    data = _live_client(args).get_state()
    payload = _live_envelope(
        "state.get",
        data,
        message=f"state: receptor={data.get('selected_receptor') or '-'} queue={data.get('queue_count', 0)} run={data.get('run_status') or '-'}",
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_run_status(args: argparse.Namespace) -> int:
    data = _live_client(args).get_run_status()
    payload = _live_envelope(
        "run.status",
        data,
        message=f"run: {data.get('status') or '-'} {data.get('completed_runs', 0)}/{data.get('total_runs', 0)}",
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_receptor_list(args: argparse.Namespace) -> int:
    data = _live_client(args).list_receptors()
    receptors = data.get("receptors") if isinstance(data.get("receptors"), list) else []
    payload = _live_envelope(
        "receptor.list",
        data,
        message=f"receptors: {len(receptors)}",
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_receptor_load(args: argparse.Namespace) -> int:
    pdb_ids = " ".join(str(item).strip() for item in args.pdb_ids if str(item).strip())
    data = _live_client(args).load_receptors(pdb_ids)
    summary = data.get("summary") if isinstance(data.get("summary"), list) else []
    ignored = data.get("ignored_ids") if isinstance(data.get("ignored_ids"), list) else []
    payload = _live_envelope(
        "receptor.load",
        data,
        message=f"loaded receptors: {len(summary)}" + (f"; ignored={','.join(str(x) for x in ignored)}" if ignored else ""),
        ui_hints={"refresh": ["state", "receptors"]},
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_receptor_select(args: argparse.Namespace) -> int:
    pdb_id = str(args.pdb_id or "").strip().upper()
    data = _live_client(args).select_receptor(pdb_id)
    payload = _live_envelope(
        "receptor.select",
        data,
        message=f"selected receptor: {data.get('selected_receptor') or pdb_id or '-'}",
        ui_hints={"refresh": ["state", "viewer"], "selected_receptor": data.get("selected_receptor") or pdb_id},
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_viewer_show(args: argparse.Namespace) -> int:
    pdb_id = str(args.pdb_id or "").strip().upper()
    client = _live_client(args)
    select_result = client.select_receptor(pdb_id)
    if select_result.get("error"):
        payload = _live_envelope("viewer.show", select_result)
        _print_payload(payload, as_json=args.json, pretty=args.pretty)
        return 2
    detail = client.get_receptor_detail(pdb_id, chain=str(args.chain or ""))
    pdb_text = str(detail.get("pdb_text") or "")
    compact = {
        "pdb_id": detail.get("pdb_id") or pdb_id,
        "pdb_text_length": len(pdb_text),
        "chains": detail.get("chains") or [],
        "ligands_by_chain": detail.get("ligands_by_chain") or {},
        "pdb_file": detail.get("pdb_file") or "",
        "selected_chain": detail.get("selected_chain") or args.chain or "all",
        "selected_ligand": detail.get("selected_ligand") or "",
    }
    if detail.get("error"):
        compact["error"] = detail.get("error")
    payload = _live_envelope(
        "viewer.show",
        compact,
        message=(
            f"viewer ready: {compact['pdb_id']} ({compact['pdb_text_length']} pdb chars)"
            if compact.get("pdb_text_length")
            else f"viewer data missing: {pdb_id}"
        ),
        ui_hints={"refresh": ["state", "viewer"], "selected_receptor": compact.get("pdb_id")},
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] and compact.get("pdb_text_length") else 2


def run_agent_assets_cli(args) -> int:
    try:
        from .agent.autonomous_docking import AGENT_STATE, fetch_assets, plan_assets
    except ImportError:
        from docking_app.agent.autonomous_docking import AGENT_STATE, fetch_assets, plan_assets

    AGENT_STATE.update({"inventory": {}, "setup_rows": [], "grid_data": {}, "batch_config": {}, "batch_id": ""})
    planned = plan_assets(args.receptors, args.ligands)
    result = fetch_assets(planned["receptors"], planned["ligands"])
    payload = {"ok": not result.get("failed_receptors") and not result.get("failed_ligands"), "planned": planned, "assets": result}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload["ok"] else 2


def run_agent_workflow_cli(args) -> int:
    try:
        from .agent.autonomous_docking import (
            AGENT_STATE,
            build_queue,
            fetch_assets,
            make_gridboxes,
            plan_assets,
            prepare_batch,
            run_queue,
            setup_docking,
            suggest_setup_rows,
            validate_batch,
        )
    except ImportError:
        from docking_app.agent.autonomous_docking import (
            AGENT_STATE,
            build_queue,
            fetch_assets,
            make_gridboxes,
            plan_assets,
            prepare_batch,
            run_queue,
            setup_docking,
            suggest_setup_rows,
            validate_batch,
        )

    AGENT_STATE.update({"inventory": {}, "setup_rows": [], "grid_data": {}, "batch_config": {}, "batch_id": ""})
    trace: list[dict[str, object]] = []

    planned = plan_assets(args.receptors, args.ligands)
    trace.append({"tool": "plan_assets", "arguments": {"receptors": args.receptors, "ligands": args.ligands}, "result": planned})

    assets = fetch_assets(planned["receptors"], planned["ligands"])
    trace.append({"tool": "fetch_assets", "arguments": planned, "result": assets})
    if assets.get("failed_receptors") or assets.get("failed_ligands"):
        payload = {"ok": False, "error": "asset download failed", "trace": trace}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    rows = str(args.rows or "").strip()
    if not rows:
        rows = suggest_setup_rows(AGENT_STATE.get("inventory") or {}, args.box_size)
    if not rows:
        payload = {"ok": False, "error": "could not choose setup rows from receptor native ligands", "trace": trace}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    setup = setup_docking(rows)
    trace.append({"tool": "submit_setup_rows", "arguments": {"rows": rows}, "result": setup})

    grids = make_gridboxes(rows)
    trace.append({"tool": "make_gridboxes", "arguments": {"rows": rows}, "result": grids})
    if grids.get("warnings") and not grids.get("grid_data"):
        payload = {"ok": False, "error": "gridbox creation failed", "trace": trace}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    batch = prepare_batch(run_count=args.runs, padding=args.padding, out_root_name=args.out_root_name)
    trace.append(
        {
            "tool": "submit_batch_config",
            "arguments": {"run_count": args.runs, "padding": args.padding, "out_root_name": args.out_root_name},
            "result": batch,
        }
    )

    validation = validate_batch()
    trace.append({"tool": "validate_batch", "arguments": {}, "result": validation})
    if not validation.get("ok"):
        payload = {"ok": False, "error": "batch validation failed", "validation": validation, "trace": trace}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    queue = build_queue(replace_queue=args.replace_queue)
    trace.append({"tool": "build_queue", "arguments": {"replace_queue": args.replace_queue}, "result": queue})
    if not queue.get("ok"):
        payload = {"ok": False, "error": "queue build failed", "queue": queue, "trace": trace}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 2

    run = run_queue(test_mode=args.test_mode)
    trace.append({"tool": "run_queue", "arguments": {"test_mode": args.test_mode}, "result": run})
    payload = {
        "ok": bool(run.get("ok")),
        "validation": validation,
        "queue": queue,
        "run": run,
        "setup_rows": setup.get("rows"),
        "grid_data": grids.get("grid_data"),
        "trace": trace,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload["ok"] else 2


def run_agent_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="DockUP agent-safe CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    assets = sub.add_parser("agent-assets", help="Fetch receptor/ligand assets and print compact inventory")
    assets.add_argument("--receptors", required=True, help="Comma/space separated PDB IDs")
    assets.add_argument("--ligands", required=True, help="Comma/semicolon separated ligand names, CIDs, local identifiers, or explicit forms like ethylene[1,3]")
    assets.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    assets.set_defaults(func=run_agent_assets_cli)

    workflow = sub.add_parser("agent-workflow", help="Run the DockUP agent tool flow without an LLM")
    workflow.add_argument("--receptors", required=True, help="Comma/space separated PDB IDs")
    workflow.add_argument("--ligands", required=True, help="Comma/semicolon separated ligand names, CIDs, local identifiers, or explicit forms like ethylene[1,3]")
    workflow.add_argument("--rows", default="", help="Optional setup rows: receptor,chain,native_ligand,box_size,dock_ligands;...")
    workflow.add_argument("--box-size", type=float, default=20.0, help="Auto setup gridbox size")
    workflow.add_argument("--runs", type=int, default=1, help="Runs per receptor/ligand setup")
    workflow.add_argument("--padding", type=float, default=0.0, help="Extra grid padding")
    workflow.add_argument("--out-root-name", default="", help="Output folder name under data/dock")
    workflow.add_argument("--replace-queue", action="store_true", help="Replace current queue with the generated batch")
    workflow.add_argument("--test-mode", action="store_true", help="Materialize/mock run without starting heavy docking")
    workflow.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    workflow.set_defaults(func=run_agent_workflow_cli)

    def add_live_output_flags(cmd: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
        default = argparse.SUPPRESS if suppress_default else False
        cmd.add_argument("--json", action="store_true", default=default, help="Print machine-readable JSON envelope")
        cmd.add_argument("--pretty", action="store_true", default=default, help="Pretty-print JSON envelope")

    live = sub.add_parser("live", help="Control a running DockUP backend through the live API")
    live.add_argument("--base-url", default="http://localhost:8000", help="Running DockUP backend URL")
    live.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    add_live_output_flags(live)
    live_sub = live.add_subparsers(dest="live_cmd", required=True)

    live_state = live_sub.add_parser("state", help="Read live DockUP state")
    add_live_output_flags(live_state, suppress_default=True)
    live_state.set_defaults(func=cmd_live_state)

    live_run = live_sub.add_parser("run", help="Run-related live commands")
    live_run_sub = live_run.add_subparsers(dest="run_cmd", required=True)
    live_run_status = live_run_sub.add_parser("status", help="Read live run status")
    add_live_output_flags(live_run_status, suppress_default=True)
    live_run_status.set_defaults(func=cmd_live_run_status)

    live_receptor = live_sub.add_parser("receptor", help="Receptor-related live commands")
    live_receptor_sub = live_receptor.add_subparsers(dest="receptor_cmd", required=True)
    live_receptor_list = live_receptor_sub.add_parser("list", help="List stored/loaded receptors")
    add_live_output_flags(live_receptor_list, suppress_default=True)
    live_receptor_list.set_defaults(func=cmd_live_receptor_list)
    live_receptor_load = live_receptor_sub.add_parser("load", help="Load receptor PDB IDs into live state")
    live_receptor_load.add_argument("pdb_ids", nargs="+")
    add_live_output_flags(live_receptor_load, suppress_default=True)
    live_receptor_load.set_defaults(func=cmd_live_receptor_load)
    live_receptor_select = live_receptor_sub.add_parser("select", help="Select a receptor in live state")
    live_receptor_select.add_argument("pdb_id")
    add_live_output_flags(live_receptor_select, suppress_default=True)
    live_receptor_select.set_defaults(func=cmd_live_receptor_select)

    live_viewer = live_sub.add_parser("viewer", help="Viewer-related live commands")
    live_viewer_sub = live_viewer.add_subparsers(dest="viewer_cmd", required=True)
    live_viewer_show = live_viewer_sub.add_parser("show", help="Select receptor and verify viewer data is available")
    live_viewer_show.add_argument("pdb_id")
    live_viewer_show.add_argument("--chain", default="")
    add_live_output_flags(live_viewer_show, suppress_default=True)
    live_viewer_show.set_defaults(func=cmd_live_viewer_show)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"agent-assets", "agent-workflow", "live"}:
        raise SystemExit(run_agent_cli(sys.argv[1:]))

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
