"""Scientific invariants: no run omission, correct denominator and numeric residue order."""
import json
from pathlib import Path

import pytest

from figure_scripts.final_plots.publication_data import Dataset, Run, interaction_matrices, load_dataset, residue_key
from figure_scripts.final_plots.publication_layout import validate_options,configured_dataset

def test_layout_options_are_bounded_and_preserve_selected_seeds():
    for invalid in [[],{'width':-1},{'unknown':2},{'receptors':['R','R']},{'interaction_mode':'fake'},
                    {'closeup_row_label_rotation':45},{'closeup_show_scores':'yes'}]:
        with pytest.raises(ValueError):validate_options(invalid)
    runs=[Run(r,'L',f'run{i}',-5,Path(),Path(),Path(),Path(),[]) for r in ['R','S'] for i in range(1,6)]
    data=Dataset(Path(),runs,['R','S'],['L'],{'R':'R','S':'S'},{'L':'L'})
    selected=configured_dataset(data,validate_options({'receptors':['R'],'receptor_labels':{'R':'Custom'}}))
    assert len(selected.runs)==5 and selected.receptor_labels['R']=='Custom'
    assert len(data.runs)==10

def test_serotonin_profile_is_detected_without_changing_identifiers():
    from figure_scripts.final_plots.publication_layout import is_serotonin
    runs=[Run('human_A_8AXD','L','run1',-5,Path(),Path(),Path(),Path(),[])]
    data=Dataset(Path(),runs,['human_A_8AXD'],['L'],{'human_A_8AXD':'Human long label'},{'L':'L'})
    options=validate_options()
    configured=configured_dataset(data,options)
    assert is_serotonin(configured,options)
    assert configured.receptor_labels['human_A_8AXD']=='5-HT3A'
    assert configured.runs[0].receptor=='human_A_8AXD'

def test_serotonin_closeup_layout_reuses_cache_and_does_not_clip_labels(monkeypatch,tmp_path):
    from PIL import Image
    from figure_scripts.final_plots import publication_closeups as closeups
    targets=['human_A_8AXD','human_AB_primary'];ligands=['PE','PET','PP','PS']
    runs=[Run(r,l,'run1',-5,Path(),Path(),Path(),Path(),
          [dict(residue='TRP85A',kind='hydrophobic_interaction')]) for r in targets for l in ligands]
    data=Dataset(tmp_path,runs,targets,ligands,{r:r for r in targets},{l:l for l in ligands})
    monkeypatch.setattr(closeups,'source_signature',lambda *_:{'fixture':'source-bound'})
    monkeypatch.setattr(closeups,'sha256',lambda _:'fixture-hash')
    monkeypatch.setattr(closeups,'export_data',lambda *_:None)
    def unexpected_render(*_args,**_kwargs):raise AssertionError('Valid cache must not rerender')
    monkeypatch.setattr(closeups,'render_one',unexpected_render)
    render=tmp_path/'closeup_renders';render.mkdir()
    labels=['5-HT3A','5-HT3AB model']
    for label in labels:
        for ligand in ligands:
            path=render/f'{label}_{ligand}_run1.png';Image.new('RGB',(160,130),'white').save(path)
            path.with_suffix('.json').write_text(json.dumps({'source_signature':{'fixture':'source-bound'}}))
    def check_geometry(fig,*_args):
        fig.canvas.draw();renderer=fig.canvas.get_renderer()
        assert fig.get_size_inches()[1]==4.4
        for ax in fig.axes:
            if ax.get_ylabel():
                assert ax.yaxis.label.get_rotation()==90
                bounds=ax.yaxis.label.get_window_extent(renderer)
                assert bounds.x0>=0 and bounds.x1<=fig.bbox.x1
        assert not any('r1 ' in text.get_text() for ax in fig.axes for text in ax.texts)
    monkeypatch.setattr(closeups,'save_figure',check_geometry)
    closeups.plot(data,tmp_path,dpi=80)


def test_empty_plip_run_counts_against_common_residue():
    contact=dict(residue="PHE100A",kind="hydrophobic_interaction")
    runs=[Run("R","L","run1",-5,Path(),Path(),Path(),Path(),[contact]),
          Run("R","L","run2",-4,Path(),Path(),Path(),Path(),[])]
    data=Dataset(Path(),runs,["R"],["L"],{"R":"R"},{"L":"L"})
    residues,freq,common=interaction_matrices(data,"R")
    assert residues==["PHE100A"]
    assert freq["PHE100A","L"]==1
    assert not common


def test_residue_sort_is_numeric_and_preserves_chain():
    assert sorted(["ALA101B","TRP9A","LYS81F","ALA101A"],key=residue_key)==["TRP9A","LYS81F","ALA101A","ALA101B"]


def test_expected_batch_refuses_missing_run(tmp_path):
    run=tmp_path/"R"/"L"/"run1";run.mkdir(parents=True)
    (run/"results.json").write_text(json.dumps({"run1":{"best_affinity":-6.0}}))
    manifest=tmp_path/"batch.tsv"
    manifest.write_text("R\tA\tL.sdf\tx\tx\t0\tx\t1\nR\tA\tL.sdf\tx\tx\t0\tx\t2\n")
    with pytest.raises(ValueError,match="Batch mismatch"):
        load_dataset(tmp_path,manifest,require_plip=False)


def test_median_selection_is_not_best_score_selection():
    scores=[-9.,-5.,-6.,-5.5,-5.7]
    runs=[Run("R","L",f"run{i+1}",s,Path(),Path(),Path(),Path(),[]) for i,s in enumerate(scores)]
    data=Dataset(Path(),runs,["R"],["L"],{}, {})
    assert data.representative("R","L").run=="run5"
    mean,sd=data.stats("R","L")
    assert mean==pytest.approx(-6.24)
    assert sd==pytest.approx((10.052/4)**0.5)


def test_pdbqt_precision_is_retained_but_mismatched_score_rejected(tmp_path):
    run=tmp_path/"R"/"L"/"run1";run.mkdir(parents=True)
    (run/"results.json").write_text(json.dumps({"run1":{"best_affinity":-10.21}}))
    poses=run/"R_results";poses.mkdir()
    pose=poses/"R_out_vina.pdbqt"
    pose.write_text("MODEL 1\nREMARK VINA RESULT: -10.211 0.000 0.000\nENDMDL\n")
    data=load_dataset(tmp_path,require_plip=False)
    assert data.runs[0].score==-10.211
    assert data.runs[0].reported_score==-10.21
    pose.write_text("MODEL 1\nREMARK VINA RESULT: -9.211 0.000 0.000\nENDMDL\n")
    with pytest.raises(ValueError,match="disagrees"):
        load_dataset(tmp_path,require_plip=False)


def test_publication_api_keeps_vector_and_provenance_exports(monkeypatch,tmp_path):
    import copy
    from types import SimpleNamespace
    from PIL import Image
    from starlette.background import BackgroundTasks
    from docking_app.models import GraphPayload
    from docking_app.routes import report
    from docking_app.state import REPORT_STATE
    snapshot=copy.deepcopy(REPORT_STATE)
    source=tmp_path/"source";source.mkdir()
    output=tmp_path/"output";output.mkdir()
    monkeypatch.setattr(report,"_resolve_report_root",lambda _:tmp_path)
    monkeypatch.setattr(report,"_resolve_report_source",lambda *_:source)
    monkeypatch.setattr(report,"_resolve_report_output_root",lambda *_:output)
    monkeypatch.setattr(report,"_collect_receptor_rows",lambda _:[{"ready":True}])
    def fake_run(cmd,**kwargs):
        out=Path(cmd[cmd.index("--out")+1])
        options=Path(cmd[cmd.index('--layout-options-json')+1])
        assert json.loads(options.read_text())['profile']=='serotonin'
        Image.new("RGB",(10,10),"white").save(out/"publication_scores.png")
        (out/"publication_scores.pdf").write_bytes(b"test vector export")
        (out/"publication_scores_runs.csv").write_text("run,score\n1,-7.5\n")
        return SimpleNamespace(returncode=0,stdout="done")
    monkeypatch.setattr(report.subprocess,"run",fake_run)
    try:
        REPORT_STATE["status"]="idle"
        tasks=BackgroundTasks()
        response=report.trigger_graphs(GraphPayload(root_path=str(tmp_path),scripts=["publication_scores"],publication_options={'profile':'serotonin'}),tasks)
        assert response.status_code==200
        tasks.tasks[0].func(*tasks.tasks[0].args,**tasks.tasks[0].kwargs)
        assert list(output.rglob("publication_scores_*.png"))
        assert list(output.rglob("publication_scores_*.pdf"))
        assert list(output.rglob("publication_scores_runs.csv"))
        assert not REPORT_STATE["errors"]
    finally:
        REPORT_STATE.clear();REPORT_STATE.update(snapshot)
