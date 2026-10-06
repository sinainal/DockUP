"""Compact score plot with every run, sample SD, and a numerical table."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from .publication_data import arguments, load_dataset, export_data, plot_style, save_figure, POLYMER_COLORS
from .publication_layout import validate_options,load_options,configured_dataset,archive_options


def plot(data, out, dpi=600, options=None):
    options=validate_options(options);data=configured_dataset(data,options)
    plot_style()
    fig = plt.figure(figsize=(options['width'],options['score_height']))
    # The plotting area is shifted just enough to compensate for the y-axis
    # label; the visible figure, rather than only the axes box, is centered.
    gs = fig.add_gridspec(2 if options['score_include_table'] else 1, 1,
                          height_ratios=[3.75, 1.35] if options['score_include_table'] else [1],hspace=0.20,
                          left=0.10, right=0.975, top=0.925,
                          bottom=0.05 if options['score_include_table'] else .14)
    ax = fig.add_subplot(gs[0])
    offsets = np.linspace(-0.3, 0.3, len(data.ligands))
    for j, ligand in enumerate(data.ligands):
        color = POLYMER_COLORS[j % len(POLYMER_COLORS)]
        for i, receptor in enumerate(data.receptors):
            runs = data.group(receptor, ligand)
            values = [r.score for r in runs]
            x = i + offsets[j]
            # Deterministic horizontal separation: even identical scores remain visible.
            dx = np.linspace(-0.080, 0.060, len(values))
            ax.scatter(x + dx, values, s=10, color=color, edgecolor="white", linewidth=0.35, zorder=4)
            mean, sd = data.stats(receptor, ligand)
            ax.errorbar(x + .108, mean, yerr=sd, fmt="D", markersize=2.9, color="#1F2937",
                        markerfacecolor="white", markeredgewidth=0.7, linewidth=0.75, capsize=1.8, zorder=5)
    for x in np.arange(0.5, len(data.receptors)-0.5):
        ax.axvline(x, color="#E8EDF1", linewidth=0.6, zorder=0)
    ax.grid(axis="y", color="#DCE2E8", linewidth=0.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlim(-0.55, len(data.receptors)-0.45)
    ax.set_xticks(range(len(data.receptors)), [data.receptor_labels[r] for r in data.receptors])
    scores = [r.score for r in data.runs]
    ax.set_ylim(min(scores)-0.55, max(scores)+0.5)
    ax.set_ylabel("Vina docking score (kcal mol$^{-1}$)")
    ax.set_xlabel("Dopamine receptor subtype" if len(data.receptors)==5 else "Receptor")
    handles = [Line2D([], [], marker="o", linestyle="", color=POLYMER_COLORS[j%4], markersize=5,
                       label=data.ligand_labels[l]) for j,l in enumerate(data.ligands)]
    handles.append(Line2D([], [], marker="D", linestyle="", color="#1F2937", markerfacecolor="white", markersize=4, label="Mean ± SD"))
    fig.legend(handles=handles, loc="upper center", ncol=len(handles), frameon=False,
               bbox_to_anchor=(0.49, 0.985), fontsize=8, columnspacing=1.35, handletextpad=.45)
    if options['score_include_table']:
        table_ax = fig.add_subplot(gs[1]); table_ax.axis("off")
        rows = [[data.receptor_labels[r]] + ["%.2f ± %.2f" % data.stats(r,l) for l in data.ligands] for r in data.receptors]
        # A negative local x offset gives the table exact figure-centering.
        table = table_ax.table(cellText=rows, colLabels=["Receptor"]+[data.ligand_labels[l] for l in data.ligands],
                              bbox=[-0.043,0.05,1,0.9], cellLoc="center")
        table.auto_set_font_size(False); table.set_fontsize(options['table_size'])
        for (r,c), cell in table.get_celld().items():
            cell.set_edgecolor("#DAE1E7"); cell.set_linewidth(0.55)
            cell.set_facecolor("#F3F6F8" if r%2 else "white")
            if r==0:
                cell.set_facecolor("#E9EEF2" if c==0 else POLYMER_COLORS[(c-1)%4])
                cell.set_text_props(weight="bold", color="#25313F" if c==0 else "white")
            elif c==0: cell.set_text_props(weight="bold")
    save_figure(fig, out, "publication_scores", dpi); plt.close(fig)
    export_data(data, out, "publication_scores")
    archive_options(out,'publication_scores',options)


def main():
    args=arguments(__doc__).parse_args()
    data=load_dataset(args.root,args.batch_manifest,require_plip=False,all_ligands=args.all_ligands)
    plot(data,args.out,args.dpi,load_options(args.layout_options_json))
    print(args.out / "publication_scores.png")


if __name__=="__main__": main()
