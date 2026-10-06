"""Native modeling workbench API, shared by browser and CLI."""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from ..config import BASE, LIGAND_DIR, RECEPTOR_DIR, TEMPLATES_DIR
from ..modeling import store
from ..modeling.alignment import align, metrics
from ..modeling.schemas import ExperimentBuild, HomologyBuild, ModelBuild, PoseBuild, QuantumJob

router = APIRouter()
_cap_cache = (0, {})


def checked(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ValueError, KeyError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/quantum", response_class=HTMLResponse, include_in_schema=False)
def quantum_page():
    return (TEMPLATES_DIR / "quantum.html").read_text()


@router.get("/api/modeling/homology/dialog", response_class=HTMLResponse)
def homology_dialog():
    return (TEMPLATES_DIR / "homology_dialog.html").read_text()


@router.get("/api/modeling/capabilities")
def capabilities():
    global _cap_cache
    if time.monotonic() - _cap_cache[0] < 30:
        return _cap_cache[1]
    env = os.environ.copy()
    env["MODELING_DATA_DIR"] = str(store.ROOT / "capability_probe")
    try:
        run = subprocess.run([os.environ.get("DOCKUP_MODELING_PYTHON", sys.executable), "-m", "docking_app.modeling.worker", "--capabilities"], cwd=BASE, env=env, capture_output=True, text=True, timeout=25)
        data = json.loads(run.stdout.strip().splitlines()[-1]) if run.returncode == 0 else {"studio": {"status": "unavailable", "reason": run.stderr[-800:]}}
    except (ValueError, IndexError, OSError, subprocess.TimeoutExpired) as exc:
        data = {"studio": {"status": "unavailable", "reason": str(exc)}}
    _cap_cache = (time.monotonic(), data)
    return data


@router.get("/api/modeling/models")
def models():
    return store.list_models()


@router.get("/api/modeling/models/{model_id}/structure")
def structure(model_id: str, format: str = "pdb"):
    if format not in {"pdb", "sdf", "xyz"}:
        raise HTTPException(422, "Format must be pdb/sdf/xyz")
    directory = checked(store.model_dir, model_id)
    path = directory / ("structure." + format)
    if not path.is_file():
        raise HTTPException(404,"Structure format not found")
    return FileResponse(path, filename=model_id + "." + format)


@router.post("/api/modeling/models", status_code=202)
def build(request: ModelBuild):
    return checked(store.submit, "build", request.model_dump())


@router.get("/api/modeling/pose-sets")
def pose_sets():
    return store.list_pose_sets()


@router.post("/api/modeling/pose-sets", status_code=202)
def build_poses(request: PoseBuild):
    return checked(store.submit, "poses", request.model_dump())


@router.post("/api/modeling/quantum/jobs", status_code=202)
def calculate(request: QuantumJob):
    return checked(store.submit, "quantum", request.model_dump())


@router.post("/api/modeling/experiments", status_code=202)
def experiment(request: ExperimentBuild):
    return checked(store.submit, "experiment", request.model_dump())


@router.get("/api/modeling/jobs")
def jobs():
    return store.list_jobs()


@router.get("/api/modeling/jobs/{job_id}")
def job(job_id: str):
    return checked(store.get_job, job_id)


@router.post("/api/modeling/jobs/{job_id}/cancel")
def cancel(job_id: str):
    return checked(store.cancel, job_id)


@router.post("/api/modeling/jobs/{job_id}/retry", status_code=202)
def retry(job_id: str):
    return checked(store.retry, job_id)


@router.get("/api/modeling/jobs/{job_id}/artifacts/{index}")
def artifact(job_id: str, index: int):
    path, output = checked(store.artifact_file, job_id, index)
    return FileResponse(path, filename=path.name, media_type="chemical/x-pdb" if output.get("format") == "pdb" else None)


class PublishRequest(BaseModel):
    artifact_index: int = Field(ge=0)


@router.post("/api/modeling/jobs/{job_id}/publish")
def publish(job_id: str, request: PublishRequest):
    path, output = checked(store.artifact_file, job_id, request.artifact_index)
    row = checked(store.get_job, job_id)
    if row["state"] not in {"succeeded", "partially_succeeded"}:
        raise HTTPException(422, "Only successful artifacts can be published")
    target_root = LIGAND_DIR if output["kind"] == "molecule" else RECEPTOR_DIR
    if output["kind"] == "molecule":
        if output.get("format") != "sdf":
            raise HTTPException(422, "Publish the topology-preserving SDF, not PDB/XYZ")
        from rdkit import Chem
        mol = Chem.SDMolSupplier(str(path), removeHs=False)[0]
        if mol is None or len(Chem.GetMolFrags(mol)) != 1:
            raise HTTPException(422, "Disconnected surface/complex cannot be published as a single Vina ligand")
    elif output["kind"] == "receptor":
        if not output.get("assessment", {}).get("basic_geometry_passed"):
            raise HTTPException(422, "Basic model geometry failed; inspect assessment before export")
    else:
        raise HTTPException(422, "Diagnostic artifacts cannot be docked")
    # Content-addressed identity; repeated publication is harmless and never overwrites another structure.
    name = "MODEL_" + output["sha256"][:16].upper() + path.suffix
    destination = target_root / name
    if destination.exists() and store.sha(destination) != output["sha256"]:
        raise HTTPException(409, "Existing filename checksum conflict")
    if not destination.exists(): shutil.copy2(path, destination)
    store.write_json(store.ROOT / "published" / (name + ".json"), {"job_id": job_id, "artifact": output, "filename": name, "published_at": time.time()})
    if output["kind"] == "molecule":
        from ..state import STATE, save_state_cache
        if name not in STATE["active_ligands"]: STATE["active_ligands"].append(name)
        save_state_cache()
    return {"filename": name, "kind": output["kind"], "sha256": output["sha256"], "note": "Published selected geometry; molecular preparation remains a separate stage"}


@router.post("/api/modeling/jobs/{job_id}/derive", status_code=201)
def derive(job_id: str, request: PublishRequest):
    """Promote an explicit geometry into the library for a subsequent quantum job."""
    from datetime import datetime, timezone
    import uuid
    from rdkit import Chem
    path, output = checked(store.artifact_file, job_id, request.artifact_index)
    row = checked(store.get_job,job_id)
    if row["state"] not in {"succeeded","partially_succeeded"} or output.get("format")!="sdf" or not output.get("model_id"):
        raise HTTPException(422,"Select a successful, topology-preserving SDF artifact")
    source = checked(store.model_dir, output["model_id"])
    mol = Chem.SDMolSupplier(str(path),removeHs=False)[0]
    original = Chem.SDMolSupplier(str(source/"structure.sdf"),removeHs=False)[0]
    if mol is None or original is None or Chem.MolToSmiles(mol)!=Chem.MolToSmiles(original):
        raise HTTPException(422,"Geometry topology no longer matches the source model")
    manifest = json.loads((source/"manifest.json").read_text())
    if output.get("geometry")!="original":
        manifest["generation_history"]={"engine":manifest.get("engine"),"optimization":manifest.get("optimization"),"conformer_results":manifest.get("conformer_results",[])}
        manifest["optimization"]=output.get("geometry","selected")
        manifest["conformer_results"]=[]
    mid="mdl_"+datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:8]
    target=store.ROOT/"library/runs"/mid;target.mkdir(parents=True)
    shutil.copy2(path,target/"structure.sdf")
    Chem.MolToPDBFile(mol,str(target/"structure.pdb"));Chem.MolToXYZFile(mol,str(target/"structure.xyz"))
    manifest.update(id=mid,title=manifest["title"]+" · "+output.get("geometry","selected"),created_at=datetime.now(timezone.utc).isoformat(),
                    geometry_origin={"job_id":job_id,"parent_model_id":output["model_id"],"sha256":output["sha256"],"geometry":output.get("geometry")})
    store.write_json(target/"manifest.json",manifest)
    return manifest


def inspect_template(path):
    import gemmi
    structure = gemmi.read_structure(str(path))
    if len(structure) != 1:
        raise ValueError("Upload one structural model, not a multi-model ensemble")
    chains = []
    for chain in structure[0]:
        residues = [r for r in chain if r.het_flag == "A" and gemmi.find_tabulated_residue(r.name).is_amino_acid()]
        if residues:
            sequence = "".join(gemmi.find_tabulated_residue(r.name).one_letter_code for r in residues)
            chains.append({"chain": chain.name, "sequence": sequence, "residues": len(residues), "auth_numbers": [str(r.seqid) for r in residues]})
    if not chains or any(len(c["chain"]) != 1 for c in chains):
        raise ValueError("Template must contain protein chains with single-character PDB IDs")
    return structure, chains


@router.post("/api/modeling/homology/templates", status_code=201)
async def upload_template(file: UploadFile = File(...)):
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Template limit is 20 MB")
    import hashlib
    template_id = "tpl_" + hashlib.sha256(data).hexdigest()[:24]
    directory = store.template_dir(template_id); directory.mkdir(parents=True, exist_ok=True)
    filename = "input.cif" if (file.filename or "").lower().endswith((".cif", ".mmcif")) else "input.pdb"
    path = directory / filename
    path.write_bytes(data)
    structure, chains = checked(inspect_template, path)
    structure.write_pdb(str(directory / "template.pdb"))
    result = {"id": template_id, "filename": Path(file.filename or "template").name, "sha256": store.sha(directory / "template.pdb"), "chains": chains}
    store.write_json(directory / "manifest.json", result)
    return result


@router.get("/api/modeling/homology/templates/{template_id}/structure")
def template_structure(template_id: str):
    path = checked(store.template_dir, template_id) / "template.pdb"
    if not path.is_file(): raise HTTPException(404,"Template not found")
    return FileResponse(path)


@router.post("/api/modeling/homology/alignment")
def alignment(request: HomologyBuild):
    template = checked(lambda: json.loads((store.template_dir(request.template_id) / "manifest.json").read_text()))
    chains = {r["chain"]: r for r in template["chains"]}
    result = []
    for row in request.targets:
        if row.template_chain not in chains:
            raise HTTPException(422, "Template chain not found")
        sequence = chains[row.template_chain]["sequence"]
        a,b = (row.aligned_target,row.aligned_template) if row.aligned_target else align(row.sequence,sequence)
        if len(a) != len(b) or a.replace("-","") != row.sequence or b.replace("-","") != sequence:
            raise HTTPException(422,"Explicit alignment does not match target/template")
        result.append({"chain":row.chain,"subtype":row.subtype,"aligned_target":a,"aligned_template":b,**metrics(a,b)})
    return {"targets":result,"method":"Global linear-gap preview; review critical regions or supply explicit alignment"}


@router.post("/api/modeling/homology/jobs", status_code=202)
def homology(request: HomologyBuild):
    alignment(request)
    return checked(store.submit, "homology", request.model_dump())


@router.get("/api/modeling/results.csv")
def csv_results():
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["job_id","kind","job_state","model_id","stage","status","energy_hartree","delta_E_int_kcal_mol","protocol","reason"])
    for row in store.list_jobs(500):
        for record in row["result"].get("records", []):
            result = record.get("result", {})
            writer.writerow([row["id"],row["kind"],row["state"],record.get("model_id",""),record["stage"],record["status"],result.get("energy_hartree",""),result.get("delta_E_int_kcal_mol",""),json.dumps(result.get("protocol", result.get("cycle", []))),record.get("reason", result.get("reason",""))])
    return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=dockup_quantum_results.csv"})


class ArchiveImport(BaseModel):
    source: str = "standalone"
    model_ids: list[str] = Field(default_factory=list, max_length=100)


def archive_root(source):
    roots = {"standalone": Path.home()/".local/share/nanoplastic-studio/runs",
             "private": BASE.parent.parent/"nanoplastic-modeling-studio-private/modeling/runs"}
    if source not in roots: raise ValueError("Unknown archive source")
    return roots[source]


@router.get("/api/modeling/archive")
def archive(source: str = "standalone"):
    root = checked(archive_root, source)
    paths = sorted(root.glob("mdl_*/manifest.json"), key=lambda p:p.stat().st_mtime, reverse=True)[:100]
    return [json.loads(p.read_text()) for p in paths]


@router.post("/api/modeling/archive/import")
def import_archive(request: ArchiveImport):
    import re
    root = checked(archive_root, request.source)
    imported = []
    for mid in request.model_ids:
        if not re.fullmatch(r"mdl_[A-Za-z0-9_]+",mid): raise HTTPException(422,"Invalid model ID")
        source = root / mid
        if source.resolve().parent != root.resolve(): raise HTTPException(422,"Unsafe archive model directory")
        destination = store.ROOT / "library/runs" / mid
        if not (source/"manifest.json").is_file(): raise HTTPException(404,mid)
        if destination.exists():
            if store.sha(destination/"structure.sdf") != store.sha(source/"structure.sdf"): raise HTTPException(409,"Model ID conflict")
        else:
            destination.mkdir(parents=True)
            for name in ("manifest.json","structure.sdf","structure.pdb","structure.xyz"):
                item=source/name
                if item.is_symlink(): raise HTTPException(422,"Archive structure links are not imported")
                if item.exists(): shutil.copy2(item,destination/name)
        imported.append(mid)
    return {"imported":imported,"historical_data_changed":False}
