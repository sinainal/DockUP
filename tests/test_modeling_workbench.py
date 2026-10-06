"""Modeling API contracts and isolated real xTB vertical slice (no historical writes)."""
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from rdkit import Chem
from rdkit.Chem import AllChem

from docking_app.modeling import store
from docking_app.modeling.alignment import align, metrics
from docking_app.modeling.schemas import HomologyBuild, ModelBuild, PoseBuild, QuantumJob
from docking_app.modeling.worker import check_orca, optimized_molecule, spin_check
from docking_app.routes import modeling


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store,"ROOT",tmp_path/"data")
    monkeypatch.setattr(modeling,"LIGAND_DIR",tmp_path/"ligands")
    monkeypatch.setattr(modeling,"RECEPTOR_DIR",tmp_path/"receptors")
    modeling.LIGAND_DIR.mkdir();modeling.RECEPTOR_DIR.mkdir()
    app=FastAPI();app.include_router(modeling.router)
    with TestClient(app) as test:
        yield test


def wait(job_id, seconds=40):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        result=store.get_job(job_id)
        if result["state"] not in {"queued","running"}:return result
        time.sleep(.15)
    store.cancel(job_id)
    raise AssertionError("Bounded test job timed out")


def molecule_files(tmp_path):
    mol=Chem.AddHs(Chem.MolFromSmiles("O"));AllChem.EmbedMolecule(mol,randomSeed=10)
    sdf=tmp_path/"water.sdf";writer=Chem.SDWriter(str(sdf));writer.write(mol);writer.close()
    xyz=tmp_path/"water.xyz";Chem.MolToXYZFile(mol,str(xyz))
    return mol,sdf,xyz


def test_alignment_roundtrip():
    a,b=align("ACDKEFG","ACDEFG")
    assert a.replace("-","")=="ACDKEFG" and b.replace("-","")=="ACDEFG"
    assert metrics(a,b)["template_gaps"]==1


@pytest.mark.parametrize("data",[{"polymer":"PET","repeats":18},{"smiles":"O","chembl_id":"CHEMBL1"},{"family":"small_molecule","model_kind":"surface_patch"}])
def test_invalid_build(data):
    with pytest.raises(ValidationError):ModelBuild(**data)


@pytest.mark.parametrize("data",[{"model_ids":["../file"]},{"model_ids":["mdl_a"],"engine":"pyscf","calculation":"optimization"},{"model_ids":["mdl_a"],"calculation":"frequency"},{"model_ids":["mdl_a","mdl_a"]}])
def test_invalid_quantum(data):
    with pytest.raises(ValidationError):QuantumJob(**data)


def test_pose_limit():
    with pytest.raises(ValidationError):PoseBuild(surface_model_id="mdl_x",adsorbate_model_id="mdl_y",site_count=25,orientations_per_site=8)


def test_optimized_sdf_topology(tmp_path):
    mol,sdf,xyz=molecule_files(tmp_path)
    result=optimized_molecule(sdf,xyz)
    assert Chem.MolToSmiles(result)==Chem.MolToSmiles(mol)
    assert result.GetNumAtoms()==mol.GetNumAtoms()


def test_xyz_wrong_order(tmp_path):
    _,sdf,xyz=molecule_files(tmp_path)
    xyz.write_text(xyz.read_text().replace("O ","C ",1))
    with pytest.raises(ValueError,match="element order"):optimized_molecule(sdf,xyz)


def test_xyz_nonfinite(tmp_path):
    _,sdf,xyz=molecule_files(tmp_path)
    lines=xyz.read_text().splitlines();cells=lines[2].split();cells[1]="nan";lines[2]=" ".join(cells);xyz.write_text("\n".join(lines))
    with pytest.raises(ValueError,match="Nonfinite"):optimized_molecule(sdf,xyz)


def test_spin_parity(tmp_path):
    mol,_,_=molecule_files(tmp_path)
    spin_check(mol,0,0)
    with pytest.raises(ValueError,match="parity"):spin_check(mol,1,0)


def test_screen_reader_not_orca(monkeypatch):
    monkeypatch.setenv("DOCKUP_ORCA_BIN","/usr/bin/orca")
    assert check_orca()["status"]=="unavailable"


def test_native_page_and_routes(client):
    response=client.get("/quantum");assert response.status_code==200
    assert 'id="qmViewport"' in response.text and 'id="qmExperiment"' in response.text
    assert client.get("/api/modeling/homology/dialog").status_code==200
    assert client.get("/api/modeling/models").json()==[]
    assert client.post("/api/modeling/models",json={"unexpected":1}).status_code==422
    assert client.get("/api/modeling/jobs/../../config").status_code==404


def test_ui_shell_and_fixed_homology_actions(client):
    from bs4 import BeautifulSoup
    html=BeautifulSoup(client.get("/quantum").text,"html.parser")
    assert html.select_one(".app-shell .topbar .brand-mark")
    assert html.select_one('link[href="/static/styles.css?v=65"]')
    panes=html.select(".qm-layout > .panel")
    assert len(panes)==2
    assert panes[0].select_one(".qm-panel-scroll #qmBuild")
    assert panes[0].select_one("#qmModels")
    assert panes[1].select_one("#qmViewport")
    popup=BeautifulSoup(client.get("/api/modeling/homology/dialog").text,"html.parser")
    assert popup.select_one(".hm-footer #hmBuild")["form"]=="hmForm"
    assert popup.select_one(".hm-footer #hmPublish")
    assert popup.select_one('#hmClose[aria-label]')
    assert popup.select_one("#hmViewTemplate") and popup.select_one("#hmViewModel")


def test_missing_model_rejected(client):
    response=client.post("/api/modeling/quantum/jobs",json={"model_ids":["mdl_missing"]})
    assert response.status_code==404


def test_artifact_tampering_and_idempotent_publish(client,tmp_path,monkeypatch):
    from docking_app.state import STATE
    before=list(STATE["active_ligands"])
    monkeypatch.setattr("docking_app.state.save_state_cache",lambda:None)
    monkeypatch.setattr(store.EXECUTOR,"submit",lambda *a:None)
    try:
        mol,sdf,_=molecule_files(tmp_path)
        job=store.submit("build",ModelBuild().model_dump())
        store.cancel(job["id"])
        directory=store.job_dir(job["id"]);path=directory/"water.sdf";path.write_bytes(sdf.read_bytes())
        output={"file":"water.sdf","sha256":store.sha(path),"kind":"molecule","format":"sdf"}
        store.update(job["id"],state="succeeded",result={"outputs":[output]})
        assert client.post(f"/api/modeling/jobs/{job['id']}/publish",json={"artifact_index":0}).status_code==200
        assert client.post(f"/api/modeling/jobs/{job['id']}/publish",json={"artifact_index":0}).status_code==200
        assert len(list(modeling.LIGAND_DIR.glob("*.sdf")))==1
        path.write_text("tampered")
        assert client.get(f"/api/modeling/jobs/{job['id']}/artifacts/0").status_code==422
    finally:STATE["active_ligands"]=before


def test_disconnected_export_rejected(client,tmp_path,monkeypatch):
    monkeypatch.setattr(store.EXECUTOR,"submit",lambda *a:None)
    job=store.submit("build",ModelBuild().model_dump())
    path=store.job_dir(job["id"])/"complex.sdf"
    mol=Chem.AddHs(Chem.MolFromSmiles("O.O"));AllChem.EmbedMolecule(mol,randomSeed=2)
    writer=Chem.SDWriter(str(path));writer.write(mol);writer.close()
    store.update(job["id"],state="succeeded",result={"outputs":[{"file":"complex.sdf","sha256":store.sha(path),"format":"sdf","kind":"molecule"}]})
    assert client.post(f"/api/modeling/jobs/{job['id']}/publish",json={"artifact_index":0}).status_code==422


def test_real_build_and_xtb(client):
    # Fresh PE dimer, max 40 s wait; all outputs in pytest tmp_path.
    job=client.post("/api/modeling/models",json={"polymer":"PE","repeats":2,"conformers":2}).json()
    row=wait(job["id"])
    assert row["state"]=="succeeded",row
    mid=row["result"]["records"][0]["model"]["id"]
    original=store.sha(store.model_dir(mid)/"structure.sdf")
    job=client.post("/api/modeling/quantum/jobs",json={"model_ids":[mid],"engine":"xtb","timeout_seconds":30,"threads":1}).json()
    row=wait(job["id"])
    assert row["state"]=="succeeded",row
    assert any(o.get("geometry")=="xtb_optimized" and o["format"]=="sdf" for o in row["result"]["outputs"])
    assert store.sha(store.model_dir(mid)/"structure.sdf")==original
    record=row["result"]["records"][0]["result"]
    assert record["converged"] and record["energy_hartree"]<0
    assert "energy_hartree" in client.get("/api/modeling/results.csv").text
    index=next(i for i,o in enumerate(row["result"]["outputs"]) if o.get("geometry")=="xtb_optimized" and o["format"]=="sdf")
    derived=client.post(f"/api/modeling/jobs/{row['id']}/derive",json={"artifact_index":index})
    assert derived.status_code==201,derived.text
    new=derived.json();assert new["id"]!=mid
    assert new["geometry_origin"]["parent_model_id"]==mid
    assert store.sha(store.model_dir(new["id"])/"structure.sdf")==row["result"]["outputs"][index]["sha256"]
    assert store.sha(store.model_dir(mid)/"structure.sdf")==original


def test_real_pyscf_water(client):
    pytest.importorskip("pyscf");pytest.importorskip("pyscf.dispersion.dftd3")
    job=client.post("/api/modeling/models",json={"family":"small_molecule","smiles":"O","name":"Fresh water test","conformers":1}).json()
    row=wait(job["id"]);assert row["state"]=="succeeded",row
    mid=row["result"]["records"][0]["model"]["id"]
    job=client.post("/api/modeling/quantum/jobs",json={"model_ids":[mid],"engine":"pyscf","calculation":"singlepoint","timeout_seconds":30,"threads":1}).json()
    row=wait(job["id"]);assert row["state"]=="succeeded",row
    value=row["result"]["records"][0]["result"]
    assert value["converged"] and -77<value["energy_hartree"]<-75
    assert value["protocol"]["solvent_model"]=="PCM"
    assert value["protocol"]["method"]=="PBE0-D3(BJ)/def2-TZVP"
    assert any(o.get("format")=="pdb" for o in row["result"]["outputs"])


def test_real_pose_and_interaction(client):
    job=client.post("/api/modeling/experiments",json={"polymers":["PE"],"repeats":[2],"sites":1,"orientations":1,"conformers":2,"run_xtb":False}).json()
    row=wait(job["id"]);assert row["state"]=="succeeded",row
    ps=next(r["pose_set"] for r in row["result"]["records"] if r["stage"]=="poses")
    assert len(ps["poses"])==1
    mid=ps["poses"][0]["model_id"]
    response=client.post("/api/modeling/quantum/jobs",json={"model_ids":[mid],"calculation":"interaction","engine":"xtb","timeout_seconds":30,"threads":1})
    assert response.status_code==202,response.text
    row=wait(response.json()["id"]);assert row["state"]=="succeeded",row
    value=row["result"]["records"][0]["result"]
    cycle=value["cycle"]
    assert len(cycle)==3 and all(v["converged"] for v in cycle)
    assert value["delta_E_int_hartree"]==pytest.approx(cycle[0]["energy_hartree"]-cycle[1]["energy_hartree"]-cycle[2]["energy_hartree"])
    assert "NOT binding free energy" in value["interpretation"]
    directory=store.job_dir(row["id"])/"studio/runs"
    manifest=json.loads((store.model_dir(mid)/"manifest.json").read_text())
    for key in ("surface_model_id","adsorbate_model_id"):
        assert (directory/manifest["source"][key]/"manifest.json").is_file()


def test_model_only_experiment(client):
    job=client.post("/api/modeling/experiments",json={"polymers":["PE"],"repeats":[2],"generate_poses":False,"conformers":2}).json()
    row=wait(job["id"]);assert row["state"]=="succeeded",row
    assert [r["stage"] for r in row["result"]["records"]]==["build"]


def test_live_partial_event_line(client,monkeypatch):
    monkeypatch.setattr(store.EXECUTOR,"submit",lambda *a:None)
    row=store.submit("build",ModelBuild().model_dump())
    (store.job_dir(row["id"])/"events.jsonl").write_text('{"stage":"build"}\n{"unfinished":')
    response=client.get("/api/modeling/jobs/"+row["id"])
    assert response.status_code==200
    assert response.json()["events"]==[{"stage":"build"}]


def test_template_errors_and_duplicate_chains(client):
    assert client.get("/api/modeling/homology/templates/tpl_"+"a"*24+"/structure").status_code==404
    response=client.post("/api/modeling/homology/alignment",json={"template_id":"tpl_"+"a"*24,"targets":[{"chain":"A","template_chain":"A","sequence":"ACD"}]})
    assert response.status_code==404
    with pytest.raises(ValidationError):
        HomologyBuild(template_id="tpl_"+"a"*24,targets=[{"chain":"A","template_chain":"A","sequence":"ACD"}]*2)


def test_cli_help():
    run=subprocess.run([str(Path(__file__).resolve().parents[1]/".venv/bin/python"),"-m","docking_app.cli","homology","--help"],capture_output=True,text=True,timeout=10)
    assert run.returncode==0 and "template" in run.stdout and "alignment" in run.stdout
