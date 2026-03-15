from __future__ import annotations

import csv
import gzip
from pathlib import Path
from typing import Any


def _normalize_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {str(key or "").strip(): value for key, value in raw.items()}


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def parse_predictions_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, skipinitialspace=True)
        for source in reader:
            raw = _normalize_row(source)
            rank = _to_int(raw.get("rank"))
            if rank is None:
                continue
            residue_ids = [item for item in str(raw.get("residue_ids") or "").split() if item]
            rows.append(
                {
                    "name": str(raw.get("name") or f"pocket{rank}").strip() or f"pocket{rank}",
                    "rank": rank,
                    "score": _to_float(raw.get("score")) or 0.0,
                    "probability": _to_float(raw.get("probability")) or 0.0,
                    "sas_points": _to_int(raw.get("sas_points")) or 0,
                    "surf_atoms": _to_int(raw.get("surf_atoms")) or 0,
                    "center_x": _to_float(raw.get("center_x")) or 0.0,
                    "center_y": _to_float(raw.get("center_y")) or 0.0,
                    "center_z": _to_float(raw.get("center_z")) or 0.0,
                    "residue_ids": residue_ids,
                    "surf_atom_ids": [item for item in str(raw.get("surf_atom_ids") or "").split() if item],
                }
            )
    return rows


def parse_residue_rows(path: Path) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    if not path.exists():
        return grouped
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, skipinitialspace=True)
        for source in reader:
            raw = _normalize_row(source)
            pocket_idx = _to_int(raw.get("pocket"))
            if pocket_idx is None or pocket_idx <= 0:
                continue
            row = {
                "chain": str(raw.get("chain") or "").strip(),
                "residue_label": str(raw.get("residue_label") or "").strip(),
                "residue_name": str(raw.get("residue_name") or "").strip(),
                "score": _to_float(raw.get("score")) or 0.0,
                "zscore": _to_float(raw.get("zscore")),
                "probability": _to_float(raw.get("probability")) or 0.0,
            }
            grouped.setdefault(pocket_idx, []).append(row)

    for rows in grouped.values():
        rows.sort(key=lambda item: item.get("probability", 0.0), reverse=True)
    return grouped


def parse_point_bounds(path: Path) -> dict[int, dict[str, Any]]:
    bounds: dict[int, dict[str, Any]] = {}
    if not path.exists():
        return bounds
    with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(("HETATM", "ATOM")):
                continue
            parts = line.split()
            if len(parts) < 9:
                continue
            pocket_rank = _to_int(parts[4])
            if pocket_rank is None or pocket_rank <= 0:
                continue
            try:
                x = float(parts[6])
                y = float(parts[7])
                z = float(parts[8])
            except (TypeError, ValueError):
                continue
            row = bounds.setdefault(
                pocket_rank,
                {
                    "min_x": x,
                    "max_x": x,
                    "min_y": y,
                    "max_y": y,
                    "min_z": z,
                    "max_z": z,
                    "point_count": 0,
                },
            )
            row["min_x"] = min(row["min_x"], x)
            row["max_x"] = max(row["max_x"], x)
            row["min_y"] = min(row["min_y"], y)
            row["max_y"] = max(row["max_y"], y)
            row["min_z"] = min(row["min_z"], z)
            row["max_z"] = max(row["max_z"], z)
            row["point_count"] += 1

    for row in bounds.values():
        row["box_cx"] = (row["min_x"] + row["max_x"]) / 2.0
        row["box_cy"] = (row["min_y"] + row["max_y"]) / 2.0
        row["box_cz"] = (row["min_z"] + row["max_z"]) / 2.0
        row["box_sx"] = max(row["max_x"] - row["min_x"], 1.0)
        row["box_sy"] = max(row["max_y"] - row["min_y"], 1.0)
        row["box_sz"] = max(row["max_z"] - row["min_z"], 1.0)
    return bounds


def _parse_receptor_bounds(
    path: Path,
    atom_ids: set[int] | None = None,
    residue_tokens: set[tuple[str, str]] | None = None,
) -> dict[str, float] | None:
    if not path.exists():
        return None
    min_x = max_x = min_y = max_y = min_z = max_z = None
    matched = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(("ATOM", "HETATM")):
                continue
            try:
                serial = int(line[6:11].strip())
            except ValueError:
                serial = None
            chain = line[21:22].strip()
            residue_label = line[22:26].strip()
            use_row = False
            if atom_ids and serial in atom_ids:
                use_row = True
            elif residue_tokens and (chain, residue_label) in residue_tokens:
                use_row = True
            if not use_row:
                continue
            try:
                x = float(line[30:38].strip())
                y = float(line[38:46].strip())
                z = float(line[46:54].strip())
            except ValueError:
                continue
            min_x = x if min_x is None else min(min_x, x)
            max_x = x if max_x is None else max(max_x, x)
            min_y = y if min_y is None else min(min_y, y)
            max_y = y if max_y is None else max(max_y, y)
            min_z = z if min_z is None else min(min_z, z)
            max_z = z if max_z is None else max(max_z, z)
            matched += 1
    if not matched or None in (min_x, max_x, min_y, max_y, min_z, max_z):
        return None
    return {
        "min_x": float(min_x),
        "max_x": float(max_x),
        "min_y": float(min_y),
        "max_y": float(max_y),
        "min_z": float(min_z),
        "max_z": float(max_z),
        "point_count": matched,
        "box_cx": (float(min_x) + float(max_x)) / 2.0,
        "box_cy": (float(min_y) + float(max_y)) / 2.0,
        "box_cz": (float(min_z) + float(max_z)) / 2.0,
        "box_sx": max(float(max_x) - float(min_x), 1.0),
        "box_sy": max(float(max_y) - float(min_y), 1.0),
        "box_sz": max(float(max_z) - float(min_z), 1.0),
    }


def _guess_receptor_input_file(output_dir: Path) -> Path | None:
    parent = output_dir.parent
    exact = parent / f"{parent.name}.pdb"
    if exact.exists():
        return exact
    matches = sorted(parent.glob("*.pdb"))
    return matches[0] if matches else None


def _raw_box_bounds(prediction: dict[str, Any], output_dir: Path) -> dict[str, float] | None:
    receptor_file = _guess_receptor_input_file(output_dir)
    if receptor_file is None:
        return None
    atom_ids = {_to_int(item) for item in prediction.get("surf_atom_ids") or []}
    cleaned_atom_ids = {item for item in atom_ids if item is not None}
    if cleaned_atom_ids:
        bounds = _parse_receptor_bounds(receptor_file, atom_ids=cleaned_atom_ids)
        if bounds:
            return bounds
    residue_tokens = set()
    for token in prediction.get("residue_ids") or []:
        chain, _, residue = str(token).partition("_")
        chain = chain.strip()
        residue = residue.strip()
        if chain and residue:
            residue_tokens.add((chain, residue))
    if residue_tokens:
        return _parse_receptor_bounds(receptor_file, residue_tokens=residue_tokens)
    return None


def _detail_table(prediction: dict[str, Any], box_bounds: dict[str, Any] | None, residue_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    residue_count = len(prediction.get("residue_ids") or [])
    if box_bounds:
        fit_box = f"{box_bounds['box_sx']:.1f} × {box_bounds['box_sy']:.1f} × {box_bounds['box_sz']:.1f}"
    else:
        fit_box = "-"
    return [
        {"label": "Probability", "value": f"{prediction.get('probability', 0.0):.3f}"},
        {"label": "Score", "value": f"{prediction.get('score', 0.0):.2f}"},
        {"label": "Pocket points", "value": str(prediction.get("sas_points", 0))},
        {"label": "Surface atoms", "value": str(prediction.get("surf_atoms", 0))},
        {"label": "Residues", "value": str(residue_count)},
        {"label": "Center", "value": f"{prediction.get('center_x', 0.0):.2f}, {prediction.get('center_y', 0.0):.2f}, {prediction.get('center_z', 0.0):.2f}"},
        {"label": "Fit box (A)", "value": fit_box},
        {"label": "Top residue", "value": _top_residue_label(residue_rows)},
    ]


def _top_residue_label(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "-"
    top = rows[0]
    chain = str(top.get("chain") or "").strip()
    label = str(top.get("residue_label") or "").strip()
    name = str(top.get("residue_name") or "").strip()
    prob = float(top.get("probability") or 0.0)
    parts = [item for item in (chain, label, name) if item]
    return f"{' '.join(parts)} ({prob:.3f})" if parts else f"{prob:.3f}"


def build_pocket_response(output_dir: Path) -> dict[str, Any]:
    prediction_file = sorted(output_dir.glob("*_predictions.csv"))
    residue_file = sorted(output_dir.glob("*_residues.csv"))
    predictions = parse_predictions_csv(prediction_file[0]) if prediction_file else []
    residues_by_pocket = parse_residue_rows(residue_file[0]) if residue_file else {}
    point_file = sorted((output_dir / "visualizations" / "data").glob("*_points.pdb.gz"))
    point_bounds = parse_point_bounds(point_file[0]) if point_file else {}

    pockets: list[dict[str, Any]] = []
    for row in predictions:
        rank = int(row["rank"])
        residue_rows = residues_by_pocket.get(rank, [])
        raw_box = _raw_box_bounds(row, output_dir)
        bounds = raw_box or point_bounds.get(rank)
        pockets.append(
            {
                "name": row["name"],
                "rank": rank,
                "probability": row["probability"],
                "score": row["score"],
                "surface_atoms": row["surf_atoms"],
                "pocket_points": row["sas_points"],
                "residue_count": len(row["residue_ids"]),
                "center": {
                    "x": row["center_x"],
                    "y": row["center_y"],
                    "z": row["center_z"],
                },
                "box_preview": {
                    "cx": bounds.get("box_cx") if bounds else row["center_x"],
                    "cy": bounds.get("box_cy") if bounds else row["center_y"],
                    "cz": bounds.get("box_cz") if bounds else row["center_z"],
                    "sx": bounds.get("box_sx") if bounds else 20.0,
                    "sy": bounds.get("box_sy") if bounds else 20.0,
                    "sz": bounds.get("box_sz") if bounds else 20.0,
                },
                "residue_ids": row["residue_ids"],
                "top_residues": residue_rows[:8],
                "detail_rows": _detail_table(row, raw_box, residue_rows),
            }
        )

    return {
        "pockets": pockets,
        "prediction_columns": [
            "probability",
            "score",
            "pocket_points",
            "surface_atoms",
            "residue_count",
            "center",
        ],
        "missing_descriptor_fields": [
            "volume",
            "enclosure",
            "depth",
            "hydrophobicity",
            "drugScore",
        ],
    }


def compute_gridbox_for_pocket(output_dir: Path, pocket_rank: int, mode: str = "fit", fixed_size: float = 20.0, padding: float = 2.0) -> dict[str, float]:
    prediction_file = sorted(output_dir.glob("*_predictions.csv"))
    point_file = sorted((output_dir / "visualizations" / "data").glob("*_points.pdb.gz"))
    predictions = parse_predictions_csv(prediction_file[0]) if prediction_file else []
    prediction = next((item for item in predictions if int(item["rank"]) == int(pocket_rank)), None)
    if prediction is None:
        raise ValueError("Pocket not found.")

    if str(mode).lower() == "fixed":
        size = max(float(fixed_size or 20.0), 1.0)
        return {
            "cx": float(prediction["center_x"]),
            "cy": float(prediction["center_y"]),
            "cz": float(prediction["center_z"]),
            "sx": size,
            "sy": size,
            "sz": size,
        }

    bounds = _raw_box_bounds(prediction, output_dir)
    if not bounds:
        point_bounds = parse_point_bounds(point_file[0]) if point_file else {}
        bounds = point_bounds.get(int(pocket_rank))
    if not bounds:
        size = max(float(fixed_size or 20.0), 1.0)
        return {
            "cx": float(prediction["center_x"]),
            "cy": float(prediction["center_y"]),
            "cz": float(prediction["center_z"]),
            "sx": size,
            "sy": size,
            "sz": size,
        }

    pad = max(float(padding or 0.0), 0.0)
    return {
        "cx": float(bounds["box_cx"]),
        "cy": float(bounds["box_cy"]),
        "cz": float(bounds["box_cz"]),
        "sx": float(bounds["box_sx"]) + (2.0 * pad),
        "sy": float(bounds["box_sy"]) + (2.0 * pad),
        "sz": float(bounds["box_sz"]) + (2.0 * pad),
    }
