"""Generic chain-aware ProMod3 worker. No receptor-specific IDs or residue offsets."""
import ctypes
import hashlib
import json
import math
import os
import sys
from pathlib import Path

# This worker runs in OpenStructure's own Python environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modeling.alignment import align, metrics


def main():
    import gemmi
    import numpy as np
    import ost
    import promod3
    from ost import io, seq
    from promod3 import modelling
    directory = Path(sys.argv[1])
    request = json.loads((directory / "request.json").read_text())["payload"]
    loader_path = os.environ.get("DOCKUP_PROMOD3_LOADER")
    if loader_path and Path(loader_path).exists():
        loader = ctypes.CDLL(loader_path)
        loader.load_openmm_plugins.argtypes = [ctypes.c_char_p]
        loader.load_openmm_plugins.restype = ctypes.c_int
        if loader.load_openmm_plugins(os.environ["OPENMM_PLUGIN_DIR"].encode()) != 0:
            raise RuntimeError("OpenMM plugin loading failed")
    template = gemmi.read_structure(str(directory / "template.pdb"))
    simplified = gemmi.Structure(); simplified.add_model(gemmi.Model("1"))
    alignments, details = seq.AlignmentList(), []
    for row in request["targets"]:
        residues = [r for r in template[0][row["template_chain"]] if r.het_flag == "A" and gemmi.find_tabulated_residue(r.name).is_amino_acid()]
        template_sequence = "".join(gemmi.find_tabulated_residue(r.name).one_letter_code for r in residues)
        target = row["sequence"]
        a, b = (row["aligned_target"], row["aligned_template"]) if row.get("aligned_target") else align(target, template_sequence)
        if len(a) != len(b) or a.replace("-", "") != target or b.replace("-", "") != template_sequence or any(x == y == "-" for x,y in zip(a,b)):
            raise ValueError("Explicit alignment does not match target/template sequences")
        m = metrics(a, b)
        if m["coverage"] < .5 or m["identity"] < .2:
            raise ValueError("Template coverage/identity too low for this initial homology workflow; choose a better template or reviewed alignment")
        chain = gemmi.Chain(row["chain"])
        numbering = []
        for i, residue in enumerate(residues, 1):
            copy = residue.clone(); numbering.append(str(residue.seqid)); copy.seqid = gemmi.SeqId(i, " "); chain.add_residue(copy)
        simplified[0].add_chain(chain)
        aln = seq.CreateAlignment()
        aln.AddSequence(seq.CreateSequence(row["chain"] + "_target", a))
        aln.AddSequence(seq.CreateSequence(row["chain"] + "_template", b))
        alignments.append(aln)
        mapping, ti, si = [], row["target_start"] - 1, 0
        for x,y in zip(a,b):
            if x != "-": ti += 1
            if y != "-": si += 1
            if x != "-": mapping.append({"target":ti,"template_auth":numbering[si-1] if y != "-" else None,"identical":x==y})
        details.append({**row, **m, "aligned_target": a, "aligned_template": b, "residue_map": mapping})
    simplified.write_pdb(str(directory / "normalized_template.pdb"))
    entity = io.LoadPDB(str(directory / "normalized_template.pdb"))
    for aln, row in zip(alignments, request["targets"]):
        aln.AttachView(1, entity.Select("cname=" + row["chain"]))
    handle = modelling.BuildRawModel(alignments, chain_names=[row["chain"] for row in request["targets"]], aln_preprocessing=False)
    if request["refine"]:
        modelling.BuildFromRawModel(handle, use_amber_ff=False, model_termini=False)
    else:
        if handle.gaps: modelling.CloseGaps(handle)
        modelling.ReconstructSidechains(handle.model, keep_sidechains=True, build_disulfids=True)
    if handle.gaps: raise RuntimeError("Unclosed alignment gaps; cannot export a gap-free model")
    io.SavePDB(handle.model, str(directory / "model_internal.pdb"))
    model = gemmi.read_structure(str(directory / "model_internal.pdb")); model.remove_hydrogens()
    bad_links, mapped_chains, ca_displacements = [], [], []
    for row, detail in zip(request["targets"], details):
        chain = model[0][row["chain"]]
        sequence = "".join(gemmi.find_tabulated_residue(r.name).one_letter_code for r in chain)
        if sequence != row["sequence"]: raise RuntimeError("Output model sequence changed")
        for i, residue in enumerate(chain): residue.seqid = gemmi.SeqId(i + row["target_start"], " ")
        for first, second in zip(chain, list(chain)[1:]):
            c, n = first.find_atom("C", "*"), second.find_atom("N", "*")
            if not c or not n or not 1.15 <= c.pos.dist(n.pos) <= 1.55:
                bad_links.append({"chain":chain.name,"residue":str(first.seqid),"distance_A":c.pos.dist(n.pos) if c and n else None})
        mapped_chains.append({"chain":row["chain"],"subtype":row["subtype"],"template_chain":row["template_chain"],"sequence_verified":True,"residues":len(chain)})
        original = {str(r.seqid):r for r in template[0][row["template_chain"]]}
        for mp in detail["residue_map"]:
            if mp["template_auth"] and mp["identical"]:
                r = chain[mp["target"] - row["target_start"]]
                old = original[mp["template_auth"]]
                a, b = r.find_atom("CA","*"), old.find_atom("CA","*")
                if a and b: ca_displacements.append(a.pos.dist(b.pos))
    heavy = [(chain.name, str(r.seqid), atom) for chain in model[0] for r in chain for atom in r if atom.element.name != "H"]
    coords = np.array([list(atom.pos) for _,_,atom in heavy])
    if not np.isfinite(coords).all(): raise RuntimeError("Nonfinite model coordinates")
    # Inter-chain severe proximity only, not a substitute for MolProbity clashscore.
    clashes = []
    for i in range(0, len(heavy), 200):
        distances = np.linalg.norm(coords[i:i+200,None,:] - coords[None,:,:],axis=2)
        for a,b in zip(*np.where(distances < 1.2)):
            left, right = i + int(a), int(b)
            if left < right and heavy[left][0] != heavy[right][0]:
                clashes.append({"first":f"{heavy[left][0]}:{heavy[left][1]}:{heavy[left][2].name}","second":f"{heavy[right][0]}:{heavy[right][1]}:{heavy[right][2].name}","distance_A":float(distances[a,b])})
    model.write_pdb(str(directory / "model.pdb"))
    assessment = {"provider":"ProMod3","promod3_version":promod3.__version__,"ost_version":ost.VERSION,"model_type":"comparative model, not experimental",
        "chains":mapped_chains,"sequence_verified":True,"remaining_alignment_gaps":0,"bad_peptide_links":bad_links,"severe_interchain_proximity_pairs":clashes,
        "template_CA_displacement_RMS_A":float(np.sqrt(np.mean(np.square(ca_displacements)))) if ca_displacements else None,
        "basic_geometry_passed":not bad_links and not clashes,"modeling_issues":[str(i) for i in getattr(handle,"modelling_issues",[])],
        "advanced_assessment":{"MolProbity":"not run","QMEANDisCo":"not run","QMEANBrane":"not run"},
        "template_sha256":hashlib.sha256((directory/"template.pdb").read_bytes()).hexdigest(),
        "limitations":["Basic sequence/link/proximity assessment is not full structural validation","Global linear-gap preview alignment requires review in divergent/loop regions","No membrane MD or experimental validation","Template similarity is not independent validation"]}
    (directory / "assessment.json").write_text(json.dumps(assessment,indent=2,allow_nan=False))
    (directory / "alignment.json").write_text(json.dumps(details,indent=2))
    print("ProMod3 complete; basic geometry",assessment["basic_geometry_passed"],flush=True)


if __name__ == "__main__": main()
