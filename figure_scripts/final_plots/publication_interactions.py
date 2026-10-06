"""One vector composite of DockUP counts, run frequency and common interactions."""
import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch
from .publication_data import (arguments, load_dataset, export_data, plot_style, save_figure,
                               interaction_matrices, KIND_ORDER, KIND_LABELS, KIND_COLORS)
from .publication_layout import validate_options,load_options,configured_dataset,archive_options


def plot(data, out, dpi=600, options=None):
    options=validate_options(options);data=configured_dataset(data,options)
    if options['interaction_mode']=='common':return plot_common(data,out,dpi,options)
    if options['interaction_mode'] in ['counts','recurrence']:return plot_component(data,out,dpi,options)
    plot_style()
    ncols=len(data.receptors)
    matrices={r:interaction_matrices(data,r) for r in data.receptors}
    active=[k for k in KIND_ORDER if any(k in values[2].values() for values in matrices.values())]
    cmap=ListedColormap(["white"]+[KIND_COLORS[k] for k in active])
    norm=BoundaryNorm(np.arange(-.5,len(active)+1.5),len(active)+1)
    max_runs=max(len(data.group(r,l)) for r in data.receptors for l in data.ligands)
    freq_colors=["#FFFFFF", "#E8EEF1", "#CAD9DF", "#9DBBC6", "#6994A5", "#356879"]
    fcmap=ListedColormap(freq_colors) if max_runs==5 else plt.get_cmap("Blues",max_runs+1)
    fig=plt.figure(figsize=(options['width'],options['interaction_height']))
    outer=fig.add_gridspec(3,1,left=.088,right=.992,bottom=.13,top=.925,
                           hspace=options['row_gap'],height_ratios=[1.10,3.00,2.28])
    grids=[outer[row].subgridspec(1,ncols,wspace=options['column_gap']) for row in range(3)]
    ymax=max(sum(1 for (res,l),k in dominant.items() if l==lig) for _,_,dominant in matrices.values() for lig in data.ligands)
    audit=[]
    row_axes=[[],[],[]]
    for col,receptor in enumerate(data.receptors):
        residues,freq,dominant=matrices[receptor]
        common=[res for res in residues if any((res,l) in dominant for l in data.ligands)]
        ax=fig.add_subplot(grids[0][0,col]); row_axes[0].append(ax); bottom=np.zeros(len(data.ligands))
        for kind in active:
            counts=[sum(k==kind and l==lig for (res,l),k in dominant.items()) for lig in data.ligands]
            ax.bar(range(len(data.ligands)),counts,bottom=bottom,color=KIND_COLORS[kind],width=.68,edgecolor="white",linewidth=.3)
            bottom+=counts
        ax.set_ylim(0,max(2,ymax+2)); ax.set_yticks(range(0,ymax+3,max(1,(ymax+2)//4)))
        ax.set_xticks(range(len(data.ligands)),[data.ligand_labels[l] for l in data.ligands],rotation=45,ha="right",fontsize=6.5)
        ax.set_title(f"{data.receptor_labels[receptor]}",fontsize=9.5,pad=6)
        ax.spines[["top","right"]].set_visible(False); ax.grid(axis="y",alpha=.25,linewidth=.4); ax.set_axisbelow(True)
        if col==0: ax.set_ylabel("Common residues",fontsize=7)
        for row,labels in ((1,residues),(2,common)):
            a=fig.add_subplot(grids[row][0,col]); row_axes[row].append(a)
            if not labels:
                a.text(.5,.5,"No common\nresidues",ha="center",va="center",fontsize=7); a.axis("off"); continue
            mat=np.array([[freq[res,l] if row==1 else (active.index(dominant[res,l])+1 if (res,l) in dominant else 0)
                           for l in data.ligands] for res in labels])
            a.imshow(mat,aspect="auto",interpolation="nearest",cmap=fcmap if row==1 else cmap,
                     norm=BoundaryNorm(np.arange(-.5,max_runs+1.5),max_runs+1) if row==1 else norm)
            a.set_xticks(range(len(data.ligands)),[data.ligand_labels[l] for l in data.ligands],rotation=45,ha="right",fontsize=6.5)
            a.set_yticks(range(len(labels)),labels,fontsize=options['label_size'])
            a.tick_params(which="both",length=0)
            a.set_xticks(np.arange(-.5,len(data.ligands),1),minor=True); a.set_yticks(np.arange(-.5,len(labels),1),minor=True)
            a.grid(which="minor",color="#DDE4E9",linewidth=.35)
            for spine in a.spines.values():spine.set_visible(False)
            if row==1:
                for (y,x),value in np.ndenumerate(mat):
                    if value: a.text(x,y,str(value),ha="center",va="center",fontsize=6.5,color="white" if value>max_runs*.6 else "#263746")
        for res in residues:
            for lig in data.ligands:
                audit.append(dict(receptor=receptor,ligand=lig,residue=res,run_frequency=freq[res,lig],
                                  total_runs=len(data.group(receptor,lig)),common_dominant=dominant.get((res,lig),"")))
    section_titles=("A   Common-residue interaction classes",
                    "B   Residue recurrence across runs",
                    "C   Dominant class at common residues")
    # X tick labels extend below the axes.  Place B and C at the midpoint of
    # the *visible* gap (not merely the midpoint between axes boxes), so the
    # white space above and below each heading is optically equal.
    heading_y=[.968]
    tick_label_overhang=.032
    for previous,current in zip(row_axes,row_axes[1:]):
        previous_visible_bottom=min(a.get_position().y0 for a in previous)-tick_label_overhang
        current_top=max(a.get_position().y1 for a in current)
        heading_y.append((previous_visible_bottom+current_top)/2)
    for y,label in zip(heading_y,section_titles):
        fig.text(.5,y,label,fontsize=8.5,weight="bold",ha="center",va="center")
    fig.legend(handles=[Patch(facecolor=KIND_COLORS[k],label=KIND_LABELS[k]) for k in active],
               loc="lower center",bbox_to_anchor=(.5,.068),ncol=len(active),frameon=False,
               fontsize=options['legend_size'],columnspacing=1.2,handletextpad=.45)
    fig.legend(handles=[Patch(facecolor=fcmap(i/max(1,max_runs)),edgecolor="#C9D3D8",label=str(i)) for i in range(1,max_runs+1)],
               loc="lower center",bbox_to_anchor=(.5,.025),ncol=max_runs,frameon=False,
               fontsize=6.7,title="Run recurrence (B)",title_fontsize=6.7,
               columnspacing=1.1,handletextpad=.4)
    save_figure(fig,out,"publication_interactions",dpi); plt.close(fig)
    export_data(data,out,"publication_interactions")
    archive_options(out,'publication_interactions',options)
    with (out/"publication_interactions_cells.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=audit[0].keys());writer.writeheader();writer.writerows(audit)


def plot_common(data,out,dpi,options):
    """Standalone component C of the full interaction figure."""
    plot_style();matrices={r:interaction_matrices(data,r) for r in data.receptors}
    active=[k for k in KIND_ORDER if any(k in m[2].values() for m in matrices.values())]
    cmap=ListedColormap(['white']+[KIND_COLORS[k] for k in active])
    norm=BoundaryNorm(np.arange(-.5,len(active)+1.5),len(active)+1)
    fig,axes=plt.subplots(1,len(data.receptors),figsize=(options['width'],options['common_height']),squeeze=False)
    audit=[]
    for ax,r in zip(axes[0],data.receptors):
        residues,freq,dominant=matrices[r]
        common=[res for res in residues if any((res,l) in dominant for l in data.ligands)]
        if common:
            mat=np.array([[active.index(dominant[res,l])+1 if (res,l) in dominant else 0 for l in data.ligands] for res in common])
            ax.imshow(mat,aspect='auto',interpolation='nearest',cmap=cmap,norm=norm)
            ax.set_yticks(range(len(common)),common,fontsize=options['label_size'])
            ax.set_xticks(np.arange(-.5,len(data.ligands),1),minor=True)
            ax.set_yticks(np.arange(-.5,len(common),1),minor=True)
            ax.grid(which='minor',color='#DDE4E9',linewidth=.4)
        else:
            ax.text(.5,.5,'No common residues',ha='center',va='center',transform=ax.transAxes,fontsize=options['label_size'])
            ax.set_yticks([])
        ax.set_xticks(range(len(data.ligands)),[data.ligand_labels[l] for l in data.ligands],fontsize=options['label_size'])
        ax.set_title(data.receptor_labels[r],fontsize=9,pad=7);ax.tick_params(which='both',length=0)
        for spine in ax.spines.values():spine.set_visible(False)
        for res in residues:
            for l in data.ligands:
                audit.append(dict(receptor=r,ligand=l,residue=res,run_frequency=freq[res,l],
                                  total_runs=len(data.group(r,l)),common_dominant=dominant.get((res,l),'')))
    fig.subplots_adjust(left=.105,right=.975,top=.87,bottom=.23,wspace=options['column_gap'])
    if active:fig.legend(handles=[Patch(facecolor=KIND_COLORS[k],label=KIND_LABELS[k]) for k in active],
         loc='lower center',bbox_to_anchor=(.5,.025),ncol=min(4,len(active)),frameon=False,fontsize=options['legend_size'])
    save_figure(fig,out,'publication_interactions',dpi);plt.close(fig)
    export_data(data,out,'publication_interactions');archive_options(out,'publication_interactions',options)
    with (out/'publication_interactions_cells.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=audit[0].keys());writer.writeheader();writer.writerows(audit)


def plot_component(data,out,dpi,options):
    """Standalone A or B, computed from the exact same matrices as the composite."""
    plot_style();matrices={r:interaction_matrices(data,r) for r in data.receptors}
    active=[k for k in KIND_ORDER if any(k in m[2].values() for m in matrices.values())]
    max_runs=max(len(data.group(r,l)) for r in data.receptors for l in data.ligands)
    palette=['#FFFFFF','#E8EEF1','#CAD9DF','#9DBBC6','#6994A5','#356879']
    fcmap=ListedColormap(palette) if max_runs==5 else plt.get_cmap('Blues',max_runs+1)
    fig,axes=plt.subplots(1,len(data.receptors),figsize=(options['width'],options['component_height']),squeeze=False)
    ymax=max(sum(l==lig for res,l in dominant) for _,_,dominant in matrices.values() for lig in data.ligands)
    audit=[]
    for ax,r in zip(axes[0],data.receptors):
        residues,freq,dominant=matrices[r]
        if options['interaction_mode']=='counts':
            bottom=np.zeros(len(data.ligands))
            for kind in active:
                counts=[sum(k==kind and l==lig for (res,l),k in dominant.items()) for lig in data.ligands]
                ax.bar(range(len(data.ligands)),counts,bottom=bottom,color=KIND_COLORS[kind],width=.68,edgecolor='white',linewidth=.3)
                bottom+=counts
            ax.set_ylim(0,max(2,ymax+2));ax.set_yticks(range(0,ymax+3,max(1,(ymax+2)//4)))
            ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.25,linewidth=.4);ax.set_axisbelow(True)
            if r==data.receptors[0]:ax.set_ylabel('Common residues',fontsize=7)
        else:
            mat=np.array([[freq[res,l] for l in data.ligands] for res in residues])
            if residues:
                ax.imshow(mat,aspect='auto',interpolation='nearest',cmap=fcmap,
                    norm=BoundaryNorm(np.arange(-.5,max_runs+1.5),max_runs+1))
                ax.set_yticks(range(len(residues)),residues,fontsize=options['label_size'])
                ax.set_xticks(np.arange(-.5,len(data.ligands),1),minor=True)
                ax.set_yticks(np.arange(-.5,len(residues),1),minor=True)
                ax.grid(which='minor',color='#DDE4E9',linewidth=.35)
                for (y,x),value in np.ndenumerate(mat):
                    if value:ax.text(x,y,str(value),ha='center',va='center',fontsize=options['label_size'],
                         color='white' if value>max_runs*.6 else '#263746')
            else:ax.set_yticks([])
            for spine in ax.spines.values():spine.set_visible(False)
            ax.tick_params(which='both',length=0)
        ax.set_xticks(range(len(data.ligands)),[data.ligand_labels[l] for l in data.ligands],fontsize=options['label_size'])
        ax.set_title(data.receptor_labels[r],fontsize=9,pad=7)
        for res in residues:
            for l in data.ligands:
                audit.append(dict(receptor=r,ligand=l,residue=res,run_frequency=freq[res,l],
                       total_runs=len(data.group(r,l)),common_dominant=dominant.get((res,l),'')))
    fig.subplots_adjust(left=.105,right=.975,top=.87,bottom=.23 if options['interaction_mode']=='counts' else .14,
                        wspace=options['column_gap'])
    if options['interaction_mode']=='counts' and active:
        handles=[Patch(facecolor=KIND_COLORS[k],label=KIND_LABELS[k]) for k in active]
    else:
        handles=[Patch(facecolor=fcmap(i/max(1,max_runs)),label=str(i)) for i in range(1,max_runs+1)]
    if handles:fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.025),ncol=len(handles),frameon=False,fontsize=options['legend_size'])
    save_figure(fig,out,'publication_interactions',dpi);plt.close(fig)
    export_data(data,out,'publication_interactions');archive_options(out,'publication_interactions',options)
    with (out/'publication_interactions_cells.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=audit[0].keys());writer.writeheader();writer.writerows(audit)


def main():
    args=arguments(__doc__).parse_args()
    data=load_dataset(args.root,args.batch_manifest,all_ligands=args.all_ligands)
    plot(data,args.out,args.dpi,load_options(args.layout_options_json))
    print(args.out/"publication_interactions.png")


if __name__=="__main__":main()
