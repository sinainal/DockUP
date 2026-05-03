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


def _coerce_live_envelope(
    action: str,
    data: dict[str, Any],
    *,
    message: str = "",
    ui_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if {"ok", "action", "data"}.issubset(data.keys()):
        return data
    return _live_envelope(action, data, message=message, ui_hints=ui_hints)


def _envelope_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _json_arg(value: str, default: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON argument: {exc}") from exc


def _csv_arg(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


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
    state = _envelope_data(data) or data
    payload = _coerce_live_envelope(
        "state.get",
        data,
        message=f"state: receptor={state.get('selected_receptor') or '-'} queue={state.get('queue_count', 0)} run={state.get('run_status') or '-'}",
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_run_status(args: argparse.Namespace) -> int:
    data = _live_client(args).get_run_status()
    inner = _envelope_data(data) or data
    payload = _coerce_live_envelope(
        "run.status",
        data,
        message=f"run: {inner.get('status') or '-'} {inner.get('completed_runs', 0)}/{inner.get('total_runs', 0)}",
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_receptor_list(args: argparse.Namespace) -> int:
    data = _live_client(args).list_receptors()
    inner = _envelope_data(data) or data
    receptors = inner.get("receptors") if isinstance(inner.get("receptors"), list) else []
    payload = _coerce_live_envelope(
        "receptor.list",
        data,
        message=f"receptors: {len(receptors)}",
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_receptor_load(args: argparse.Namespace) -> int:
    pdb_ids = " ".join(str(item).strip() for item in args.pdb_ids if str(item).strip())
    data = _live_client(args).load_receptors(pdb_ids)
    inner = _envelope_data(data) or data
    summary = inner.get("summary") if isinstance(inner.get("summary"), list) else []
    ignored = inner.get("ignored_ids") if isinstance(inner.get("ignored_ids"), list) else []
    payload = _coerce_live_envelope(
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
    inner = _envelope_data(data) or data
    payload = _coerce_live_envelope(
        "receptor.select",
        data,
        message=f"selected receptor: {inner.get('selected_receptor') or pdb_id or '-'}",
        ui_hints={"refresh": ["state", "viewer"], "selected_receptor": inner.get("selected_receptor") or pdb_id},
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_receptor_delete(args: argparse.Namespace) -> int:
    data = _live_client(args).delete_receptor(str(args.target or "").strip())
    payload = _coerce_live_envelope("receptor.delete", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_receptor_clear(args: argparse.Namespace) -> int:
    data = _live_client(args).clear_receptors()
    payload = _coerce_live_envelope("receptor.clear", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_ligand_list(args: argparse.Namespace) -> int:
    data = _live_client(args).list_ligands()
    inner = _envelope_data(data) or data
    ligands = inner.get("ligands") if isinstance(inner.get("ligands"), list) else []
    payload = _coerce_live_envelope("ligand.list", data, message=f"ligands: {len(ligands)}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_ligand_fetch(args: argparse.Namespace) -> int:
    ligand_ids = ";".join(str(item).strip() for item in args.ligands if str(item).strip())
    data = _live_client(args).fetch_ligands(ligand_ids)
    payload = _coerce_live_envelope("ligand.fetch", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_ligand_delete(args: argparse.Namespace) -> int:
    data = _live_client(args).delete_ligand(str(args.name or "").strip())
    payload = _coerce_live_envelope("ligand.delete", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_ligand_clear(args: argparse.Namespace) -> int:
    data = _live_client(args).clear_ligands()
    payload = _coerce_live_envelope("ligand.clear", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_assets_inspect(args: argparse.Namespace) -> int:
    data = _live_client(args).inspect_assets()
    inner = _envelope_data(data) or data
    inventory = inner.get("inventory") if isinstance(inner.get("inventory"), dict) else {}
    receptors = inventory.get("receptors") if isinstance(inventory.get("receptors"), dict) else {}
    ligands = inventory.get("ligands") if isinstance(inventory.get("ligands"), list) else []
    payload = _coerce_live_envelope("assets.inspect", data, message=f"assets: {len(receptors)} receptor(s), {len(ligands)} ligand(s)")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_viewer_show(args: argparse.Namespace) -> int:
    pdb_id = str(args.pdb_id or "").strip().upper()
    data = _live_client(args).show_viewer(pdb_id, chain=str(args.chain or ""))
    payload = _coerce_live_envelope("viewer.show", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_viewer_residues(args: argparse.Namespace) -> int:
    pdb_id = str(args.pdb_id or "").strip().upper()
    data = _live_client(args).show_residues(pdb_id, residue=str(args.residue or "TRP"), chain=str(args.chain or "all"))
    inner = _envelope_data(data) or data
    payload = _coerce_live_envelope(
        "viewer.residues",
        data,
        message=str(inner.get("summary") or f"residues: {len(inner.get('residues') or [])}"),
        ui_hints={"refresh": ["state", "viewer", "grid-selection"]},
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_workspace_select(args: argparse.Namespace) -> int:
    data = _live_client(args).select_workspace(
        str(args.receptor or "all"),
        chain=str(args.chain or "auto"),
        native_ligand=str(args.native_ligand or "auto"),
        dock_ligands=str(args.dock_ligands or "all"),
    )
    payload = _coerce_live_envelope("workspace.select", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_gridbox_set(args: argparse.Namespace) -> int:
    data = _live_client(args).set_gridbox(
        method=str(args.method or "native_ligand"),
        size=float(args.size),
        padding=float(args.padding),
        center=str(args.center or ""),
        pocket_rank=int(args.pocket_rank),
        p2rank_mode=str(args.p2rank_mode or "fit"),
    )
    payload = _coerce_live_envelope("gridbox.set", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_config_set(args: argparse.Namespace) -> int:
    payload_args = {
        "engine": args.engine,
        "mode": args.mode,
        "run_count": args.runs,
        "padding": args.padding,
        "out_root_name": args.out_root_name,
        "exhaustiveness": args.exhaustiveness,
        "num_modes": args.num_modes,
        "energy_range": args.energy_range,
        "cpu": args.cpu,
        "seed": args.seed,
        "ph": args.ph,
        "advanced": args.advanced,
    }
    data = _live_client(args).set_config(**payload_args)
    payload = _coerce_live_envelope("config.set", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_queue_list(args: argparse.Namespace) -> int:
    data = _live_client(args).list_queue()
    inner = _envelope_data(data) or data
    payload = _coerce_live_envelope("queue.list", data, message=f"queue: {inner.get('queue_count', 0)} job(s)")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_queue_build(args: argparse.Namespace) -> int:
    data = _live_client(args).build_queue(replace_queue=not bool(args.append))
    payload = _coerce_live_envelope("queue.build", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_queue_remove(args: argparse.Namespace) -> int:
    data = _live_client(args).remove_queue_batch(str(args.batch_id or ""))
    payload = _coerce_live_envelope("queue.remove", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_run_start(args: argparse.Namespace) -> int:
    data = _live_client(args).start_run(test_mode=bool(args.test_mode), batch_id=args.batch_id)
    payload = _coerce_live_envelope("run.start", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_run_stop(args: argparse.Namespace) -> int:
    data = _live_client(args).stop_run()
    payload = _coerce_live_envelope("run.stop", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_results_folders(args: argparse.Namespace) -> int:
    data = _live_client(args).list_result_folders()
    inner = _envelope_data(data) or data
    folders = inner.get("folders") if isinstance(inner.get("folders"), list) else []
    payload = _coerce_live_envelope("results.folders", data, message=f"result folders: {len(folders)}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_results_scan(args: argparse.Namespace) -> int:
    data = _live_client(args).scan_results(root_path=str(args.root or "data/dock"))
    inner = _envelope_data(data) or data
    results = inner.get("results") if isinstance(inner.get("results"), list) else []
    payload = _coerce_live_envelope("results.scan", data, message=f"results: {len(results)}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_results_detail(args: argparse.Namespace) -> int:
    data = _live_client(args).get_result_detail(result_dir=str(args.result_dir or ""))
    payload = _coerce_live_envelope("results.detail", data)
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_list(args: argparse.Namespace) -> int:
    data = _live_client(args).list_reports(
        root_path=args.root,
        source_path=args.source,
        output_path=args.output,
        linked_path=args.linked,
    )
    receptors = data.get("receptors") if isinstance(data.get("receptors"), list) else []
    images = data.get("images") if isinstance(data.get("images"), list) else []
    payload = _coerce_live_envelope(
        "report.list",
        data,
        message=f"report source={data.get('source_path') or '-'} receptors={len(receptors)} images={len(images)}",
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_preview(args: argparse.Namespace) -> int:
    data = _live_client(args).report_preview(
        root_path=args.root,
        source_path=args.source,
        receptor_id=args.receptor,
        run_name=args.run,
        render_mode=args.mode,
    )
    payload = _coerce_live_envelope(
        "report.preview",
        data,
        message=str(data.get("message") or f"preview available={data.get('available', True)}"),
    )
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_images(args: argparse.Namespace) -> int:
    data = _live_client(args).list_report_images(
        root_path=args.root,
        source_path=args.source,
        output_path=args.output,
        images_root_path=args.images_root,
    )
    payload = _coerce_live_envelope("report.images", data, message=f"report images: {data.get('total', 0)}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_metadata_get(args: argparse.Namespace) -> int:
    data = _live_client(args).get_report_root_metadata(root_path=args.root, source_path=args.source)
    payload = _coerce_live_envelope("report.metadata.get", data, message=f"metadata source={data.get('source_path') or '-'}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_metadata_save(args: argparse.Namespace) -> int:
    payload_args = {
        "root_path": args.root,
        "source_path": args.source,
        "reset": bool(args.reset),
        "main_type": args.main_type,
        "receptor_labels": _json_arg(args.receptor_labels_json, {}),
        "ligand_labels": _json_arg(args.ligand_labels_json, {}),
        "receptor_order": _csv_arg(args.receptor_order),
        "ligand_order": _csv_arg(args.ligand_order),
        "figure_start_number": args.figure_start_number,
        "extra_sections": _json_arg(args.extra_sections_json, []),
        "figure_caption_overrides": _json_arg(args.caption_overrides_json, {}),
    }
    data = _live_client(args).save_report_root_metadata(**payload_args)
    payload = _coerce_live_envelope("report.metadata.save", data, message="report metadata saved")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_doc_config_get(args: argparse.Namespace) -> int:
    data = _live_client(args).get_report_doc_config(root_path=args.root, source_path=args.source)
    payload = _coerce_live_envelope("report.doc_config.get", data, message=f"figure_start_number={data.get('figure_start_number', '-')}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_doc_config_save(args: argparse.Namespace) -> int:
    payload_args = {
        "root_path": args.root,
        "source_path": args.source,
        "figure_start_number": args.figure_start_number,
        "extra_sections": _json_arg(args.extra_sections_json, []),
        "figure_caption_overrides": _json_arg(args.caption_overrides_json, {}),
    }
    data = _live_client(args).save_report_doc_config(**payload_args)
    payload = _coerce_live_envelope("report.doc_config.save", data, message="report doc config saved")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_delete_source(args: argparse.Namespace) -> int:
    data = _live_client(args).delete_report_source(root_path=args.root, source_path=args.source)
    payload = _coerce_live_envelope("report.source.delete", data, message=f"deleted source: {data.get('deleted') or '-'}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_delete_images(args: argparse.Namespace) -> int:
    data = _live_client(args).delete_all_report_images(
        root_path=args.root,
        source_path=args.source,
        output_path=args.output,
        scope=args.scope,
    )
    payload = _coerce_live_envelope("report.images.delete_all", data, message=f"deleted images: {data.get('deleted_count', 0)}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_delete_image(args: argparse.Namespace) -> int:
    data = _live_client(args).delete_report_image(
        root_path=args.root,
        source_path=args.source,
        output_path=args.output,
        images_root_path=args.images_root,
        path=args.path,
    )
    payload = _coerce_live_envelope("report.image.delete", data, message=f"deleted image: {data.get('deleted') or '-'}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_graphs(args: argparse.Namespace) -> int:
    data = _live_client(args).trigger_report_graphs(
        root_path=args.root,
        source_path=args.source,
        output_path=args.output,
        linked_path=args.linked,
        scripts=args.scripts,
    )
    payload = _coerce_live_envelope("report.graphs", data, message=f"graphs: {data.get('status') or '-'}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_render(args: argparse.Namespace) -> int:
    payload_args = {
        "root_path": args.root,
        "source_path": args.source,
        "output_path": args.output,
        "linked_path": args.linked,
        "dpi": args.dpi,
        "render_mode": args.mode,
        "otofigure_style": args.otofigure_style,
        "otofigure_ray_trace": not bool(args.no_ray_trace),
        "otofigure_render_engine": args.otofigure_engine,
        "otofigure_background": args.background,
        "otofigure_surface_enabled": not bool(args.no_surface),
        "otofigure_surface_opacity": args.surface_opacity,
        "otofigure_protein_color": args.protein_color,
        "otofigure_ligand_thickness": args.ligand_thickness,
        "otofigure_far_ratio": args.far_ratio,
        "otofigure_close_ratio": args.close_ratio,
        "otofigure_interaction_ratio": args.interaction_ratio,
        "otofigure_far_padding": args.far_padding,
        "otofigure_far_frame_margin": args.far_frame_margin,
        "otofigure_close_padding": args.close_padding,
        "receptors": list(args.receptors or []),
        "run_by_receptor": _json_arg(args.run_by_receptor_json, {}),
        "ligand_by_receptor": _json_arg(args.ligand_by_receptor_json, {}),
        "is_preview": bool(args.preview),
    }
    data = _live_client(args).trigger_report_render(**payload_args)
    payload = _coerce_live_envelope("report.render", data, message=f"render: {data.get('status') or '-'}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_render_stop(args: argparse.Namespace) -> int:
    data = _live_client(args).stop_report_render()
    payload = _coerce_live_envelope("report.render.stop", data, message=str(data.get("message") or f"render: {data.get('status') or '-'}"))
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_compile(args: argparse.Namespace) -> int:
    payload_args = {
        "root_path": args.root,
        "source_path": args.source,
        "output_path": args.output,
        "images_root_path": args.images_root,
        "selected_images": list(args.images or []),
        "figure_captions": _json_arg(args.captions_json, {}),
        "figure_start_number": args.figure_start_number,
        "extra_sections": _json_arg(args.extra_sections_json, []),
    }
    data = _live_client(args).compile_report(**payload_args)
    payload = _coerce_live_envelope("report.compile", data, message=f"report compile: {data.get('status') or '-'} {data.get('doc_path') or ''}".strip())
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


def cmd_live_report_status(args: argparse.Namespace) -> int:
    data = _live_client(args).get_report_status()
    payload = _coerce_live_envelope("report.status", data, message=f"report: {data.get('status') or '-'} {data.get('progress', 0)}/{data.get('total', 0)}")
    _print_payload(payload, as_json=args.json, pretty=args.pretty)
    return 0 if payload["ok"] else 2


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

    live_assets = live_sub.add_parser("assets", help="Inspect live receptor/ligand inventory")
    live_assets_sub = live_assets.add_subparsers(dest="assets_cmd", required=True)
    live_assets_inspect = live_assets_sub.add_parser("inspect", help="Inspect loaded receptors and active ligands")
    add_live_output_flags(live_assets_inspect, suppress_default=True)
    live_assets_inspect.set_defaults(func=cmd_live_assets_inspect)

    live_run = live_sub.add_parser("run", help="Run-related live commands")
    live_run_sub = live_run.add_subparsers(dest="run_cmd", required=True)
    live_run_status = live_run_sub.add_parser("status", help="Read live run status")
    add_live_output_flags(live_run_status, suppress_default=True)
    live_run_status.set_defaults(func=cmd_live_run_status)
    live_run_start = live_run_sub.add_parser("start", help="Start the live queue")
    live_run_start.add_argument("--test-mode", action="store_true", help="Start a test/log run without heavy docking")
    live_run_start.add_argument("--batch-id", type=int, default=None, help="Optional queue batch id to run")
    add_live_output_flags(live_run_start, suppress_default=True)
    live_run_start.set_defaults(func=cmd_live_run_start)
    live_run_stop = live_run_sub.add_parser("stop", help="Stop the active live run")
    add_live_output_flags(live_run_stop, suppress_default=True)
    live_run_stop.set_defaults(func=cmd_live_run_stop)

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
    live_receptor_delete = live_receptor_sub.add_parser("delete", help="Delete a stored receptor file from live state")
    live_receptor_delete.add_argument("target")
    add_live_output_flags(live_receptor_delete, suppress_default=True)
    live_receptor_delete.set_defaults(func=cmd_live_receptor_delete)
    live_receptor_clear = live_receptor_sub.add_parser("clear", help="Clear all stored receptors from live state")
    add_live_output_flags(live_receptor_clear, suppress_default=True)
    live_receptor_clear.set_defaults(func=cmd_live_receptor_clear)

    live_ligand = live_sub.add_parser("ligand", help="Ligand-related live commands")
    live_ligand_sub = live_ligand.add_subparsers(dest="ligand_cmd", required=True)
    live_ligand_list = live_ligand_sub.add_parser("list", help="List stored ligands")
    add_live_output_flags(live_ligand_list, suppress_default=True)
    live_ligand_list.set_defaults(func=cmd_live_ligand_list)
    live_ligand_fetch = live_ligand_sub.add_parser("fetch", help="Fetch ligand structures into live state")
    live_ligand_fetch.add_argument("ligands", nargs="+")
    add_live_output_flags(live_ligand_fetch, suppress_default=True)
    live_ligand_fetch.set_defaults(func=cmd_live_ligand_fetch)
    live_ligand_delete = live_ligand_sub.add_parser("delete", help="Delete one stored ligand")
    live_ligand_delete.add_argument("name")
    add_live_output_flags(live_ligand_delete, suppress_default=True)
    live_ligand_delete.set_defaults(func=cmd_live_ligand_delete)
    live_ligand_clear = live_ligand_sub.add_parser("clear", help="Clear all stored ligands")
    add_live_output_flags(live_ligand_clear, suppress_default=True)
    live_ligand_clear.set_defaults(func=cmd_live_ligand_clear)

    live_viewer = live_sub.add_parser("viewer", help="Viewer-related live commands")
    live_viewer_sub = live_viewer.add_subparsers(dest="viewer_cmd", required=True)
    live_viewer_show = live_viewer_sub.add_parser("show", help="Select receptor and verify viewer data is available")
    live_viewer_show.add_argument("pdb_id")
    live_viewer_show.add_argument("--chain", default="")
    add_live_output_flags(live_viewer_show, suppress_default=True)
    live_viewer_show.set_defaults(func=cmd_live_viewer_show)
    live_viewer_residues = live_viewer_sub.add_parser("residues", help="List/highlight residues such as TRP in the live viewer")
    live_viewer_residues.add_argument("pdb_id", nargs="?", default="")
    live_viewer_residues.add_argument("--residue", default="TRP", help="Residue code or name, e.g. TRP or tryptophan")
    live_viewer_residues.add_argument("--chain", default="all")
    add_live_output_flags(live_viewer_residues, suppress_default=True)
    live_viewer_residues.set_defaults(func=cmd_live_viewer_residues)

    live_workspace = live_sub.add_parser("workspace", help="Workspace-related live commands")
    live_workspace_sub = live_workspace.add_subparsers(dest="workspace_cmd", required=True)
    live_workspace_select = live_workspace_sub.add_parser("select", help="Select receptor/native/dock ligands for docking")
    live_workspace_select.add_argument("receptor", nargs="?", default="all")
    live_workspace_select.add_argument("--chain", default="auto")
    live_workspace_select.add_argument("--native-ligand", default="auto")
    live_workspace_select.add_argument("--dock-ligands", default="all")
    add_live_output_flags(live_workspace_select, suppress_default=True)
    live_workspace_select.set_defaults(func=cmd_live_workspace_select)

    live_gridbox = live_sub.add_parser("gridbox", help="Gridbox-related live commands")
    live_gridbox_sub = live_gridbox.add_subparsers(dest="gridbox_cmd", required=True)
    live_gridbox_set = live_gridbox_sub.add_parser("set", help="Set a live gridbox")
    live_gridbox_set.add_argument("--method", default="native_ligand", choices=["native_ligand", "current_selection", "manual", "p2rank", "gridfinder", "auto"])
    live_gridbox_set.add_argument("--size", type=float, default=20.0)
    live_gridbox_set.add_argument("--padding", type=float, default=0.0)
    live_gridbox_set.add_argument("--center", default="", help="Manual center as x,y,z")
    live_gridbox_set.add_argument("--pocket-rank", type=int, default=1)
    live_gridbox_set.add_argument("--p2rank-mode", default="fit")
    add_live_output_flags(live_gridbox_set, suppress_default=True)
    live_gridbox_set.set_defaults(func=cmd_live_gridbox_set)

    live_config = live_sub.add_parser("config", help="Config-related live commands")
    live_config_sub = live_config.add_subparsers(dest="config_cmd", required=True)
    live_config_set = live_config_sub.add_parser("set", help="Set live docking config")
    live_config_set.add_argument("--engine", default="vina_gpu_21")
    live_config_set.add_argument("--mode", default="standard")
    live_config_set.add_argument("--runs", type=int, default=1)
    live_config_set.add_argument("--padding", type=float, default=0.0)
    live_config_set.add_argument("--out-root-name", default="")
    live_config_set.add_argument("--exhaustiveness", type=int, default=None)
    live_config_set.add_argument("--num-modes", type=int, default=None)
    live_config_set.add_argument("--energy-range", type=float, default=None)
    live_config_set.add_argument("--cpu", type=int, default=None)
    live_config_set.add_argument("--seed", type=int, default=None)
    live_config_set.add_argument("--ph", type=float, default=None)
    live_config_set.add_argument("--advanced", default="")
    add_live_output_flags(live_config_set, suppress_default=True)
    live_config_set.set_defaults(func=cmd_live_config_set)

    live_queue = live_sub.add_parser("queue", help="Queue-related live commands")
    live_queue_sub = live_queue.add_subparsers(dest="queue_cmd", required=True)
    live_queue_list = live_queue_sub.add_parser("list", help="List live queue jobs")
    add_live_output_flags(live_queue_list, suppress_default=True)
    live_queue_list.set_defaults(func=cmd_live_queue_list)
    live_queue_build = live_queue_sub.add_parser("build", help="Build live queue from current workspace/config/gridbox")
    live_queue_build.add_argument("--append", action="store_true", help="Append jobs instead of replacing queue")
    add_live_output_flags(live_queue_build, suppress_default=True)
    live_queue_build.set_defaults(func=cmd_live_queue_build)
    live_queue_remove = live_queue_sub.add_parser("remove", help="Remove one queue batch")
    live_queue_remove.add_argument("batch_id")
    add_live_output_flags(live_queue_remove, suppress_default=True)
    live_queue_remove.set_defaults(func=cmd_live_queue_remove)

    live_results = live_sub.add_parser("results", help="Results-page live commands")
    live_results_sub = live_results.add_subparsers(dest="results_cmd", required=True)
    live_results_folders = live_results_sub.add_parser("folders", help="List dock result roots")
    add_live_output_flags(live_results_folders, suppress_default=True)
    live_results_folders.set_defaults(func=cmd_live_results_folders)
    live_results_scan = live_results_sub.add_parser("scan", help="Scan result folders")
    live_results_scan.add_argument("--root", default="data/dock")
    add_live_output_flags(live_results_scan, suppress_default=True)
    live_results_scan.set_defaults(func=cmd_live_results_scan)
    live_results_detail = live_results_sub.add_parser("detail", help="Read one result detail")
    live_results_detail.add_argument("result_dir")
    add_live_output_flags(live_results_detail, suppress_default=True)
    live_results_detail.set_defaults(func=cmd_live_results_detail)

    def add_report_path_flags(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--root", default="data/dock", help="Report root path")
        cmd.add_argument("--source", default="", help="Report source path")
        cmd.add_argument("--output", default="", help="Report output path")

    live_report = live_sub.add_parser("report", help="Report-page live commands")
    live_report_sub = live_report.add_subparsers(dest="report_cmd", required=True)

    report_list = live_report_sub.add_parser("list", help="List report sources, receptors, outputs, and images")
    add_report_path_flags(report_list)
    report_list.add_argument("--linked", default="", help="Optional linked path")
    add_live_output_flags(report_list, suppress_default=True)
    report_list.set_defaults(func=cmd_live_report_list)

    report_preview = live_report_sub.add_parser("preview", help="Resolve report preview context")
    add_report_path_flags(report_preview)
    report_preview.add_argument("--receptor", default="", help="Optional receptor id")
    report_preview.add_argument("--run", default="", help="Optional run name")
    report_preview.add_argument("--mode", default="", help="Render mode")
    add_live_output_flags(report_preview, suppress_default=True)
    report_preview.set_defaults(func=cmd_live_report_preview)

    report_images = live_report_sub.add_parser("images", help="List report images")
    add_report_path_flags(report_images)
    report_images.add_argument("--images-root", default="", help="Optional images root path")
    add_live_output_flags(report_images, suppress_default=True)
    report_images.set_defaults(func=cmd_live_report_images)

    report_metadata = live_report_sub.add_parser("metadata", help="Report source metadata commands")
    report_metadata_sub = report_metadata.add_subparsers(dest="metadata_cmd", required=True)
    report_metadata_get = report_metadata_sub.add_parser("get", help="Read report source metadata")
    report_metadata_get.add_argument("--root", default="data/dock")
    report_metadata_get.add_argument("--source", default="")
    add_live_output_flags(report_metadata_get, suppress_default=True)
    report_metadata_get.set_defaults(func=cmd_live_report_metadata_get)
    report_metadata_save = report_metadata_sub.add_parser("save", help="Save report source metadata")
    report_metadata_save.add_argument("--root", default="data/dock")
    report_metadata_save.add_argument("--source", default="")
    report_metadata_save.add_argument("--reset", action="store_true")
    report_metadata_save.add_argument("--main-type", default="")
    report_metadata_save.add_argument("--receptor-labels-json", default="{}")
    report_metadata_save.add_argument("--ligand-labels-json", default="{}")
    report_metadata_save.add_argument("--receptor-order", default="", help="Comma-separated receptor order")
    report_metadata_save.add_argument("--ligand-order", default="", help="Comma-separated ligand order")
    report_metadata_save.add_argument("--figure-start-number", type=int, default=1)
    report_metadata_save.add_argument("--extra-sections-json", default="[]")
    report_metadata_save.add_argument("--caption-overrides-json", default="{}")
    add_live_output_flags(report_metadata_save, suppress_default=True)
    report_metadata_save.set_defaults(func=cmd_live_report_metadata_save)

    report_doc_config = live_report_sub.add_parser("doc-config", help="Report document configuration commands")
    report_doc_config_sub = report_doc_config.add_subparsers(dest="doc_config_cmd", required=True)
    report_doc_config_get = report_doc_config_sub.add_parser("get", help="Read report document config")
    report_doc_config_get.add_argument("--root", default="data/dock")
    report_doc_config_get.add_argument("--source", default="")
    add_live_output_flags(report_doc_config_get, suppress_default=True)
    report_doc_config_get.set_defaults(func=cmd_live_report_doc_config_get)
    report_doc_config_save = report_doc_config_sub.add_parser("save", help="Save report document config")
    report_doc_config_save.add_argument("--root", default="data/dock")
    report_doc_config_save.add_argument("--source", default="")
    report_doc_config_save.add_argument("--figure-start-number", type=int, default=1)
    report_doc_config_save.add_argument("--extra-sections-json", default="[]")
    report_doc_config_save.add_argument("--caption-overrides-json", default="{}")
    add_live_output_flags(report_doc_config_save, suppress_default=True)
    report_doc_config_save.set_defaults(func=cmd_live_report_doc_config_save)

    report_delete_source = live_report_sub.add_parser("delete-source", help="Delete a first-level report source folder")
    report_delete_source.add_argument("--root", default="data/dock")
    report_delete_source.add_argument("--source", required=True)
    add_live_output_flags(report_delete_source, suppress_default=True)
    report_delete_source.set_defaults(func=cmd_live_report_delete_source)

    report_delete_images = live_report_sub.add_parser("delete-images", help="Delete generated report images")
    add_report_path_flags(report_delete_images)
    report_delete_images.add_argument("--scope", default="all", choices=["all", "render", "plot", "plots", "graphs"])
    add_live_output_flags(report_delete_images, suppress_default=True)
    report_delete_images.set_defaults(func=cmd_live_report_delete_images)

    report_delete_image = live_report_sub.add_parser("delete-image", help="Delete one generated report image")
    add_report_path_flags(report_delete_image)
    report_delete_image.add_argument("--images-root", default="")
    report_delete_image.add_argument("path")
    add_live_output_flags(report_delete_image, suppress_default=True)
    report_delete_image.set_defaults(func=cmd_live_report_delete_image)

    report_graphs = live_report_sub.add_parser("graphs", help="Generate predefined report plots")
    add_report_path_flags(report_graphs)
    report_graphs.add_argument("--linked", default="")
    report_graphs.add_argument("--scripts", nargs="*", default=[], help="Optional plot ids; empty means all predefined plots")
    add_live_output_flags(report_graphs, suppress_default=True)
    report_graphs.set_defaults(func=cmd_live_report_graphs)

    report_render = live_report_sub.add_parser("render", help="Start report image rendering")
    add_report_path_flags(report_render)
    report_render.add_argument("--linked", default="")
    report_render.add_argument("--dpi", type=int, default=120)
    report_render.add_argument("--mode", default="classic", choices=["classic", "otofigure", "multi_ligand", "multi_ligand_panel"])
    report_render.add_argument("--receptors", nargs="*", default=[])
    report_render.add_argument("--preview", action="store_true")
    report_render.add_argument("--run-by-receptor-json", default="{}")
    report_render.add_argument("--ligand-by-receptor-json", default="{}")
    report_render.add_argument("--otofigure-style", default="balanced")
    report_render.add_argument("--otofigure-engine", default="ray")
    report_render.add_argument("--background", default="transparent")
    report_render.add_argument("--no-ray-trace", action="store_true")
    report_render.add_argument("--no-surface", action="store_true")
    report_render.add_argument("--surface-opacity", type=float, default=0.5)
    report_render.add_argument("--protein-color", default="bluewhite")
    report_render.add_argument("--ligand-thickness", type=float, default=0.22)
    report_render.add_argument("--far-ratio", type=int, default=4)
    report_render.add_argument("--close-ratio", type=int, default=2)
    report_render.add_argument("--interaction-ratio", type=int, default=3)
    report_render.add_argument("--far-padding", type=float, default=0.03)
    report_render.add_argument("--far-frame-margin", type=float, default=0.03)
    report_render.add_argument("--close-padding", type=float, default=0.20)
    add_live_output_flags(report_render, suppress_default=True)
    report_render.set_defaults(func=cmd_live_report_render)

    report_render_stop = live_report_sub.add_parser("render-stop", help="Stop active report render task")
    add_live_output_flags(report_render_stop, suppress_default=True)
    report_render_stop.set_defaults(func=cmd_live_report_render_stop)

    report_compile = live_report_sub.add_parser("compile", help="Compile a report document")
    add_report_path_flags(report_compile)
    report_compile.add_argument("--images-root", default="")
    report_compile.add_argument("--images", nargs="*", default=[])
    report_compile.add_argument("--captions-json", default="{}")
    report_compile.add_argument("--figure-start-number", type=int, default=1)
    report_compile.add_argument("--extra-sections-json", default="[]")
    add_live_output_flags(report_compile, suppress_default=True)
    report_compile.set_defaults(func=cmd_live_report_compile)

    report_status = live_report_sub.add_parser("status", help="Read report task status")
    add_live_output_flags(report_status, suppress_default=True)
    report_status.set_defaults(func=cmd_live_report_status)

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
