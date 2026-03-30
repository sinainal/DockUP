from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.Draw import rdMolDraw2D


KIND_PRIORITY = {
    "salt_bridges": 0,
    "pi_cation_interactions": 1,
    "hydrogen_bonds": 2,
    "halogen_bonds": 3,
    "pi_stacks": 4,
    "metal_complexes": 5,
    "water_bridges": 6,
    "hydrophobic_interactions": 7,
    "contact": 8,
}

KIND_STYLE = {
    "hydrophobic_interactions": {"fill": "#aef6a6", "stroke": "#6fd16c", "edge": "#c9b0ff", "dash": (14, 10)},
    "hydrogen_bonds": {"fill": "#f4b8f7", "stroke": "#d885d9", "edge": "#f2a3f7", "dash": (10, 8)},
    "salt_bridges": {"fill": "#d68cff", "stroke": "#a84fdf", "edge": "#a84fdf", "dash": (8, 6)},
    "pi_stacks": {"fill": "#f3c1db", "stroke": "#d990b8", "edge": "#f0a6cf", "dash": (10, 8)},
    "pi_cation_interactions": {"fill": "#e8c1ff", "stroke": "#ba7de0", "edge": "#bf83ff", "dash": (8, 6)},
    "halogen_bonds": {"fill": "#b8f7f7", "stroke": "#63cfd0", "edge": "#6ecdd1", "dash": (10, 6)},
    "metal_complexes": {"fill": "#ffd7ae", "stroke": "#e49a44", "edge": "#e49a44", "dash": (6, 5)},
    "water_bridges": {"fill": "#b8d3ff", "stroke": "#6b91d8", "edge": "#7ba3e8", "dash": (10, 8)},
    "contact": {"fill": "#dfe8cf", "stroke": "#9caf7a", "edge": "#bfc9aa", "dash": (10, 8)},
}

DIST_TAGS = ("dist", "dist_h-a", "dist_d-a")
NODE_RADIUS = 46.0
ATOM_OVERLAP_WEIGHT = 14.0
LINE_CROSSING_WEIGHT = 2600.0
NODE_COLLISION_WEIGHT = 3200.0
LINE_BIAS_WEIGHT = 0.9
LIGAND_PIXEL_WEIGHT = 42.0
NODE_PIXEL_WEIGHT = 280.0
CANVAS_WIDTH = 1600
CANVAS_HEIGHT = 950
CONTENT_PADDING = 84
EDGE_ALPHA = 194
NODE_FILL_ALPHA = 202
NODE_STROKE_ALPHA = 242
LABEL_FONT_SIZE = 22
CARBON_GRAY = (0.28, 0.28, 0.28)


@dataclass
class Interaction:
    residue_key: tuple[str, str, str]
    kind: str
    distance: float | None
    ligand_refs: list[int] = field(default_factory=list)
    atom_indices: list[int] = field(default_factory=list)


@dataclass
class ResidueNode:
    key: tuple[str, str, str]
    kind: str
    contact_count: int
    min_distance: float | None
    interactions: list[Interaction] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    anchor_x: float = 0.0
    anchor_y: float = 0.0


@dataclass(frozen=True)
class Placement:
    x: float
    y: float
    anchor_x: float
    anchor_y: float
    bias: float


def _hex_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[idx:idx + 2], 16) for idx in (0, 2, 4)) + (alpha,)


def _ints_from_text(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(value) for value in re.findall(r"-?\d+", text)]


def _idx_values(parent: ET.Element, tag: str) -> list[int]:
    node = parent.find(tag)
    if node is None:
        return []
    values: list[int] = []
    if list(node):
        for child in list(node):
            values.extend(_ints_from_text(child.text))
    else:
        values.extend(_ints_from_text(node.text))
    return values


def _unique_ints(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def _parse_smiles_to_pdb_mapping(path: Path) -> dict[int, int]:
    root = ET.parse(path).getroot()
    raw = (root.findtext(".//bindingsite/mappings/smiles_to_pdb") or "").strip()
    mapping: dict[int, int] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        smiles_idx_raw, pdb_ref_raw = pair.split(":", 1)
        try:
            mapping[int(pdb_ref_raw.strip())] = int(smiles_idx_raw.strip())
        except ValueError:
            continue
    return mapping


def _extract_ligand_refs(item: ET.Element, kind: str) -> list[int]:
    protisdon = (item.findtext("protisdon") or "").strip().lower() == "true"
    refs: list[int] = []
    if kind == "hydrophobic_interactions":
        refs.extend(_idx_values(item, "ligcarbonidx"))
    elif kind in {"hydrogen_bonds", "water_bridges"}:
        refs.extend(_idx_values(item, "acceptoridx" if protisdon else "donoridx"))
    elif kind in {"salt_bridges", "pi_stacks", "pi_cation_interactions"}:
        refs.extend(_idx_values(item, "lig_idx_list"))
    if refs:
        return _unique_ints(refs)
    for child in list(item):
        tag = child.tag.lower()
        if "idx" not in tag or tag.startswith("prot"):
            continue
        if kind in {"hydrogen_bonds", "water_bridges"} and tag in {"acceptoridx", "donoridx"}:
            continue
        refs.extend(_idx_values(item, child.tag))
    return _unique_ints(refs)


def _parse_plip_report(path: Path) -> list[Interaction]:
    root = ET.parse(path).getroot()
    interactions: list[Interaction] = []
    for container in root.findall(".//bindingsite/interactions/*"):
        kind = container.tag
        for item in list(container):
            resnr = (item.findtext("resnr") or "").strip()
            restype = (item.findtext("restype") or "").strip()
            reschain = (item.findtext("reschain") or "").strip()
            if not (resnr and restype and reschain):
                continue
            dist_val = None
            for tag in DIST_TAGS:
                raw = (item.findtext(tag) or "").strip()
                if raw:
                    try:
                        dist_val = float(raw)
                        break
                    except ValueError:
                        pass
            interactions.append(
                Interaction(
                    residue_key=(reschain, restype, resnr),
                    kind=kind,
                    distance=dist_val,
                    ligand_refs=_extract_ligand_refs(item, kind),
                )
            )
    return interactions


def _load_molecules(run_dir: Path) -> tuple[Chem.Mol, Chem.Mol]:
    for candidate in sorted(run_dir.glob("*_ligand_fixed.sdf")) + sorted(run_dir.glob("*_ligand.sdf")):
        supplier = Chem.SDMolSupplier(str(candidate), removeHs=False)
        mol3d = next((mol for mol in supplier if mol is not None), None)
        if mol3d is None:
            continue
        return mol3d, _prepare_draw_mol(mol3d)
    raise FileNotFoundError(f"Missing readable ligand sdf in {run_dir}")


def _prepare_draw_mol(mol: Chem.Mol) -> Chem.Mol:
    draw_mol = Chem.RemoveHs(Chem.Mol(mol))
    # RDKit renders DOUBLE+STEREOANY as crossed bonds. For interaction maps we want
    # the same RDKit depiction except a plain parallel double-bond glyph.
    for bond in draw_mol.GetBonds():
        if bond.GetBondTypeAsDouble() == 2 and str(bond.GetStereo()) == "STEREOANY":
            bond.SetStereo(Chem.rdchem.BondStereo.STEREONONE)
    AllChem.Compute2DCoords(draw_mol)
    return draw_mol


def _assign_interaction_atoms(interactions: list[Interaction], mol2d: Chem.Mol, report_path: Path) -> None:
    mapping = _parse_smiles_to_pdb_mapping(report_path)
    resolved = {
        pdb_ref: smiles_idx - 1
        for pdb_ref, smiles_idx in mapping.items()
        if 0 < smiles_idx <= mol2d.GetNumAtoms()
    }
    for interaction in interactions:
        interaction.atom_indices = _unique_ints([resolved[ref] for ref in interaction.ligand_refs if ref in resolved])


def _load_interaction_summary(run_dir: Path, interactions: list[Interaction]) -> list[ResidueNode]:
    interaction_map = json.loads((run_dir / "interaction_map.json").read_text())
    summary_lookup = {
        (
            str(entry["receptor_chain"]),
            str(entry["receptor_resname"]),
            str(entry["receptor_resid"]),
        ): entry
        for entry in interaction_map.get("residue_summary", [])
    }
    grouped: dict[tuple[str, str, str], list[Interaction]] = {}
    for interaction in interactions:
        grouped.setdefault(interaction.residue_key, []).append(interaction)
    nodes: list[ResidueNode] = []
    for key, items in sorted(grouped.items(), key=lambda item: _residue_sort_key(item[0][2])):
        entry = summary_lookup.get(key, {})
        primary = min(items, key=lambda item: KIND_PRIORITY.get(item.kind, 999)).kind
        min_distance = min((item.distance for item in items if item.distance is not None), default=entry.get("min_distance"))
        nodes.append(
            ResidueNode(
                key=key,
                kind=primary,
                contact_count=int(entry.get("contact_count", len(items))),
                min_distance=min_distance,
                interactions=items,
            )
        )
    return nodes


def _residue_sort_key(resid: str) -> tuple[int, str]:
    match = re.search(r"-?\d+", resid)
    if match:
        return (int(match.group(0)), resid)
    return (10**9, resid)


def _normalize(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _configure_draw_options(drawer) -> None:
    options = drawer.drawOptions()
    options.clearBackground = False
    options.padding = 0.06
    options.addAtomIndices = False
    options.legendFontSize = 18
    options.bondLineWidth = 4
    options.minFontSize = 18
    options.maxFontSize = 28
    if hasattr(options, "fixedBondLength"):
        options.fixedBondLength = 44.0
    if hasattr(options, "multipleBondOffset"):
        options.multipleBondOffset = 0.15
    if hasattr(options, "noAtomLabels"):
        options.noAtomLabels = False
    if hasattr(options, "useDefaultAtomPalette"):
        options.useDefaultAtomPalette()
    if hasattr(options, "updateAtomPalette"):
        options.updateAtomPalette({6: CARBON_GRAY})


def _atom_points(mol2d: Chem.Mol, width: int, height: int) -> dict[int, tuple[float, float]]:
    prepared = rdMolDraw2D.PrepareMolForDrawing(mol2d)
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    _configure_draw_options(drawer)
    drawer.DrawMolecule(prepared)
    drawer.FinishDrawing()
    points: dict[int, tuple[float, float]] = {}
    for atom in mol2d.GetAtoms():
        if atom.GetAtomicNum() == 1:
            continue
        pt = drawer.GetDrawCoords(atom.GetIdx())
        points[atom.GetIdx()] = (float(pt.x), float(pt.y))
    return points


def _render_molecule_image(mol2d: Chem.Mol, width: int, height: int) -> Image.Image:
    drawer = rdMolDraw2D.MolDraw2DCairo(width, height)
    _configure_draw_options(drawer)
    drawer.DrawMolecule(rdMolDraw2D.PrepareMolForDrawing(mol2d))
    drawer.FinishDrawing()
    image = Image.open(BytesIO(drawer.GetDrawingText())).convert("RGBA")
    cleaned: list[tuple[int, int, int, int]] = []
    for r, g, b, a in image.getdata():
        if r >= 248 and g >= 248 and b >= 248:
            cleaned.append((255, 255, 255, 0))
        else:
            cleaned.append((r, g, b, a))
    image.putdata(cleaned)
    return image


def _line_end(x: float, y: float, anchor_x: float, anchor_y: float) -> tuple[float, float]:
    ux, uy = _normalize(anchor_x - x, anchor_y - y)
    return (x + ux * NODE_RADIUS * 0.92, y + uy * NODE_RADIUS * 0.92)


def _trim_to_content(image: Image.Image, padding: int = CONTENT_PADDING) -> Image.Image:
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return image
    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    return image.crop((left, top, right, bottom))


def _anchor_candidates(node: ResidueNode, atom_points: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    interactions = [inter for inter in node.interactions if inter.kind == node.kind] or node.interactions
    candidates: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for interaction in interactions:
        for atom_index in interaction.atom_indices:
            if atom_index not in atom_points:
                continue
            point = atom_points[atom_index]
            key = (round(point[0], 3), round(point[1], 3))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(point)
    return candidates


def _bounds(atom_points: dict[int, tuple[float, float]]) -> tuple[float, float, float, float, float, float]:
    xs = [point[0] for point in atom_points.values()]
    ys = [point[1] for point in atom_points.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return min_x, max_x, min_y, max_y, (min_x + max_x) / 2.0, (min_y + max_y) / 2.0


def _generate_v6_candidates(
    anchor_x: float,
    anchor_y: float,
    bounds: tuple[float, float, float, float, float, float],
    width: int,
    height: int,
) -> list[Placement]:
    _, _, _, _, center_x, center_y = bounds
    margin = NODE_RADIUS + 96.0
    ux, uy = _normalize(anchor_x - center_x, anchor_y - center_y)
    tx, ty = -uy, ux
    radii = [138.0, 178.0, 222.0, 266.0]
    tangential = [0.0, -58.0, 58.0, -112.0, 112.0]
    placements: list[Placement] = []
    for radius in radii:
        for tangent in tangential:
            x = anchor_x + ux * radius + tx * tangent
            y = anchor_y + uy * radius + ty * tangent
            if margin <= x <= width - margin and margin <= y <= height - margin:
                placements.append(Placement(x, y, anchor_x, anchor_y, radius * 0.34 + abs(tangent) * 0.24))
    return placements


def _generate_candidates(node: ResidueNode, atom_points: dict[int, tuple[float, float]], bounds, width: int, height: int) -> list[Placement]:
    seen: set[tuple[float, float, float, float]] = set()
    placements: list[Placement] = []
    for anchor_x, anchor_y in _anchor_candidates(node, atom_points):
        for placement in _generate_v6_candidates(anchor_x, anchor_y, bounds, width, height):
            key = (
                round(placement.x, 2),
                round(placement.y, 2),
                round(placement.anchor_x, 2),
                round(placement.anchor_y, 2),
            )
            if key in seen:
                continue
            seen.add(key)
            placements.append(placement)
    return placements


def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / max(dx * dx + dy * dy, 1e-6)
    t = min(max(t, 0.0), 1.0)
    qx = ax + t * dx
    qy = ay + t * dy
    return math.hypot(px - qx, py - qy)


def _opaque_alpha(alpha_mask: Image.Image, x: float, y: float) -> int:
    ix = int(round(x))
    iy = int(round(y))
    if ix < 0 or iy < 0 or ix >= alpha_mask.width or iy >= alpha_mask.height:
        return 0
    return int(alpha_mask.getpixel((ix, iy)))


def _segment_mask_penalty(alpha_mask: Image.Image, start: tuple[float, float], end: tuple[float, float]) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    total = math.hypot(dx, dy)
    if total < 1e-6:
        return 0.0
    ux = dx / total
    uy = dy / total
    px = -uy
    py = ux
    cursor = min(18.0, total)
    limit = max(cursor, total - 12.0)
    penalty = 0.0
    while cursor < limit:
        sx = start[0] + ux * cursor
        sy = start[1] + uy * cursor
        sample = 0.0
        for offset in (-6.0, 0.0, 6.0):
            alpha = _opaque_alpha(alpha_mask, sx + px * offset, sy + py * offset)
            if alpha > 12:
                sample += alpha / 255.0
        penalty += sample
        cursor += 4.0
    return penalty


def _node_mask_penalty(alpha_mask: Image.Image, x: float, y: float) -> float:
    offsets = (
        (0.0, 0.0),
        (-NODE_RADIUS * 0.55, 0.0),
        (NODE_RADIUS * 0.55, 0.0),
        (0.0, -NODE_RADIUS * 0.55),
        (0.0, NODE_RADIUS * 0.55),
        (-NODE_RADIUS * 0.4, -NODE_RADIUS * 0.4),
        (NODE_RADIUS * 0.4, -NODE_RADIUS * 0.4),
        (-NODE_RADIUS * 0.4, NODE_RADIUS * 0.4),
        (NODE_RADIUS * 0.4, NODE_RADIUS * 0.4),
    )
    penalty = 0.0
    for ox, oy in offsets:
        alpha = _opaque_alpha(alpha_mask, x + ox, y + oy)
        if alpha > 12:
            penalty += alpha / 255.0
    return penalty


def _segment_intersection(a1, a2, b1, b2) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if (round(a1[0], 3), round(a1[1], 3)) == (round(b1[0], 3), round(b1[1], 3)):
        return False
    o1 = orient(a1, a2, b1)
    o2 = orient(a1, a2, b2)
    o3 = orient(b1, b2, a1)
    o4 = orient(b1, b2, a2)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def _score_candidate(
    node: ResidueNode,
    placement: Placement,
    placed: dict[str, Placement],
    nodes_by_id: dict[str, ResidueNode],
    atom_points,
    alpha_mask: Image.Image,
) -> float:
    end_x, end_y = _line_end(placement.x, placement.y, placement.anchor_x, placement.anchor_y)
    line_length = math.hypot(end_x - placement.anchor_x, end_y - placement.anchor_y)
    score = line_length + placement.bias * LINE_BIAS_WEIGHT
    clearance = NODE_RADIUS * 0.98
    for point in atom_points.values():
        if math.hypot(point[0] - placement.anchor_x, point[1] - placement.anchor_y) < 3.0:
            continue
        dist = _point_to_segment_distance(point[0], point[1], placement.anchor_x, placement.anchor_y, end_x, end_y)
        if dist < clearance:
            score += (clearance - dist) ** 2 * ATOM_OVERLAP_WEIGHT
    score += _segment_mask_penalty(alpha_mask, (placement.anchor_x, placement.anchor_y), (end_x, end_y)) * LIGAND_PIXEL_WEIGHT
    score += _node_mask_penalty(alpha_mask, placement.x, placement.y) * NODE_PIXEL_WEIGHT
    for other_id, other in placed.items():
        other_node = nodes_by_id[other_id]
        dist = math.hypot(placement.x - other.x, placement.y - other.y)
        min_sep = NODE_RADIUS * 2.12
        if dist < min_sep:
            score += (min_sep - dist + 1.0) * NODE_COLLISION_WEIGHT
        other_end_x, other_end_y = _line_end(other.x, other.y, other.anchor_x, other.anchor_y)
        if _segment_intersection(
            (placement.anchor_x, placement.anchor_y),
            (end_x, end_y),
            (other.anchor_x, other.anchor_y),
            (other_end_x, other_end_y),
        ):
            score += LINE_CROSSING_WEIGHT
        if other_node.kind == node.kind and abs(placement.y - other.y) < 16 and abs(placement.x - other.x) < 16:
            score += 600.0
    return score


def _node_id(node: ResidueNode) -> str:
    return f"{node.key[0]}:{node.key[1]}:{node.key[2]}"


def _layout_nodes(nodes: list[ResidueNode], atom_points, alpha_mask: Image.Image, bounds, width: int, height: int) -> None:
    nodes_by_id = {_node_id(node): node for node in nodes}
    candidate_map = {_node_id(node): _generate_candidates(node, atom_points, bounds, width, height) for node in nodes}
    order = sorted(
        nodes,
        key=lambda node: (
            len(candidate_map[_node_id(node)]),
            KIND_PRIORITY.get(node.kind, 999),
            -node.contact_count,
            _residue_sort_key(node.key[2]),
        ),
    )

    placed: dict[str, Placement] = {}
    for node in order:
        nid = _node_id(node)
        placements = candidate_map[nid]
        if not placements:
            continue
        placed[nid] = min(
            placements,
            key=lambda placement: _score_candidate(node, placement, placed, nodes_by_id, atom_points, alpha_mask),
        )

    for _ in range(2):
        for node in order:
            nid = _node_id(node)
            placements = candidate_map[nid]
            if not placements:
                continue
            snapshot = dict(placed)
            snapshot.pop(nid, None)
            placed[nid] = min(
                placements,
                key=lambda placement: _score_candidate(node, placement, snapshot, nodes_by_id, atom_points, alpha_mask),
            )

    for node in nodes:
        placement = placed.get(_node_id(node))
        if placement is None:
            continue
        node.x = placement.x
        node.y = placement.y
        node.anchor_x = placement.anchor_x
        node.anchor_y = placement.anchor_y


def _draw_dashed_line(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], dash: tuple[int, int], fill, width: int) -> None:
    total = math.hypot(end[0] - start[0], end[1] - start[1])
    if total < 1e-6:
        return
    ux = (end[0] - start[0]) / total
    uy = (end[1] - start[1]) / total
    dash_len, gap_len = dash
    cursor = 0.0
    while cursor < total:
        seg_end = min(cursor + dash_len, total)
        p1 = (start[0] + ux * cursor, start[1] + uy * cursor)
        p2 = (start[0] + ux * seg_end, start[1] + uy * seg_end)
        draw.line([p1, p2], fill=fill, width=width)
        cursor += dash_len + gap_len


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_interaction_map(mol2d: Chem.Mol, nodes: list[ResidueNode], width: int, height: int, out_path: Path) -> None:
    molecule_image = _render_molecule_image(mol2d, width, height)
    drawable_nodes = [
        node
        for node in nodes
        if any(abs(value) > 1e-3 for value in (node.x, node.y, node.anchor_x, node.anchor_y))
    ]
    image = Image.new("RGBA", molecule_image.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    font = _load_font(LABEL_FONT_SIZE)
    for node in drawable_nodes:
        style = KIND_STYLE.get(node.kind, KIND_STYLE["contact"])
        edge_fill = _hex_rgba(style["edge"], EDGE_ALPHA)
        px, py = _line_end(node.x, node.y, node.anchor_x, node.anchor_y)
        _draw_dashed_line(draw, (node.anchor_x, node.anchor_y), (px, py), style["dash"], edge_fill, width=4)
    image.alpha_composite(molecule_image)
    draw = ImageDraw.Draw(image, "RGBA")
    for node in drawable_nodes:
        style = KIND_STYLE.get(node.kind, KIND_STYLE["contact"])
        fill = _hex_rgba(style["fill"], NODE_FILL_ALPHA)
        stroke = _hex_rgba(style["stroke"], NODE_STROKE_ALPHA)
        draw.ellipse(
            (node.x - NODE_RADIUS, node.y - NODE_RADIUS, node.x + NODE_RADIUS, node.y + NODE_RADIUS),
            fill=fill,
            outline=stroke,
            width=2,
        )
        label1 = node.key[1]
        label2 = f"{node.key[0]}:{node.key[2]}"
        bbox1 = draw.textbbox((0, 0), label1, font=font)
        bbox2 = draw.textbbox((0, 0), label2, font=font)
        text_w = max(bbox1[2] - bbox1[0], bbox2[2] - bbox2[0])
        total_h = (bbox1[3] - bbox1[1]) + (bbox2[3] - bbox2[1]) + 4
        x = node.x - text_w / 2.0
        y = node.y - total_h / 2.0
        draw.text((x, y), label1, font=font, fill=(36, 49, 29, 255))
        draw.text((node.x - (bbox2[2] - bbox2[0]) / 2.0, y + (bbox1[3] - bbox1[1]) + 4), label2, font=font, fill=(36, 49, 29, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image = _trim_to_content(image)
    image.save(out_path, dpi=(120, 120))


def render_run(run_dir: Path, receptor_id: str, run_name: str, out_path: Path, *, width: int, height: int) -> None:
    report_path = run_dir / "plip" / "report.xml"
    interactions = _parse_plip_report(report_path)
    _, mol2d = _load_molecules(run_dir)
    _assign_interaction_atoms(interactions, mol2d, report_path)
    nodes = _load_interaction_summary(run_dir, interactions)
    atom_points = _atom_points(mol2d, width, height)
    molecule_image = _render_molecule_image(mol2d, width, height)
    alpha_mask = molecule_image.getchannel("A")
    bounds = _bounds(atom_points)
    _layout_nodes(nodes, atom_points, alpha_mask, bounds, width, height)
    _draw_interaction_map(mol2d, nodes, width, height, out_path)
    print(f"Wrote {out_path} for {receptor_id}/{run_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render straight-line OtoFigure interaction maps")
    parser.add_argument("--manifest", required=True, help="JSON manifest with receptor_id, run_name, run_dir entries")
    parser.add_argument("--output_dir", required=True, help="Directory where *_interaction.png files will be written")
    parser.add_argument("--width", type=int, default=CANVAS_WIDTH)
    parser.add_argument("--height", type=int, default=CANVAS_HEIGHT)
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for entry in manifest:
        receptor_id = str(entry["receptor_id"]).lower()
        run_name = str(entry["run_name"])
        run_dir = Path(entry["run_dir"]).resolve()
        out_path = output_dir / f"{receptor_id}_{run_name}_interaction.png"
        render_run(run_dir, receptor_id, run_name, out_path, width=int(args.width), height=int(args.height))


if __name__ == "__main__":
    main()
