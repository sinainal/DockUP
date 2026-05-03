from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from docking_app.agent import autonomous_docking
from docking_app.config import DOCK_DIR, LIGAND_DIR, WORKSPACE_DIR
from docking_app.pocket_finder import clear_cached_results
from docking_app.state import DOCKING_CONFIG_DEFAULTS, RUN_STATE, STATE, save_state_cache


@dataclass
class SeedBundle:
    root_dir: Path
    inputs_dir: Path
    receptor_files: dict[str, Path]
    ligand_name: str
    ligand_path: Path
    ligand_names: list[str]
    ligand_paths: dict[str, Path]
    cleanup_paths: list[Path]


P2RANK_CLEAN_FIXTURE = WORKSPACE_DIR / "tools" / "p2rank" / "test_data" / "clean" / "1a82a.pdb"


@dataclass
class CaseSpec:
    case_id: str
    prompt: str
    think_mode: str = "auto"
    max_steps: int = 8
    prepare: Callable[[SeedBundle], None] | None = None
    check: Callable[[dict[str, Any], list[dict[str, Any]], SeedBundle], tuple[bool, str]] | None = None
    notes: str = ""


def _record_atom_line(
    record: str,
    serial: int,
    atom: str,
    resname: str,
    chain: str,
    resid: int,
    x: float,
    y: float,
    z: float,
    element: str,
) -> str:
    line = [" "] * 80
    line[0:6] = list(f"{record:<6}")
    line[6:11] = list(f"{serial:5d}")
    line[12:16] = list(f"{atom:>4s}")
    line[17:20] = list(f"{resname:>3s}")
    line[21:22] = list((chain or " ")[:1])
    line[22:26] = list(f"{resid:4d}")
    line[30:38] = list(f"{x:8.3f}")
    line[38:46] = list(f"{y:8.3f}")
    line[46:54] = list(f"{z:8.3f}")
    line[76:78] = list(f"{element:>2s}")
    return "".join(line).rstrip()


def _make_receptor_text(*, native: bool) -> str:
    rows = [
        _record_atom_line("ATOM", 1, "N", "GLY", "B", 1, 10.000, 10.000, 10.000, "N"),
        _record_atom_line("ATOM", 2, "CA", "GLY", "B", 1, 11.000, 10.000, 10.500, "C"),
        _record_atom_line("ATOM", 3, "C", "GLY", "B", 1, 11.500, 11.000, 11.000, "C"),
        _record_atom_line("ATOM", 4, "O", "GLY", "B", 1, 12.000, 11.500, 11.500, "O"),
    ]
    if native:
        rows.extend(
            [
                _record_atom_line("HETATM", 5, "C1", "NXL", "B", 308, 20.000, 20.000, 20.000, "C"),
                _record_atom_line("HETATM", 6, "C2", "NXL", "B", 308, 21.000, 20.500, 20.000, "C"),
                _record_atom_line("HETATM", 7, "C3", "NXL", "B", 308, 21.500, 21.000, 20.500, "C"),
                _record_atom_line("HETATM", 8, "C4", "NXL", "B", 308, 22.000, 21.500, 21.000, "C"),
                _record_atom_line("HETATM", 9, "C5", "NXL", "B", 308, 22.500, 22.000, 21.500, "C"),
                _record_atom_line("HETATM", 10, "C6", "NXL", "B", 308, 23.000, 22.500, 22.000, "C"),
            ]
        )
    rows.extend(
        [
            _record_atom_line("HETATM", 11, "CL", "CL", "B", 900, 14.000, 14.000, 14.000, "CL"),
            _record_atom_line("HETATM", 12, "CL2", "CL", "B", 900, 14.500, 14.500, 14.500, "CL"),
            _record_atom_line("HETATM", 13, "S", "SO4", "B", 901, 15.000, 15.000, 15.000, "S"),
            "END",
        ]
    )
    return "\n".join(rows) + "\n"


def _make_receptor_text_no_ligand() -> str:
    rows = [
        _record_atom_line("ATOM", 1, "N", "GLY", "A", 1, 5.000, 5.000, 5.000, "N"),
        _record_atom_line("ATOM", 2, "CA", "GLY", "A", 1, 6.000, 5.500, 5.250, "C"),
        _record_atom_line("ATOM", 3, "C", "GLY", "A", 1, 6.500, 6.000, 5.750, "C"),
        _record_atom_line("ATOM", 4, "O", "GLY", "A", 1, 7.000, 6.500, 6.250, "O"),
        "END",
    ]
    return "\n".join(rows) + "\n"


def _write_seed_ligand(path: Path, title: str = "DockUP hard10 ligand") -> str:
    sdf = f"""{title}
  DockUP

  0  0  0     0  0            999 V2000
M  END
$$$$
"""
    path.write_text(sdf, encoding="utf-8")
    return path.name


def prepare_seed_bundle(root_dir: Path) -> SeedBundle:
    inputs_dir = root_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    receptor_files = {
        "MNA1": inputs_dir / "MNA1.pdb",
        "P2R1": inputs_dir / "P2R1.pdb",
        "RUN1": inputs_dir / "RUN1.pdb",
        "DEL1": inputs_dir / "DEL1.pdb",
    }
    receptor_files["MNA1"].write_text(_make_receptor_text(native=True), encoding="utf-8")
    if P2RANK_CLEAN_FIXTURE.exists():
        receptor_files["P2R1"].write_text(P2RANK_CLEAN_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        receptor_files["P2R1"].write_text(_make_receptor_text_no_ligand(), encoding="utf-8")
    receptor_files["RUN1"].write_text(_make_receptor_text(native=True), encoding="utf-8")
    receptor_files["DEL1"].write_text(_make_receptor_text_no_ligand(), encoding="utf-8")

    stamp = int(root_dir.stat().st_mtime_ns % 10_000_000)
    ligand_specs = [
        ("primary", f"agent_hard10_{stamp}_primary.sdf", "DockUP hard10 primary ligand"),
        ("secondary", f"agent_hard10_{stamp}_secondary.sdf", "DockUP hard10 secondary ligand"),
        ("tertiary", f"agent_hard10_{stamp}_tertiary.sdf", "DockUP hard10 tertiary ligand"),
    ]
    ligand_paths = {label: LIGAND_DIR / filename for label, filename, _title in ligand_specs}
    ligand_names = []
    cleanup_paths: list[Path] = []
    for label, filename, title in ligand_specs:
        path = ligand_paths[label]
        ligand_names.append(_write_seed_ligand(path, title=title))
        cleanup_paths.append(path)
    ligand_name = ligand_names[0]
    ligand_path = ligand_paths["primary"]

    return SeedBundle(
        root_dir=root_dir,
        inputs_dir=inputs_dir,
        receptor_files=receptor_files,
        ligand_name=ligand_name,
        ligand_path=ligand_path,
        ligand_names=ligand_names,
        ligand_paths=ligand_paths,
        cleanup_paths=cleanup_paths,
    )


def reset_state(bundle: SeedBundle) -> None:
    bundle.ligand_path.parent.mkdir(parents=True, exist_ok=True)
    for label, path in bundle.ligand_paths.items():
        title = f"DockUP hard10 {label} ligand"
        _write_seed_ligand(path, title=title)
    for receptor_id in bundle.receptor_files:
        try:
            clear_cached_results(receptor_id)
        except Exception:
            pass
    STATE.clear()
    STATE.update(
        {
            "mode": "Docking",
            "receptor_meta": [
                {
                    "pdb_id": "MNA1",
                    "pdb_file": str(bundle.receptor_files["MNA1"]),
                    "pdb_text": bundle.receptor_files["MNA1"].read_text(encoding="utf-8"),
                    "chains": ["all", "B"],
                    "ligands_by_chain": {"B": ["CL 900", "NXL 308", "SO4 901"], "all": ["CL 900", "NXL 308", "SO4 901"]},
                    "error": "",
                },
                {
                    "pdb_id": "P2R1",
                    "pdb_file": str(bundle.receptor_files["P2R1"]),
                    "pdb_text": bundle.receptor_files["P2R1"].read_text(encoding="utf-8"),
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"A": [], "all": []},
                    "error": "",
                },
                {
                    "pdb_id": "RUN1",
                    "pdb_file": str(bundle.receptor_files["RUN1"]),
                    "pdb_text": bundle.receptor_files["RUN1"].read_text(encoding="utf-8"),
                    "chains": ["all", "B"],
                    "ligands_by_chain": {"B": ["CL 900", "NXL 308", "SO4 901"], "all": ["CL 900", "NXL 308", "SO4 901"]},
                    "error": "",
                },
                {
                    "pdb_id": "DEL1",
                    "pdb_file": str(bundle.receptor_files["DEL1"]),
                    "pdb_text": bundle.receptor_files["DEL1"].read_text(encoding="utf-8"),
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"A": [], "all": []},
                    "error": "",
                },
            ],
            "selection_map": {
                "MNA1": {"chain": "B", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
                "P2R1": {"chain": "A", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
                "RUN1": {"chain": "B", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
                "DEL1": {"chain": "A", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
            },
            "selected_ids": [],
            "selected_receptor": "",
            "selected_ligand": "",
            "selected_chain": "all",
            "active_ligands": [bundle.ligand_name],
            "grid_file_path": "",
            "agent_grid_data": {},
            "queue": [],
            "runs": 1,
            "grid_pad": 0.0,
            "docking_config": copy.deepcopy(DOCKING_CONFIG_DEFAULTS),
            "out_root": str(DOCK_DIR),
            "out_root_path": str(DOCK_DIR),
            "out_root_name": "",
            "results_root_path": str(DOCK_DIR),
        }
    )
    RUN_STATE.update(
        {
            "status": "idle",
            "log_lines": [],
            "returncode": None,
            "command": "",
            "out_root": "",
            "start_time": None,
            "total_runs": 0,
            "completed_runs": 0,
        }
    )
    autonomous_docking.AGENT_STATE.update(
        {
            "inventory": {},
            "setup_rows": [],
            "grid_data": {},
            "batch_config": {},
            "batch_id": "",
            "recent_actions": [],
            "memory_summary": "",
            "last_tool": "",
            "last_tool_summary": "",
            "last_answer": "",
            "last_error": "",
            "workflow_stage": "idle",
        }
    )
    save_state_cache()


def _case_ok_default(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    if not result.get("ok", False):
        return False, str(result.get("error") or "tool loop failed")
    return True, ""


def _tools_used(result: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    for row in result.get("trace") or []:
        if not isinstance(row, dict) or not row.get("tool"):
            continue
        tool = str(row.get("tool") or "").strip()
        if tool and tool not in tools:
            tools.append(tool)
    return tools


def _last_trace_tool_result(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    trace = result.get("trace") or []
    for row in reversed(trace):
        if not isinstance(row, dict):
            continue
        if str(row.get("tool") or "").strip() != tool_name:
            continue
        tool_result = row.get("result")
        if isinstance(tool_result, dict):
            return tool_result
    return {}


def _case_no_tool_short_answer(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    if not result.get("ok", False):
        return False, str(result.get("error") or "tool loop failed")
    if _tools_used(result):
        return False, f"unexpected tools: {_tools_used(result)}"
    answer = str(result.get("answer") or "").strip()
    if not answer or len(answer) > 240:
        return False, f"answer too long or empty ({len(answer)} chars)"
    return True, ""


def _case_state_summary(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "get_dockup_state" not in tools:
        return False, f"missing get_dockup_state tool call: {tools}"
    return _case_ok_default(result, _events, _bundle)


def _case_main_native(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "select_workspace" not in tools or "set_gridbox" not in tools:
        return False, f"expected select_workspace + set_gridbox, saw {tools}"
    for row in result.get("trace") or []:
        if isinstance(row, dict) and row.get("tool") == "select_workspace":
            selected = row.get("result", {}).get("selected") if isinstance(row.get("result"), dict) else None
            if isinstance(selected, list) and selected:
                native = str(selected[0].get("native_ligand") or "")
                if native.startswith("CL"):
                    return False, f"selected helper ligand instead of main ligand: {native}"
    return _case_ok_default(result, _events, _bundle)


def _case_p2rank(result: dict[str, Any], events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "set_gridbox" not in tools:
        return False, f"set_gridbox not used: {tools}"
    grid_result = _last_trace_tool_result(result, "set_gridbox")
    mode = str(grid_result.get("gridbox_mode") or grid_result.get("resolved_gridbox_mode") or "").lower()
    if "p2rank" not in mode and not any(str(event.get("stage")) == "p2rank" for event in events if isinstance(event, dict)):
        return False, "missing p2rank status events or p2rank gridbox result"
    return _case_ok_default(result, events, _bundle)


def _case_config(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "set_docking_config" not in tools:
        return False, f"missing set_docking_config tool call: {tools}"
    return _case_ok_default(result, _events, _bundle)


def _case_build_test(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "build_or_run_queue" not in tools:
        return False, f"missing build_or_run_queue tool call: {tools}"
    run = _last_trace_tool_result(result, "build_or_run_queue").get("run", {})
    if isinstance(run, dict) and run.get("test_mode") is False:
        return False, "expected test-mode queue run"
    return _case_ok_default(result, _events, _bundle)


def _case_full_run(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "build_or_run_queue" not in tools:
        return False, f"missing build_or_run_queue tool call: {tools}"
    run = _last_trace_tool_result(result, "build_or_run_queue").get("run", {})
    if not isinstance(run, dict) or not run.get("started"):
        return False, "full run was not started"
    return _case_ok_default(result, _events, _bundle)


def _case_delete_specific(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if not any(tool in {"delete_ligands", "delete_receptors", "delete_queue_batches"} for tool in tools):
        return False, f"missing delete tool call: {tools}"
    return _case_ok_default(result, _events, _bundle)


def _case_clarify(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    if not result.get("ok", False):
        return False, str(result.get("error") or "tool loop failed")
    answer = str(result.get("answer") or "").strip()
    if not answer or len(answer) > 160:
        return False, f"clarification should be short, got {len(answer)} chars"
    if "?" not in answer:
        return False, "clarification should ask a question"
    return True, ""


def _case_final_boss(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    required = {"select_workspace", "set_gridbox", "set_docking_config", "build_or_run_queue"}
    if not required.issubset(set(tools)):
        return False, f"missing required tools: {sorted(required - set(tools))}"
    if result.get("stopped_reason") == "repeated_text":
        return False, "stopped on repeated text"
    return _case_ok_default(result, _events, _bundle)


def _case_requires_tools(*required: str) -> Callable[[dict[str, Any], list[dict[str, Any]], SeedBundle], tuple[bool, str]]:
    required_set = set(required)

    def _check(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
        tools = _tools_used(result)
        missing = [tool for tool in required if tool not in tools]
        if missing:
            return False, f"missing required tools: {missing}"
        return _case_ok_default(result, _events, _bundle)

    return _check


def _case_requires_question(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
    if not result.get("ok", False):
        return False, str(result.get("error") or "tool loop failed")
    answer = str(result.get("answer") or "").strip()
    if not answer:
        return False, "answer is empty"
    if len(answer) > 180:
        return False, f"clarification should be short, got {len(answer)} chars"
    if "?" not in answer:
        return False, "clarification should ask a question"
    return True, ""


def _case_requires_answer_short(max_len: int = 220) -> Callable[[dict[str, Any], list[dict[str, Any]], SeedBundle], tuple[bool, str]]:
    def _check(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
        if not result.get("ok", False):
            return False, str(result.get("error") or "tool loop failed")
        answer = str(result.get("answer") or "").strip()
        if not answer or len(answer) > max_len:
            return False, f"answer too long or empty ({len(answer)} chars)"
        return True, ""

    return _check


def _case_requires_run_started(*, test_mode: bool | None = None) -> Callable[[dict[str, Any], list[dict[str, Any]], SeedBundle], tuple[bool, str]]:
    def _check(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
        tools = _tools_used(result)
        if "build_or_run_queue" not in tools:
            return False, f"missing build_or_run_queue tool call: {tools}"
        run = _last_trace_tool_result(result, "build_or_run_queue").get("run", {})
        if not isinstance(run, dict) or not run.get("started"):
            return False, "full run was not started"
        if test_mode is not None and bool(run.get("test_mode")) is not bool(test_mode):
            return False, f"expected test_mode={test_mode}, saw {run.get('test_mode')}"
        return _case_ok_default(result, _events, _bundle)

    return _check


def _case_requires_build_action(*, expected_test_mode: bool) -> Callable[[dict[str, Any], list[dict[str, Any]], SeedBundle], tuple[bool, str]]:
    def _check(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
        tools = _tools_used(result)
        if "build_or_run_queue" not in tools:
            return False, f"missing build_or_run_queue tool call: {tools}"
        run = _last_trace_tool_result(result, "build_or_run_queue").get("run", {})
        if not isinstance(run, dict):
            return False, "missing queue run payload"
        if bool(run.get("test_mode")) is not expected_test_mode:
            return False, f"expected test_mode={expected_test_mode}, saw {run.get('test_mode')}"
        return _case_ok_default(result, _events, _bundle)

    return _check


def _case_requires_build_only() -> Callable[[dict[str, Any], list[dict[str, Any]], SeedBundle], tuple[bool, str]]:
    def _check(result: dict[str, Any], _events: list[dict[str, Any]], _bundle: SeedBundle) -> tuple[bool, str]:
        tools = _tools_used(result)
        if "build_or_run_queue" not in tools:
            return False, f"missing build_or_run_queue tool call: {tools}"
        run = _last_trace_tool_result(result, "build_or_run_queue").get("run", {})
        if not isinstance(run, dict):
            return False, "missing queue run payload"
        if run.get("started"):
            return False, "build_only should not start a run"
        return _case_ok_default(result, _events, _bundle)

    return _check


def _tool_call_count(result: dict[str, Any], tool_name: str) -> int:
    return sum(1 for row in result.get("trace") or [] if isinstance(row, dict) and row.get("tool") == tool_name)


def _case_requires_repeated_tools(**minimums: int) -> Callable[[dict[str, Any], list[dict[str, Any]], SeedBundle], tuple[bool, str]]:
    def _check(result: dict[str, Any], events: list[dict[str, Any]], bundle: SeedBundle) -> tuple[bool, str]:
        for tool_name, minimum in minimums.items():
            count = _tool_call_count(result, tool_name)
            if count < minimum:
                return False, f"expected at least {minimum} {tool_name} call(s), saw {count}"
        return _case_ok_default(result, events, bundle)

    return _check


def _case_requires_append_queue(result: dict[str, Any], events: list[dict[str, Any]], bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "build_or_run_queue" not in tools:
        return False, f"missing build_or_run_queue tool call: {tools}"
    seen_append = False
    seen_non_real = False
    for row in result.get("trace") or []:
        if not isinstance(row, dict) or row.get("tool") != "build_or_run_queue":
            continue
        tool_result = row.get("result")
        if not isinstance(tool_result, dict):
            continue
        if tool_result.get("replace_queue") is False or (tool_result.get("queue") or {}).get("replace_queue") is False:
            seen_append = True
        run = tool_result.get("run")
        if not isinstance(run, dict) or run.get("test_mode") is not False:
            seen_non_real = True
    if not seen_append:
        return False, "expected at least one append queue build (replace_queue=false)"
    if not seen_non_real:
        return False, "expected build/test behavior without real docking"
    return _case_ok_default(result, events, bundle)


def _case_requires_no_real_run(result: dict[str, Any], events: list[dict[str, Any]], bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "build_or_run_queue" not in tools:
        return False, f"missing build_or_run_queue tool call: {tools}"
    for row in result.get("trace") or []:
        if not isinstance(row, dict) or row.get("tool") != "build_or_run_queue":
            continue
        tool_result = row.get("result")
        run = tool_result.get("run", {}) if isinstance(tool_result, dict) else {}
        if isinstance(run, dict) and run.get("test_mode") is False:
            return False, "started or requested a real docking run despite test-only instruction"
    return _case_ok_default(result, events, bundle)


def _case_state_read_only(result: dict[str, Any], events: list[dict[str, Any]], bundle: SeedBundle) -> tuple[bool, str]:
    tools = _tools_used(result)
    if "get_dockup_state" not in tools:
        return False, f"missing get_dockup_state tool call: {tools}"
    mutating_tools = {
        "fetch_assets",
        "select_workspace",
        "set_gridbox",
        "set_docking_config",
        "build_or_run_queue",
        "delete_ligands",
        "delete_receptors",
        "delete_queue_batches",
    }
    used_mutating = [tool for tool in tools if tool in mutating_tools]
    if used_mutating:
        return False, f"state question should not mutate app state; used {used_mutating}"
    return _case_ok_default(result, events, bundle)


def _prepare_active_ligands(*ligand_labels: str) -> Callable[[SeedBundle], None]:
    labels = [label for label in ligand_labels if label]

    def _prepare(bundle: SeedBundle) -> None:
        selected = [label for label in labels if label in bundle.ligand_names]
        if not selected:
            selected = [bundle.ligand_name]
        STATE["active_ligands"] = selected
        if isinstance(autonomous_docking.AGENT_STATE.get("inventory"), dict):
            autonomous_docking.AGENT_STATE["inventory"] = autonomous_docking._inventory_for(
                autonomous_docking._state_receptor_ids(),
                list(STATE.get("active_ligands") or []),
            )

    return _prepare


def _prepare_receptor_subset(*pdb_ids: str) -> Callable[[SeedBundle], None]:
    wanted = [autonomous_docking._normalize_receptor_id(pdb_id) for pdb_id in pdb_ids if autonomous_docking._normalize_receptor_id(pdb_id)]

    def _prepare(bundle: SeedBundle) -> None:
        wanted_set = set(wanted)
        STATE["receptor_meta"] = [
            row
            for row in STATE.get("receptor_meta", [])
            if isinstance(row, dict) and autonomous_docking._normalize_receptor_id(row.get("pdb_id")) in wanted_set
        ]
        STATE["selection_map"] = {
            pdb_id: {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}
            for pdb_id in wanted
        }
        STATE["selected_ids"] = []
        STATE["selected_receptor"] = ""
        STATE["selected_chain"] = "all"
        STATE["selected_ligand"] = ""
        if isinstance(autonomous_docking.AGENT_STATE.get("inventory"), dict):
            autonomous_docking.AGENT_STATE["inventory"] = autonomous_docking._inventory_for(
                autonomous_docking._state_receptor_ids(),
                list(STATE.get("active_ligands") or []),
            )

    return _prepare


def _prepare_selected_workspace(receptor: str, chain: str = "all", native_ligand: str = "", dock_ligands: str = "all") -> Callable[[SeedBundle], None]:
    def _prepare(bundle: SeedBundle) -> None:
        STATE["selected_receptor"] = autonomous_docking._normalize_receptor_id(receptor)
        STATE["selected_chain"] = str(chain or "all").strip() or "all"
        STATE["selected_ligand"] = str(native_ligand or "").strip()
        STATE["selected_ids"] = [STATE["selected_receptor"]] if STATE["selected_receptor"] else []
        selection_map = STATE.setdefault("selection_map", {})
        if STATE["selected_receptor"]:
            selection_map[STATE["selected_receptor"]] = {
                "chain": STATE["selected_chain"],
                "ligand_resname": STATE["selected_ligand"],
                "ligand_resnames": [STATE["selected_ligand"]] if STATE["selected_ligand"] else [],
                "flex_residues": [],
            }
        autonomous_docking.AGENT_STATE["setup_rows"] = []
        autonomous_docking.AGENT_STATE["grid_data"] = {}
        autonomous_docking.AGENT_STATE["batch_config"] = {}

    return _prepare


def _prepare_ready_build_test(
    receptor: str,
    chain: str = "all",
    native_ligand: str = "",
    dock_ligands: str = "all",
) -> Callable[[SeedBundle], None]:
    def _prepare(bundle: SeedBundle) -> None:
        _prepare_selected_workspace(receptor, chain=chain, native_ligand=native_ligand, dock_ligands=dock_ligands)(bundle)
        autonomous_docking.select_workspace(receptor, chain=chain, native_ligand=native_ligand, dock_ligands=dock_ligands)
        autonomous_docking.set_gridbox(method="native_ligand")
        autonomous_docking.set_docking_config(
            engine="vina_gpu_21",
            mode="standard",
            run_count=1,
            exhaustiveness=16,
        )

    return _prepare


def _combine_preparations(*preparers: Callable[[SeedBundle], None]) -> Callable[[SeedBundle], None]:
    valid_preparers = [prepare for prepare in preparers if callable(prepare)]

    def _prepare(bundle: SeedBundle) -> None:
        for prepare in valid_preparers:
            prepare(bundle)

    return _prepare


def _prepare_multi_target_bundle(*pdb_ids: str) -> Callable[[SeedBundle], None]:
    wanted = [autonomous_docking._normalize_receptor_id(pdb_id) for pdb_id in pdb_ids if autonomous_docking._normalize_receptor_id(pdb_id)]

    def _prepare(bundle: SeedBundle) -> None:
        _prepare_receptor_subset(*wanted)(bundle)
        _prepare_active_ligands(*(bundle.ligand_names[:3] or [bundle.ligand_name]))(bundle)

    return _prepare


def build_hard10_cases() -> list[CaseSpec]:
    return [
        CaseSpec(
            case_id="01_short_answer",
            prompt="DockUP'u tek cümlede söyle. Tool çağırma, kısa kal.",
            think_mode="auto",
            max_steps=4,
            check=_case_no_tool_short_answer,
            notes="Baseline chat davranışı.",
        ),
        CaseSpec(
            case_id="02_state_summary",
            prompt="Mevcut DockUP state'ini 3 maddede teknik olarak özetle; queue, gridbox ve run durumunu ayrı söyle.",
            think_mode="auto",
            max_steps=6,
            check=_case_state_summary,
            notes="State okuma ve kısa cevap.",
        ),
        CaseSpec(
            case_id="03_main_native_gridbox",
            prompt="MNA1 için ana native ligand üzerinden workspace'i kur ve gridbox'ı ayarla; CL gibi yardımcı iyonları ana ligand sayma.",
            think_mode="auto",
            max_steps=10,
            check=_case_main_native,
            notes="Ana native ligand seçimi.",
        ),
        CaseSpec(
            case_id="04_p2rank_fallback",
            prompt="P2R1 için native ligand yok; gridbox'ı otomatik gridfinder/P2Rank ile kur ve kısa yükleniyor durumunu göster.",
            think_mode="auto",
            max_steps=10,
            prepare=_prepare_selected_workspace("P2R1", chain="A", native_ligand="", dock_ligands="all"),
            check=_case_p2rank,
            notes="P2Rank fallback ve progress.",
        ),
        CaseSpec(
            case_id="05_config_only",
            prompt="RUN1 için docking config'i sade şekilde ayarla: vina_gpu_21, standard, run_count=1, exhaustiveness=16. Run başlatma.",
            think_mode="auto",
            max_steps=8,
            check=_case_config,
            notes="Config akışı.",
        ),
        CaseSpec(
            case_id="06_build_test",
            prompt="RUN1 için workspace ve grid hazır; build_test ile kuyruk doğrula, ağır run başlatma.",
            think_mode="auto",
            max_steps=10,
            prepare=_prepare_ready_build_test("RUN1", chain="B", native_ligand="NXL 308", dock_ligands="all"),
            check=_case_build_test,
            notes="Test kuyruk oluşturma.",
        ),
        CaseSpec(
            case_id="07_delete_specific",
            prompt="Sistemde varsa yalnızca {ligand} ligandını ve DEL1 receptorünü sil; diğer loaded şeylere dokunma.",
            think_mode="auto",
            max_steps=8,
            check=_case_delete_specific,
            notes="Spesifik silme.",
        ),
        CaseSpec(
            case_id="08_clarify_missing_receptor",
            prompt="Gridbox'ı ayarla ama hangi receptor olduğunu söylemiyorum; ilerlemek için tek kısa netleştirme sor.",
            think_mode="auto",
            max_steps=6,
            check=_case_clarify,
            notes="Belirsizlikte kısa soru.",
        ),
        CaseSpec(
            case_id="09_multi_target_flow",
            prompt="MNA1 için native ligand gridbox'ı ve P2R1 için P2Rank gridbox'ı kur; sonra RUN1 için config'i tamamla ve build_test yap. Eksik tek bilgi varsa sadece bir kısa soru sor.",
            think_mode="auto",
            max_steps=12,
            check=_case_final_boss,
            notes="Çok hedefli akış.",
        ),
        CaseSpec(
            case_id="10_full_run",
            prompt="RUN1 için her şey hazırsa full run başlat; değilse eksikleri tamamla ve sonra başlat.",
            think_mode="auto",
            max_steps=10,
            check=_case_full_run,
            notes="Gerçek run başlatma.",
        ),
    ]


def build_hard30_cases() -> list[CaseSpec]:
    cases = list(build_hard10_cases())

    def _multi_target_build_test(result: dict[str, Any], events: list[dict[str, Any]], bundle: SeedBundle) -> tuple[bool, str]:
        required = {"select_workspace", "set_gridbox", "set_docking_config", "build_or_run_queue"}
        tools = _tools_used(result)
        missing = [tool for tool in required if tool not in tools]
        if missing:
            return False, f"missing required tools: {missing}"
        run = _last_trace_tool_result(result, "build_or_run_queue").get("run", {})
        if not isinstance(run, dict) or run.get("test_mode") is not True:
            return False, "expected build_test/test_mode run"
        return _case_ok_default(result, events, bundle)

    def _multi_target_full_run(result: dict[str, Any], events: list[dict[str, Any]], bundle: SeedBundle) -> tuple[bool, str]:
        required = {"select_workspace", "set_gridbox", "set_docking_config", "build_or_run_queue"}
        tools = _tools_used(result)
        missing = [tool for tool in required if tool not in tools]
        if missing:
            return False, f"missing required tools: {missing}"
        run = _last_trace_tool_result(result, "build_or_run_queue").get("run", {})
        if not isinstance(run, dict) or run.get("started") is not True:
            return False, "full run was not started"
        if run.get("test_mode") is not False:
            return False, f"expected full run test_mode=False, saw {run.get('test_mode')}"
        return _case_ok_default(result, events, bundle)

    cases.extend(
        [
            CaseSpec(
                case_id="11_fetch_assets_basic",
                prompt="DEL1, MNA1 ve RUN1 receptorlerini ve {ligands} ligandlarını çek; kısa sonuç ver.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("fetch_assets"),
                notes="Basit asset çekme.",
            ),
            CaseSpec(
                case_id="12_inspect_assets",
                prompt="Yüklü receptor ve ligandları teknik ama kısa biçimde inspect_assets ile özetle.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("inspect_assets"),
                notes="Asset inceleme.",
            ),
            CaseSpec(
                case_id="13_show_viewer",
                prompt="MNA1 receptorünü viewer'da aç ve chain B'yi seç.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("show_in_viewer"),
                notes="Viewer seçimi.",
            ),
            CaseSpec(
                case_id="14_show_residues",
                prompt="MNA1 üzerindeki CL residue'larını show_residues ile vurgula.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("show_residues"),
                notes="Residue görünürlüğü.",
            ),
            CaseSpec(
                case_id="15_manual_gridbox",
                prompt="MNA1 için gridbox'ı manuel merkez 1.0,2.0,3.0 ile kur.",
                think_mode="auto",
                max_steps=8,
                prepare=_prepare_selected_workspace("MNA1"),
                check=_case_requires_tools("set_gridbox"),
                notes="Manuel gridbox.",
            ),
            CaseSpec(
                case_id="16_workspace_all",
                prompt="RUN1 için workspace'i seç ve dock_ligands olarak all bırak.",
                think_mode="auto",
                max_steps=8,
                check=_case_requires_tools("select_workspace"),
                notes="Workspace/all ligand davranışı.",
            ),
            CaseSpec(
                case_id="17_read_workflow_details",
                prompt="DockUP workflow ayrıntılarını read_tool_details('workflow') ile oku.",
                think_mode="auto",
                max_steps=4,
                check=_case_requires_tools("read_tool_details"),
                notes="Workflow yardımı.",
            ),
            CaseSpec(
                case_id="18_read_settings_details",
                prompt="DockUP settings ayrıntılarını read_tool_details('settings') ile oku.",
                think_mode="auto",
                max_steps=4,
                check=_case_requires_tools("read_tool_details"),
                notes="Ayar yardımı.",
            ),
            CaseSpec(
                case_id="19_build_only_queue",
                prompt="RUN1 için kuyruğu sadece oluştur, çalıştırma.",
                think_mode="auto",
                max_steps=8,
                prepare=_prepare_selected_workspace("RUN1"),
                check=_case_requires_build_only(),
                notes="Build-only queue.",
            ),
            CaseSpec(
                case_id="20_delete_all_ligands",
                prompt="Mevcut ligands'ı tamamen sil ve state'i temizle.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("delete_ligands"),
                notes="Ligand temizleme.",
            ),
            CaseSpec(
                case_id="21_delete_all_receptors",
                prompt="Mevcut receptor'ları tamamen sil ve state'i temizle.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("delete_receptors"),
                notes="Receptor temizleme.",
            ),
            CaseSpec(
                case_id="22_delete_queue_batches",
                prompt="Queue batchlerini tamamen sil ve geriye kalan durumu kısa özetle.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("delete_queue_batches"),
                notes="Queue temizleme.",
            ),
            CaseSpec(
                case_id="23_multi_target_two",
                prompt="MNA1 ve P2R1 için ayrı workspace/gridbox akışını kur; eksik bilgi varsa tek kısa soru sor.",
                think_mode="auto",
                max_steps=10,
                prepare=_prepare_receptor_subset("MNA1", "P2R1"),
                check=_case_requires_tools("select_workspace", "set_gridbox"),
                notes="İki hedefli akış.",
            ),
            CaseSpec(
                case_id="24_multi_target_three",
                prompt="MNA1, P2R1 ve RUN1 için üç hedefli workspace/gridbox/config akışını kur.",
                think_mode="auto",
                max_steps=12,
                prepare=_prepare_multi_target_bundle("MNA1", "P2R1", "RUN1"),
                check=_case_requires_tools("select_workspace", "set_gridbox", "set_docking_config"),
                notes="Üç hedefli kurulum.",
            ),
            CaseSpec(
                case_id="25_three_target_build_test",
                prompt="MNA1, P2R1 ve RUN1 için 3 ligandlı test kuyruğu kur ve build_test yap.",
                think_mode="auto",
                max_steps=12,
                prepare=_prepare_multi_target_bundle("MNA1", "P2R1", "RUN1"),
                check=_multi_target_build_test,
                notes="Üç hedefli build_test.",
            ),
            CaseSpec(
                case_id="26_current_selection_gridbox",
                prompt="RUN1 için current_selection gridbox kur; seçimi tekrar etme.",
                think_mode="auto",
                max_steps=8,
                prepare=_prepare_selected_workspace("RUN1", chain="B", native_ligand="NXL 308"),
                check=_case_requires_tools("set_gridbox"),
                notes="Mevcut seçime gridbox.",
            ),
            CaseSpec(
                case_id="27_p2rank_missing_native",
                prompt="P2R1 için usable native ligand yok; P2Rank/gridfinder ile gridbox kur ve kısa loading durumunu göster.",
                think_mode="auto",
                max_steps=10,
                prepare=_prepare_selected_workspace("P2R1", chain="A", native_ligand="", dock_ligands="all"),
                check=_case_p2rank,
                notes="P2Rank fallback.",
            ),
            CaseSpec(
                case_id="28_typo_ligand_recovery",
                prompt="Asprin diye yazılmış ligandı düzeltip fetch_assets ile çek.",
                think_mode="auto",
                max_steps=6,
                check=_case_requires_tools("fetch_assets"),
                notes="Ligand typo recovery.",
            ),
            CaseSpec(
                case_id="29_state_after_setup",
                prompt="Mevcut DockUP state'ini hızlı özetle; queue, gridbox ve run durumunu ayrı söyle.",
                think_mode="auto",
                max_steps=6,
                check=_case_state_summary,
                notes="State tekrar okuma.",
            ),
            CaseSpec(
                case_id="30_three_target_full_run",
                prompt="MNA1, P2R1 ve RUN1 için önce native/p2rank gridboxları kur, sonra RUN1 adına config ile tam run başlat.",
                think_mode="auto",
                max_steps=14,
                prepare=_prepare_multi_target_bundle("MNA1", "P2R1", "RUN1"),
                check=_multi_target_full_run,
                notes="Üç hedefli tam run.",
            ),
        ]
    )
    return cases


def build_agent_control_baseline_cases() -> list[CaseSpec]:
    old_cases_by_id = {case.case_id: case for case in build_hard30_cases()}
    selected_old_ids = [
        "02_state_summary",
        "03_main_native_gridbox",
        "04_p2rank_fallback",
        "06_build_test",
        "07_delete_specific",
    ]
    cases = [old_cases_by_id[case_id] for case_id in selected_old_ids]

    cases.extend(
        [
            CaseSpec(
                case_id="cb_06_two_receptors_three_grid_batches",
                prompt=(
                    "MNA1 ve P2R1 receptorleri ile {ligand_a} ve {ligand_b} ligandlarını kullan. "
                    "Üç ayrı test batch formatı kur: MNA1 native ligand grid size 18, "
                    "MNA1 manual center 1,2,3 size 20, P2R1 P2Rank grid size 22. "
                    "İkinci ve üçüncü batch'i append et; gerçek docking başlatma."
                ),
                think_mode="auto",
                max_steps=18,
                prepare=_prepare_multi_target_bundle("MNA1", "P2R1"),
                check=_case_requires_append_queue,
                notes="İki receptor, iki ligand, üç grid/batch ve append queue kontrolü.",
            ),
            CaseSpec(
                case_id="cb_07_state_forensics_read_only",
                prompt=(
                    "Canlı state'i oku ve sadece şu alanları raporla: selected receptor, aktif ligand sayısı, "
                    "queue count, run status ve gridbox var mı. State'i değiştirme."
                ),
                think_mode="auto",
                max_steps=6,
                check=_case_state_read_only,
                notes="Kompleks state sorusu, read-only davranış.",
            ),
            CaseSpec(
                case_id="cb_08_multi_config_append_build_only",
                prompt=(
                    "RUN1 ve {ligand_a} için aynı native gridbox ile iki farklı config denemesi hazırla: "
                    "vina_gpu_21 exhaustiveness 8 ve vina_gpu_21 exhaustiveness 32. "
                    "İkinci config batch'ini append et. Sadece build_only yap, gerçek run başlatma."
                ),
                think_mode="auto",
                max_steps=16,
                prepare=_prepare_multi_target_bundle("RUN1"),
                check=_case_requires_append_queue,
                notes="Aynı setup üstünde çoklu config ve append batch.",
            ),
            CaseSpec(
                case_id="cb_09_native_plus_p2rank_mixed",
                prompt=(
                    "MNA1 için yardımcı iyonları atlayıp ana native ligand ile gridbox kur; P2R1 için native ligand "
                    "olmadığından P2Rank/gridfinder ile gridbox kur. Sonra iki hedef için queue'yu sadece oluştur, "
                    "çalıştırma."
                ),
                think_mode="auto",
                max_steps=16,
                prepare=_prepare_multi_target_bundle("MNA1", "P2R1"),
                check=_case_requires_repeated_tools(select_workspace=1, set_gridbox=2, build_or_run_queue=1),
                notes="Native ligand ve P2Rank fallback karışık akış.",
            ),
            CaseSpec(
                case_id="cb_10_no_real_docking_guard",
                prompt=(
                    "RUN1 ve {ligand_a} için test amaçlı docking planını hazırla. Eksikleri tamamla, kuyruğu doğrula, "
                    "planlanan job sayısını söyle; gerçek docking kesinlikle başlatma."
                ),
                think_mode="auto",
                max_steps=12,
                prepare=_prepare_multi_target_bundle("RUN1"),
                check=_case_requires_no_real_run,
                notes="Modelin test-only güvenlik sınırını koruması.",
            ),
        ]
    )
    return cases
