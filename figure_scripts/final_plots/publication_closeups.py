"""Ray-traced close-ups from raw complex coordinates and PLIP contacts."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from PIL import Image

from .publication_data import (arguments, load_dataset, export_data, plot_style, save_figure,
                               POLYMER_COLORS, KIND_COLORS, KIND_LABELS, KIND_ORDER, residue_key, sha256)

RENDER_VERSION = 3
from .publication_layout import validate_options,load_options,configured_dataset,archive_options,is_serotonin


def source_signature(run, ligand_color):
    return dict(complex_sha256=sha256(run.complex), plip_sha256=sha256(run.plip),
                ligand_color=ligand_color, render_version=RENDER_VERSION)


def render_one(run, output: Path, ligand_color: str, width=1600, height=1300):
    """A separate PyMOL worker bounds memory and keeps the report process responsive."""
    payload = dict(complex=str(run.complex), contacts=run.contacts, output=str(output),
                   ligand_color=ligand_color, width=width, height=height,
                   source_signature=source_signature(run,ligand_color))
    job = output.with_suffix(".json")
    job.write_text(json.dumps(payload, indent=2)+"\n")
    # Use the DockUP environment when the caller is a system Python without PyMOL.
    python = sys.executable
    try:
        import pymol2  # noqa: F401
    except ImportError:
        local_python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
        if not local_python.exists():
            raise RuntimeError("PyMOL is required to regenerate molecular close-ups")
        python = str(local_python)
    process = subprocess.run([python, "-m", __name__ if __name__ != "__main__" else "figure_scripts.final_plots.publication_closeups",
                              "--worker", str(job)], cwd=Path(__file__).resolve().parents[2],
                             env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True,text=True,timeout=180)
    output.with_suffix(".log").write_text(process.stdout+process.stderr)
    if process.returncode or not output.is_file():
        raise RuntimeError(f"PyMOL render failed for {run.directory}: {(process.stdout+process.stderr)[-1800:]}")
    return job


def worker(job: Path):
    import pymol2
    payload=json.loads(job.read_text())
    with pymol2.PyMOL() as pymol:
        cmd=pymol.cmd
        cmd.set("max_threads",4)
        cmd.load(payload["complex"],"complex")
        cmd.remove("solvent")
        cmd.hide("everything")
        cmd.select("ligand","complex and resn UNL")
        if not cmd.count_atoms("ligand"):
            raise ValueError("No UNL ligand in source complex")
        cmd.select("protein","complex and not resn UNL")
        residues=sorted({(c["chain"],c["resnr"]) for c in payload["contacts"]})
        selection=" or ".join(f"(protein and chain {chain} and resi {number})" for chain,number in residues) or "none"
        cmd.select("contact_residues",selection)
        cmd.select("sidechains","contact_residues and not name C+O+N")
        # Keep backbone atoms when PLIP explicitly assigns a contact to them.
        # Otherwise an N/O endpoint can float next to a hidden backbone atom.
        backbone=[]
        model=cmd.get_model("contact_residues and name C+O+N")
        for contact in payload["contacts"]:
            xyz=contact["protein_xyz"]
            if xyz is None:continue
            for atom in model.atom:
                if (atom.chain==contact["chain"] and atom.resi==contact["resnr"]
                        and math.dist(atom.coord,xyz)<.01):
                    backbone.append(f"(protein and chain {atom.chain} and resi {atom.resi})")
        if backbone:
            cmd.select("sidechains","sidechains or " + " or ".join(sorted(set(backbone))))
        cmd.show("sticks","sidechains or ligand")
        cmd.hide("everything","elem H")
        cmd.set_color("protein_carbon",[.62,.67,.72])
        cmd.color("protein_carbon","protein")
        h=payload["ligand_color"].lstrip("#")
        cmd.set_color("ligand_carbon",[int(h[i:i+2],16)/255 for i in (0,2,4)])
        cmd.color("ligand_carbon","ligand")
        cmd.color("red","elem O");cmd.color("blue","elem N");cmd.color("yellow","elem S")
        cmd.set("stick_radius",.13,"protein");cmd.set("stick_radius",.23,"ligand")
        cmd.set("stick_quality",20)
        cmd.set("orthoscopic",1);cmd.set("depth_cue",0);cmd.set("fog",0)
        cmd.set("antialias",2);cmd.set("ray_shadows",0)
        cmd.set("ambient",.48);cmd.set("direct",.58);cmd.set("specular",.22);cmd.set("shininess",25)
        cmd.set("ray_opaque_background",1);cmd.bg_color("white")
        cmd.orient("ligand or sidechains")
        # Explicit PLIP geometry; no new distance search or inferred bonds.
        for i,c in enumerate(payload["contacts"]):
            if c["ligand_xyz"] is None or c["protein_xyz"] is None:continue
            color=KIND_COLORS[c["kind"]]
            cmd.set_color(f"contact_color_{i}",[int(color[j:j+2],16)/255 for j in (1,3,5)])
            cmd.pseudoatom(f"end_l_{i}",pos=c["ligand_xyz"])
            cmd.pseudoatom(f"end_p_{i}",pos=c["protein_xyz"])
            cmd.distance(f"contact_{i}",f"end_l_{i}",f"end_p_{i}")
            cmd.set("dash_color",f"contact_color_{i}",f"contact_{i}")
            cmd.set("dash_radius",.023 if c["kind"]=="hydrophobic_interaction" else .045,f"contact_{i}")
            cmd.set("dash_length",.16,f"contact_{i}");cmd.set("dash_gap",.18,f"contact_{i}")
            cmd.hide("labels",f"contact_{i}");cmd.hide("everything",f"end_l_{i} or end_p_{i}")
        cmd.zoom("ligand or sidechains",buffer=2.8,complete=1)
        cmd.clip("slab",70)
        cmd.set("ray_trace_mode",1);cmd.set("ray_trace_gain",.03)
        # Retain the scene and view for exact reproduction/editing.
        cmd.save(str(Path(payload["output"]).with_suffix(".pse")))
        cmd.png(payload["output"],width=payload["width"],height=payload["height"],dpi=600,ray=1,quiet=1)
        Path(payload["output"]).with_suffix(".view.json").write_text(json.dumps(list(cmd.get_view()))+"\n")


def plot(data,out,dpi=600,rerender=False,options=None):
    options=validate_options(options);data=configured_dataset(data,options)
    plot_style()
    render_dir=out/"closeup_renders";render_dir.mkdir(parents=True,exist_ok=True)
    nrows,ncols=len(data.receptors),len(data.ligands)
    serotonin=is_serotonin(data,options)
    rotation=options['closeup_row_label_rotation']
    if rotation is None:rotation=90 if serotonin else 0
    show_scores=options['closeup_show_scores']
    if show_scores is None:show_scores=not serotonin
    height=options['closeup_height'] or (4.4 if serotonin and nrows==2 else 7.8)
    fig,axes=plt.subplots(nrows,ncols,figsize=(options['width'],height),squeeze=False)
    selected=[]
    for i,receptor in enumerate(data.receptors):
        for j,ligand in enumerate(data.ligands):
            run=data.representative(receptor,ligand)
            path=render_dir/f"{data.receptor_labels[receptor]}_{data.ligand_labels[ligand]}_{run.run}.png"
            cache_job=path.with_suffix(".json")
            try:
                valid_cache=(path.is_file() and json.loads(cache_job.read_text()).get("source_signature")==source_signature(run,POLYMER_COLORS[j%4]))
            except (OSError,ValueError):
                valid_cache=False
            if not valid_cache or rerender:
                render_one(run,path,POLYMER_COLORS[j%4])
            ax=axes[i,j];ax.imshow(Image.open(path));ax.set_xticks([]);ax.set_yticks([])
            for spine in ax.spines.values():spine.set_color("#DCE2E8");spine.set_linewidth(.55)
            if i==0:ax.set_title(data.ligand_labels[ligand],color=POLYMER_COLORS[j%4],fontsize=10,pad=5)
            if j==0:ax.set_ylabel(data.receptor_labels[receptor],fontsize=8.5,rotation=rotation,
                        ha='center' if rotation==90 else 'right',va='center',labelpad=12 if rotation==90 else 8)
            if show_scores:
                ax.text(.97,.035,f"r{run.run.removeprefix('run')}  {run.score:.3f}",transform=ax.transAxes,ha="right",fontsize=5.8,
                        bbox=dict(facecolor="white",edgecolor="none",pad=1.2,alpha=.9))
            if serotonin:ax.text(.025,.97,f'{chr(65+i)}{j+1}',transform=ax.transAxes,
                        ha='left',va='top',fontsize=7,weight='bold',color='#25313F')
            selected.append(dict(receptor=receptor,ligand=ligand,run=run.run,score=run.score,
                                 complex=str(run.complex),complex_sha256=sha256(run.complex),plip=str(run.plip),
                                 plip_sha256=sha256(run.plip),render=str(path.relative_to(out))))
            print(f"Rendered {i*ncols+j+1}/{nrows*ncols}: {receptor}/{ligand}/{run.run}",flush=True)
    fig.subplots_adjust(left=.075,right=.975 if serotonin else .992,
                        top=.915 if serotonin else .958,bottom=.12 if serotonin else .075,
                        hspace=.055,wspace=.035)
    kinds=[k for k in KIND_ORDER if any(c["kind"]==k for x in selected for r in data.group(x["receptor"],x["ligand"]) if r.run==x["run"] for c in r.contacts)]
    fig.legend(handles=[Line2D([],[],ls="--",color=KIND_COLORS[k],label=KIND_LABELS[k]) for k in kinds],
               loc="lower center",bbox_to_anchor=(.5,.014),ncol=len(kinds),frameon=False,
               fontsize=options['legend_size'],columnspacing=1.0,handletextpad=.35)
    save_figure(fig,out,"publication_closeups",dpi);plt.close(fig)
    export_data(data,out,"publication_closeups")
    archive_options(out,'publication_closeups',options)
    (out/"publication_closeups_selection.json").write_text(json.dumps(selected,indent=2)+"\n")


def main():
    if len(sys.argv)==3 and sys.argv[1]=="--worker":
        worker(Path(sys.argv[2]));return
    parser=arguments(__doc__);parser.add_argument("--rerender",action="store_true")
    args=parser.parse_args()
    data=load_dataset(args.root,args.batch_manifest,all_ligands=args.all_ligands)
    options=load_options(args.layout_options_json);data=configured_dataset(data,options)
    plot(data,args.out,args.dpi,args.rerender,options)
    print(args.out/"publication_closeups.png")


if __name__=="__main__":main()
