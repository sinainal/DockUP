from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PREP_VERSION = "dockup-prepared-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _abs(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _read_grid(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key in {"center_x", "center_y", "center_z", "size_x", "size_y", "size_z"}:
            values[key] = float(value.strip())
    return values


def plan(
    *,
    prepared_root: str | Path,
    pdb_id: str,
    chain: str,
    ligand_resname: str,
    receptor_pdb: str | Path,
    ligand_sdf: str | Path,
    grid_file: str | Path,
    flexres: str = "",
    mkrec_allow_bad_res: str = "1",
    mkrec_default_altloc: str = "A",
    pdb2pqr_ph: str = "",
    pdb2pqr_ff: str = "",
    pdb2pqr_ffout: str = "",
    pdb2pqr_nodebump: str = "",
    pdb2pqr_keep_chain: str = "",
    ligand_source_name: str = "",
) -> dict[str, Any]:
    root = _abs(prepared_root)
    receptor_path = _abs(receptor_pdb)
    ligand_path = _abs(ligand_sdf)
    grid_path = _abs(grid_file)
    pdb_norm = str(pdb_id or "").strip().upper()
    chain_norm = str(chain or "all").strip() or "all"
    flex_norm = str(flexres or "").strip()
    receptor_payload = {
        "kind": "receptor",
        "schema_version": SCHEMA_VERSION,
        "prep_version": PREP_VERSION,
        "pdb_id": pdb_norm,
        "chain": chain_norm,
        "flexres": flex_norm,
        "receptor_sha256": _sha256_file(receptor_path),
        "grid": _read_grid(grid_path),
        "mkrec_allow_bad_res": str(mkrec_allow_bad_res or ""),
        "mkrec_default_altloc": str(mkrec_default_altloc or ""),
        "pdb2pqr_ph": str(pdb2pqr_ph or ""),
        "pdb2pqr_ff": str(pdb2pqr_ff or ""),
        "pdb2pqr_ffout": str(pdb2pqr_ffout or ""),
        "pdb2pqr_nodebump": str(pdb2pqr_nodebump or ""),
        "pdb2pqr_keep_chain": str(pdb2pqr_keep_chain or ""),
    }
    ligand_payload = {
        "kind": "ligand",
        "schema_version": SCHEMA_VERSION,
        "prep_version": PREP_VERSION,
        "pdb_id": pdb_norm,
        "ligand_resname": str(ligand_resname or ""),
        "ligand_source_name": str(ligand_source_name or ""),
        "ligand_sha256": _sha256_file(ligand_path),
    }
    grid_payload = {
        "kind": "grid",
        "schema_version": SCHEMA_VERSION,
        "prep_version": PREP_VERSION,
        "pdb_id": pdb_norm,
        "grid": _read_grid(grid_path),
        "grid_sha256": _sha256_file(grid_path),
    }
    receptor_id = _stable_hash(receptor_payload)
    ligand_id = _stable_hash(ligand_payload)
    grid_id = _stable_hash(grid_payload)
    receptor_dir = root / "receptors" / receptor_id
    ligand_dir = root / "ligands" / ligand_id
    grid_dir = root / "grids" / grid_id
    receptor = {
        "id": receptor_id,
        "dir": str(receptor_dir),
        "rigid_pdbqt": str(receptor_dir / f"{pdb_norm}_rigid.pdbqt" if flex_norm else receptor_dir / f"{pdb_norm}_receptor.pdbqt"),
        "flex_pdbqt": str(receptor_dir / f"{pdb_norm}_flex.pdbqt") if flex_norm else "",
        "receptor_json": str(receptor_dir / f"{pdb_norm}.json"),
        "source_pdb": str(receptor_path),
        "metadata": receptor_payload,
    }
    ligand = {
        "id": ligand_id,
        "dir": str(ligand_dir),
        "pdbqt": str(ligand_dir / f"{pdb_norm}_ligand.pdbqt"),
        "fixed_sdf": str(ligand_dir / f"{pdb_norm}_ligand_fixed.sdf"),
        "source_sdf": str(ligand_path),
        "metadata": ligand_payload,
    }
    grid = {
        "id": grid_id,
        "dir": str(grid_dir),
        "grid_file": str(grid_dir / f"{pdb_norm}_gridbox.txt"),
        "source_grid": str(grid_path),
        "metadata": grid_payload,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "prepared_root": str(root),
        "receptor": receptor,
        "ligand": ligand,
        "grid": grid,
        "cache_hit": {
            "receptor": Path(receptor["rigid_pdbqt"]).exists() and (not receptor["flex_pdbqt"] or Path(receptor["flex_pdbqt"]).exists()),
            "ligand": Path(ligand["pdbqt"]).exists() and Path(ligand["fixed_sdf"]).exists(),
            "grid": Path(grid["grid_file"]).exists(),
        },
    }


def plan_receptor_input(
    *,
    prepared_root: str | Path,
    pdb_id: str,
    chain: str,
    ligand_resname: str,
    source_pdb: str | Path,
    pdb2pqr_ph: str = "",
    pdb2pqr_ff: str = "",
    pdb2pqr_ffout: str = "",
    pdb2pqr_nodebump: str = "",
    pdb2pqr_keep_chain: str = "",
) -> dict[str, Any]:
    root = _abs(prepared_root)
    source_path = _abs(source_pdb)
    pdb_norm = str(pdb_id or "").strip().upper()
    chain_norm = str(chain or "all").strip() or "all"
    payload = {
        "kind": "receptor_input",
        "schema_version": SCHEMA_VERSION,
        "prep_version": PREP_VERSION,
        "pdb_id": pdb_norm,
        "chain": chain_norm,
        "ligand_resname": str(ligand_resname or ""),
        "source_pdb_sha256": _sha256_file(source_path),
        "pdb2pqr_ph": str(pdb2pqr_ph or ""),
        "pdb2pqr_ff": str(pdb2pqr_ff or ""),
        "pdb2pqr_ffout": str(pdb2pqr_ffout or ""),
        "pdb2pqr_nodebump": str(pdb2pqr_nodebump or ""),
        "pdb2pqr_keep_chain": str(pdb2pqr_keep_chain or ""),
    }
    input_id = _stable_hash(payload)
    input_dir = root / "receptor_inputs" / input_id
    raw_pdb = input_dir / f"{pdb_norm}_rec_raw.pdb"
    pqr = input_dir / f"{pdb_norm}_rec.pqr"
    return {
        "schema_version": SCHEMA_VERSION,
        "prepared_root": str(root),
        "receptor_input": {
            "id": input_id,
            "dir": str(input_dir),
            "raw_pdb": str(raw_pdb),
            "pqr": str(pqr),
            "source_pdb": str(source_path),
            "metadata": payload,
        },
        "cache_hit": {
            "raw_pdb": raw_pdb.exists(),
            "pqr": pqr.exists(),
        },
    }


def _install_file(src: str | Path, dst: str | Path) -> None:
    src_path = _abs(src)
    dst_path = _abs(dst)
    if not src_path.exists():
        raise FileNotFoundError(str(src_path))
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        return
    tmp = dst_path.with_name(f".{dst_path.name}.{os.getpid()}.tmp")
    shutil.copy2(src_path, tmp)
    tmp.replace(dst_path)


def install(plan_payload: dict[str, Any], *, sources: dict[str, str]) -> dict[str, Any]:
    receptor = plan_payload["receptor"]
    ligand = plan_payload["ligand"]
    grid = plan_payload["grid"]
    installed: list[str] = []
    pairs = [
        ("rigid_pdbqt", sources.get("rigid_pdbqt"), receptor.get("rigid_pdbqt")),
        ("flex_pdbqt", sources.get("flex_pdbqt"), receptor.get("flex_pdbqt")),
        ("receptor_json", sources.get("receptor_json"), receptor.get("receptor_json")),
        ("ligand_pdbqt", sources.get("ligand_pdbqt"), ligand.get("pdbqt")),
        ("ligand_fixed_sdf", sources.get("ligand_fixed_sdf"), ligand.get("fixed_sdf")),
        ("grid_file", sources.get("grid_file"), grid.get("grid_file")),
    ]
    for label, src, dst in pairs:
        if not src or not dst:
            continue
        if label in {"flex_pdbqt", "receptor_json"} and not Path(src).expanduser().exists():
            continue
        _install_file(src, dst)
        installed.append(label)
    manifest = {
        **plan_payload,
        "installed_labels": installed,
        "updated_at": time.time(),
    }
    root = Path(plan_payload["prepared_root"])
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    existing: dict[str, Any] = {}
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        existing = {"schema_version": SCHEMA_VERSION, "artifacts": []}
    artifacts = existing.get("artifacts") if isinstance(existing.get("artifacts"), list) else []
    artifacts.append(
        {
            "receptor_id": receptor["id"],
            "ligand_id": ligand["id"],
            "grid_id": grid["id"],
            "updated_at": manifest["updated_at"],
        }
    )
    existing["schema_version"] = SCHEMA_VERSION
    existing["artifacts"] = artifacts[-5000:]
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(manifest_path)
    return manifest


def install_receptor_input(plan_payload: dict[str, Any], *, sources: dict[str, str]) -> dict[str, Any]:
    receptor_input = plan_payload["receptor_input"]
    installed: list[str] = []
    for label, src, dst in (
        ("raw_pdb", sources.get("raw_pdb"), receptor_input.get("raw_pdb")),
        ("pqr", sources.get("pqr"), receptor_input.get("pqr")),
    ):
        if not src or not dst:
            continue
        _install_file(src, dst)
        installed.append(label)
    manifest = {
        **plan_payload,
        "installed_labels": installed,
        "updated_at": time.time(),
    }
    root = Path(plan_payload["prepared_root"])
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        existing = {"schema_version": SCHEMA_VERSION, "artifacts": []}
    artifacts = existing.get("artifacts") if isinstance(existing.get("artifacts"), list) else []
    artifacts.append(
        {
            "receptor_input_id": receptor_input["id"],
            "updated_at": manifest["updated_at"],
        }
    )
    existing["schema_version"] = SCHEMA_VERSION
    existing["artifacts"] = artifacts[-5000:]
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(manifest_path)
    return manifest


def _shell_quote(value: str) -> str:
    import shlex

    return shlex.quote(str(value))


def _print_shell(plan_payload: dict[str, Any]) -> None:
    receptor = plan_payload["receptor"]
    ligand = plan_payload["ligand"]
    grid = plan_payload["grid"]
    hit = plan_payload["cache_hit"]
    values = {
        "DOCKUP_PREPARED_ENABLED": "1",
        "DOCKUP_PREPARED_PLAN_JSON": json.dumps(plan_payload, sort_keys=True, separators=(",", ":")),
        "DOCKUP_PREPARED_ROOT": plan_payload["prepared_root"],
        "DOCKUP_PREPARED_RECEPTOR_ID": receptor["id"],
        "DOCKUP_PREPARED_LIGAND_ID": ligand["id"],
        "DOCKUP_PREPARED_GRID_ID": grid["id"],
        "DOCKUP_PREPARED_RECEPTOR_HIT": "1" if hit["receptor"] else "0",
        "DOCKUP_PREPARED_LIGAND_HIT": "1" if hit["ligand"] else "0",
        "DOCKUP_PREPARED_GRID_HIT": "1" if hit["grid"] else "0",
        "DOCKUP_PREPARED_RIGID_PDBQT": receptor["rigid_pdbqt"],
        "DOCKUP_PREPARED_FLEX_PDBQT": receptor["flex_pdbqt"],
        "DOCKUP_PREPARED_RECEPTOR_JSON": receptor["receptor_json"],
        "DOCKUP_PREPARED_LIGAND_PDBQT": ligand["pdbqt"],
        "DOCKUP_PREPARED_LIGAND_FIXED_SDF": ligand["fixed_sdf"],
        "DOCKUP_PREPARED_GRID_FILE": grid["grid_file"],
    }
    for key, value in values.items():
        print(f"{key}={_shell_quote(value)}")


def _print_receptor_input_shell(plan_payload: dict[str, Any]) -> None:
    receptor_input = plan_payload["receptor_input"]
    hit = plan_payload["cache_hit"]
    values = {
        "DOCKUP_PREPARED_INPUT_ENABLED": "1",
        "DOCKUP_PREPARED_INPUT_PLAN_JSON": json.dumps(plan_payload, sort_keys=True, separators=(",", ":")),
        "DOCKUP_PREPARED_INPUT_ID": receptor_input["id"],
        "DOCKUP_PREPARED_INPUT_RAW_HIT": "1" if hit["raw_pdb"] else "0",
        "DOCKUP_PREPARED_INPUT_PQR_HIT": "1" if hit["pqr"] else "0",
        "DOCKUP_PREPARED_INPUT_RAW_PDB": receptor_input["raw_pdb"],
        "DOCKUP_PREPARED_INPUT_PQR": receptor_input["pqr"],
    }
    for key, value in values.items():
        print(f"{key}={_shell_quote(value)}")


def _cmd_plan(args: argparse.Namespace) -> int:
    payload = plan(**{key: value for key, value in vars(args).items() if key not in {"cmd", "func"}})
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_plan_shell(args: argparse.Namespace) -> int:
    payload = plan(**{key: value for key, value in vars(args).items() if key not in {"cmd", "func"}})
    _print_shell(payload)
    return 0


def _cmd_plan_receptor_input_shell(args: argparse.Namespace) -> int:
    payload = plan_receptor_input(**{key: value for key, value in vars(args).items() if key not in {"cmd", "func"}})
    _print_receptor_input_shell(payload)
    return 0


def _cmd_install_receptor_input(args: argparse.Namespace) -> int:
    payload = json.loads(args.plan_json)
    result = install_receptor_input(
        payload,
        sources={
            "raw_pdb": args.raw_pdb,
            "pqr": args.pqr,
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    payload = json.loads(args.plan_json)
    result = install(
        payload,
        sources={
            "rigid_pdbqt": args.rigid_pdbqt,
            "flex_pdbqt": args.flex_pdbqt,
            "receptor_json": args.receptor_json,
            "ligand_pdbqt": args.ligand_pdbqt,
            "ligand_fixed_sdf": args.ligand_fixed_sdf,
            "grid_file": args.grid_file,
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockUP prepared artifact store helper")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "plan-shell"):
        p = sub.add_parser(name)
        p.add_argument("--prepared-root", required=True)
        p.add_argument("--pdb-id", required=True)
        p.add_argument("--chain", default="all")
        p.add_argument("--ligand-resname", default="")
        p.add_argument("--receptor-pdb", required=True)
        p.add_argument("--ligand-sdf", required=True)
        p.add_argument("--grid-file", required=True)
        p.add_argument("--flexres", default="")
        p.add_argument("--mkrec-allow-bad-res", default="1")
        p.add_argument("--mkrec-default-altloc", default="A")
        p.add_argument("--pdb2pqr-ph", default="")
        p.add_argument("--pdb2pqr-ff", default="")
        p.add_argument("--pdb2pqr-ffout", default="")
        p.add_argument("--pdb2pqr-nodebump", default="")
        p.add_argument("--pdb2pqr-keep-chain", default="")
        p.add_argument("--ligand-source-name", default="")
        p.set_defaults(func=_cmd_plan_shell if name == "plan-shell" else _cmd_plan)
    p = sub.add_parser("install")
    p.add_argument("--plan-json", required=True)
    p.add_argument("--rigid-pdbqt", default="")
    p.add_argument("--flex-pdbqt", default="")
    p.add_argument("--receptor-json", default="")
    p.add_argument("--ligand-pdbqt", default="")
    p.add_argument("--ligand-fixed-sdf", default="")
    p.add_argument("--grid-file", default="")
    p.set_defaults(func=_cmd_install)
    p = sub.add_parser("plan-receptor-input-shell")
    p.add_argument("--prepared-root", required=True)
    p.add_argument("--pdb-id", required=True)
    p.add_argument("--chain", default="all")
    p.add_argument("--ligand-resname", default="")
    p.add_argument("--source-pdb", required=True)
    p.add_argument("--pdb2pqr-ph", default="")
    p.add_argument("--pdb2pqr-ff", default="")
    p.add_argument("--pdb2pqr-ffout", default="")
    p.add_argument("--pdb2pqr-nodebump", default="")
    p.add_argument("--pdb2pqr-keep-chain", default="")
    p.set_defaults(func=_cmd_plan_receptor_input_shell)
    p = sub.add_parser("install-receptor-input")
    p.add_argument("--plan-json", required=True)
    p.add_argument("--raw-pdb", default="")
    p.add_argument("--pqr", default="")
    p.set_defaults(func=_cmd_install_receptor_input)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
