"""Generate the three publication figures from one validated dataset."""
from .publication_data import arguments, load_dataset
from .publication_scores import plot as plot_scores
from .publication_interactions import plot as plot_interactions
from .publication_closeups import plot as plot_closeups
from .publication_layout import load_options


def main():
    parser = arguments(__doc__)
    parser.add_argument("--rerender", action="store_true")
    args = parser.parse_args()
    data = load_dataset(args.root, args.batch_manifest, all_ligands=args.all_ligands)
    options = load_options(args.layout_options_json)
    plot_scores(data, args.out, args.dpi, options)
    plot_interactions(data, args.out, args.dpi, options)
    plot_closeups(data, args.out, args.dpi, args.rerender, options)
    print(f"All three figures exported from the same {len(data.runs)} runs: {args.out}")


if __name__ == "__main__":
    main()
