"""Bounded worker; only called with a private job directory by the store.

The Studio application is loaded as a package, not copied or forked into DockUP.
All Studio writes target the job's isolated snapshot. No historical dataset is
automatically imported. Optional providers report unavailable, never mock success.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def studio():
    source = Path(os.environ.get("DOCKUP_STUDIO_SOURCE", str(Path(__file__).resolve().parents[2].parent.parent / "nanoplastic-modeling-studio")))
    if not (source / "__init__.py").is_file():
        raise RuntimeError("Studio source not found; configure DOCKUP_STUDIO_SOURCE")
    name = "dockup_studio_backend"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, source / "__init__.py", submodule_search_locations=[str(source)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    xtb = importlib.import_module(name + ".xtb")
    archived = source.parent / "nanoplastic-modeling-studio-private/artice/execute_e1/tools/xtb_root"
    if archived.is_dir() and not os.environ.get("XTB_BIN"):
        os.environ["XTB_BIN"] = str(archived / "usr/bin/xtb")
        xtb._BUNDLED_XTB_ROOT = archived
    crest = importlib.import_module(name + ".crest")
    crest_root = source.parent / "nanoplastic-modeling-studio-private/modeling/tools/crest"
    if crest_root.is_dir() and not os.environ.get("CREST_BIN"):
        crest._BUNDLED_CREST = crest_root / "crest"
        crest._CONSTRAINT_COMPAT_CREST = crest_root / "versions/v2.12/crest"
    # Existing user's engine overrides always take precedence.
    return lambda sub: importlib.import_module(name + "." + sub)


def check_orca():
    binary = os.environ.get("DOCKUP_ORCA_BIN")
    if not binary:
        return {"status": "unavailable", "reason": "Set DOCKUP_ORCA_BIN to the quantum-chemistry ORCA executable; /usr/bin/orca is not used"}
    path = Path(binary).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK) or path == Path("/usr/bin/orca"):
        return {"status": "unavailable", "reason": "Not an executable quantum-chemistry ORCA path"}
    with path.open("rb") as stream:
        magic = stream.read(4)
    if magic != b"\x7fELF":
        return {"status": "unavailable", "reason": "ORCA binary must be native ELF; shell/Python wrappers are not accepted"}
    try:
        result = subprocess.run([str(path)], capture_output=True, text=True, timeout=5)
        text = result.stdout + result.stderr
        if not re.search(r"O\s+R\s+C\s+A|ORCA.*(?:VERSION|Version|quantum)|Program Version", text):
            return {"status": "unavailable", "reason": "ORCA scientific banner could not be identified"}
        match = re.search(r"(?:Program Version|Version)\s+(\d+\.\d+(?:\.\d+)?)", text)
        return {"status": "ready", "binary": str(path), "version": match.group(1) if match else "identified; version not parsed", "method": "r2SCAN-3c"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": str(exc)}


def capabilities():
    data = {"orca": check_orca(), "pyscf": {"status": "unavailable", "reason": "PySCF + dispersion extension not installed in worker environment"}}
    try:
        load = studio()
        data.update(studio={"status": "ready"}, xtb=load("xtb").get_xtb_status(), crest=load("crest").get_crest_status(), builders=load("external_engines").engine_status(), catalog=load("catalog").public_catalog())
        for key in ("xtb", "crest"):
            if data[key].get("status") == "ready":
                binary = data[key].get("binary")
                env = load("xtb")._execution_environment(load("xtb")._find_xtb())
                result = subprocess.run([binary, "--version"], env=env, capture_output=True, text=True, timeout=5)
                if result.returncode:
                    data[key].update(status="unavailable", reason="Executable/version probe failed: " + (result.stderr or result.stdout)[-600:])
                else:
                    data[key]["version_banner"] = (result.stdout + result.stderr)[-800:]
    except Exception as exc:
        data["studio"] = {"status": "unavailable", "reason": str(exc)}
    if importlib.util.find_spec("pyscf"):
        try:
            import pyscf
            import pyscf.dispersion.dftd3
            data["pyscf"] = {"status": "ready", "version": pyscf.__version__, "method": "PBE0-D3(BJ)/def2-TZVP"}
        except ImportError:
            pass
    from .homology_runtime import runtime
    python, env = runtime()
    try:
        run = subprocess.run([python, "-c", "import ost,promod3; print(promod3.__version__)"], env=env, capture_output=True, text=True, timeout=8)
        data["homology"] = {"status": "ready" if run.returncode == 0 else "unavailable", "provider": "ProMod3", "version": run.stdout.strip(), "reason": run.stderr[-600:] if run.returncode else "Template-based modeling; geometric checks are not experimental validation"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        data["homology"] = {"status": "unavailable", "reason": str(exc)}
    return data


def optimized_molecule(sdf, xyz):
    """Preserve topology and atom identity; XYZ is never used to infer bond orders."""
    import numpy as np
    from rdkit import Chem
    mol = Chem.SDMolSupplier(str(sdf), removeHs=False)[0]
    if mol is None or mol.GetNumConformers() != 1:
        raise ValueError("Source SDF lacks one valid conformer")
    lines = Path(xyz).read_text().splitlines()
    count = int(lines[0])
    rows = [r.split() for r in lines[2:] if r.strip()]
    if count != mol.GetNumAtoms() or len(rows) != count:
        raise ValueError("Optimized XYZ atom count mismatch")
    if [r[0].capitalize() for r in rows] != [a.GetSymbol() for a in mol.GetAtoms()]:
        raise ValueError("Optimized XYZ element order mismatch")
    positions = np.array([[float(v) for v in row[1:4]] for row in rows])
    if positions.shape != (count, 3) or not np.isfinite(positions).all():
        raise ValueError("Nonfinite or invalid XYZ coordinates")
    periodic = Chem.GetPeriodicTable()
    for bond in mol.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        d = float(np.linalg.norm(positions[a] - positions[b]))
        radius = periodic.GetRcovalent(mol.GetAtomWithIdx(a).GetAtomicNum()) + periodic.GetRcovalent(mol.GetAtomWithIdx(b).GetAtomicNum())
        if not .55 * radius < d < 1.8 * radius:
            raise ValueError(f"Optimized bond length abnormal: atom {a}/{b}, {d:.3f} Å")
    conf = mol.GetConformer()
    for i, xyzrow in enumerate(positions):
        conf.SetAtomPosition(i, tuple(xyzrow))
    explicit = {a.GetIdx(): a.GetChiralTag() for a in mol.GetAtoms() if a.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED}
    geometric = Chem.Mol(mol)
    Chem.AssignAtomChiralTagsFromStructure(geometric, replaceExistingTags=True)
    if any(geometric.GetAtomWithIdx(i).GetChiralTag() != tag for i, tag in explicit.items()):
        raise ValueError("Explicit stereocentre changed during optimization; review geometry before export")
    return mol


def spin_check(mol, charge, unpaired):
    electrons = sum(a.GetAtomicNum() for a in mol.GetAtoms()) - charge
    if unpaired > electrons or (electrons - unpaired) % 2:
        raise ValueError("Electron count and charge/spin parity are inconsistent")


def save_coordinates(mol, xyz):
    from rdkit import Chem
    Chem.MolToXYZFile(mol, str(xyz))


def _dft_compute(mol, request, directory):
    """Actual SP/Opt/Freq providers; all numeric energies are in Hartree."""
    from rdkit import Chem
    directory.mkdir(parents=True, exist_ok=True)
    charge = request["charge"] if request.get("charge") is not None else Chem.GetFormalCharge(mol)
    spin_check(mol, charge, request["multiplicity"] - 1)
    save_coordinates(mol, directory / "input.xyz")
    protocol = {"engine": request["engine"], "charge": charge, "multiplicity": request["multiplicity"], "solvent": request["solvent"], "calculation": request["calculation"], "threads": request["threads"], "memory_mb": request["memory_mb"]}
    if request["engine"] == "orca":
        capability = check_orca()
        if capability["status"] != "ready":
            raise RuntimeError(capability["reason"])
        job_keyword = {"optimization": "Opt", "frequency": "Freq", "singlepoint": "SP"}[request["calculation"]]
        solvent = " CPCM(Water)" if request["solvent"] == "water" else ""
        inp = f"! r2SCAN-3c TightSCF {job_keyword}{solvent}\n%pal nprocs {request['threads']} end\n%maxcore {max(64, request['memory_mb'] // request['threads'])}\n* xyzfile {charge} {request['multiplicity']} input.xyz\n"
        (directory / "calc.inp").write_text(inp)
        run = subprocess.run([capability["binary"], "calc.inp"], cwd=directory, capture_output=True, text=True, timeout=request["timeout_seconds"])
        text = run.stdout + "\n" + run.stderr
        (directory / "calc.out").write_text(text)
        energies = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", text)
        if run.returncode or "ORCA TERMINATED NORMALLY" not in text or not energies or re.search(r"SCF NOT CONVERGED|SCF DID NOT CONVERGE", text, re.I):
            raise RuntimeError("ORCA failed/nonconverged; inspect calc.out")
        if request["calculation"] == "optimization" and "THE OPTIMIZATION HAS CONVERGED" not in text:
            raise RuntimeError("ORCA geometry optimization did not converge")
        energy = float(energies[-1])
        protocol.update(method="r2SCAN-3c", solvent_model="CPCM" if solvent else "none", version=capability["version"])
        frequencies = [float(v) for v in re.findall(r"^\s*\d+:\s+(-?\d+\.\d+)\s+cm", text, re.M)]
        if request["calculation"] == "frequency" and not frequencies:
            raise RuntimeError("Frequency output missing; normal termination is not a Hessian check")
        result = {"energy_hartree": energy, "protocol": protocol, "converged": True, "frequencies_cm1": frequencies, "imaginary_modes": sum(v < -10 for v in frequencies), "minimum_check": "frequency calculated on supplied geometry" if frequencies else "not performed"}
        if request["calculation"] == "optimization":
            if not (directory / "calc.xyz").exists():
                raise RuntimeError("ORCA optimized XYZ missing")
            result["optimized_xyz"] = str(directory / "calc.xyz")
    else:
        import pyscf
        from pyscf import gto, dft as pdft
        conf = mol.GetConformer()
        atoms = [(a.GetSymbol(), tuple(conf.GetAtomPosition(a.GetIdx()))) for a in mol.GetAtoms()]
        pyscf.lib.num_threads(request["threads"])
        system = gto.M(atom=atoms, unit="Angstrom", basis="def2-TZVP", charge=charge, spin=request["multiplicity"] - 1, max_memory=request["memory_mb"], output=str(directory / "calc.out"))
        method = pdft.RKS(system, xc="pbe0-d3bj") if system.spin == 0 else pdft.UKS(system, xc="pbe0-d3bj")
        method = method.density_fit()
        if request["solvent"] == "water":
            method = method.PCM()
            method.with_solvent.eps = 78.3553
        energy = float(method.kernel())
        if not method.converged:
            raise RuntimeError("PySCF SCF did not converge")
        protocol.update(method="PBE0-D3(BJ)/def2-TZVP", solvent_model="PCM" if request["solvent"] == "water" else "none", version=pyscf.__version__, bsse_correction="none; basis/counterpoise sensitivity not performed")
        result = {"energy_hartree": energy, "protocol": protocol, "converged": True, "minimum_check": "not performed"}
    if not math.isfinite(result["energy_hartree"]):
        raise RuntimeError("Nonfinite DFT energy")
    return result


def dft(mol, request, directory):
    """Isolate C/Fortran SCF work so per-calculation deadlines are enforceable."""
    from rdkit import Chem
    directory.mkdir(parents=True, exist_ok=True)
    writer = Chem.SDWriter(str(directory / "dft_input.sdf")); writer.write(mol); writer.close()
    (directory / "dft_request.json").write_text(json.dumps(request))
    try:
        run = subprocess.run([sys.executable,"-m","docking_app.modeling.worker","--dft",str(directory)],capture_output=True,text=True,timeout=request["timeout_seconds"])
        (directory / "engine.log").write_text(run.stdout + run.stderr)
    except subprocess.TimeoutExpired as exc:
        def decoded(v):return v.decode(errors="replace") if isinstance(v,bytes) else (v or "")
        (directory / "engine.log").write_text(decoded(exc.stdout)+decoded(exc.stderr))
        raise RuntimeError(f"DFT calculation timed out after {request['timeout_seconds']} seconds") from exc
    if run.returncode:
        raise RuntimeError((run.stderr or run.stdout)[-1200:] or "DFT worker failed")
    return json.loads((directory / "dft_result.json").read_text())


def execute(directory, spec):
    from rdkit import Chem
    load = studio()
    builder, poses, xtb = load("builder"), load("pose_sets"), load("xtb")
    # The source builder requests numThreads=0 (all CPUs). Bound RDKit calls in
    # this isolated worker without changing the original application files.
    from rdkit.Chem import AllChem
    threads = int(os.environ.get("OMP_NUM_THREADS", "2"))
    original_embed = AllChem.EmbedMultipleConfs
    def bounded_embed(mol, *args, **kwargs):
        if kwargs.get("params") is not None: kwargs["params"].numThreads = threads
        else: kwargs["numThreads"] = threads
        return original_embed(mol, *args, **kwargs)
    AllChem.EmbedMultipleConfs = bounded_embed
    for method_name in ("MMFFOptimizeMoleculeConfs", "UFFOptimizeMoleculeConfs"):
        original = getattr(AllChem,method_name)
        def bounded_opt(*args, _original=original, **kwargs):
            kwargs["numThreads"] = threads
            return _original(*args, **kwargs)
        setattr(AllChem,method_name,bounded_opt)
    request = spec["payload"]
    backend = sys.modules["dockup_studio_backend"]
    source_root = Path(backend.__file__).parent
    (directory / "backend_provenance.json").write_text(json.dumps({
        "source_root":str(source_root),
        "source_files_sha256":{p.name:sha(p) for p in sorted(source_root.glob("*.py"))},
    },indent=2))
    outputs, records = [], []

    def event(stage, **data):
        row = {"time": time.time(), "stage": stage, **data}
        with (directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
        print(stage, data.get("model_id", ""), data.get("status", ""), flush=True)

    def output(path, label, **fields):
        path = Path(path)
        if any(row["file"] == str(path.relative_to(directory)) for row in outputs):
            return
        outputs.append({"file": str(path.relative_to(directory)), "sha256": sha(path), "label": label, **fields})

    def model_outputs(model):
        for ext in ("pdb", "sdf", "xyz"):
            path = builder.model_file(model["id"], "structure." + ext)
            output(path, model["title"], model_id=model["id"], geometry="original", format=ext, kind="molecule")

    def optimized_outputs(mid, xyz, prefix):
        mol = optimized_molecule(builder.model_file(mid, "structure.sdf"), xyz)
        sdf = xyz.parent / (prefix + ".sdf")
        writer = Chem.SDWriter(str(sdf)); writer.write(mol); writer.close()
        pdb = xyz.parent / (prefix + ".pdb")
        Chem.MolToPDBFile(mol, str(pdb))
        for path, ext in ((sdf, "sdf"), (pdb, "pdb"), (xyz, "xyz")):
            output(path, builder.get_model(mid)["title"] + " · " + prefix, model_id=mid, geometry=prefix, format=ext, kind="molecule")

    def build(payload):
        event("build", status="running")
        result = builder.build_model(payload)
        model_outputs(result)
        records.append({"stage": "build", "status": "succeeded", "model": result})
        event("build", model_id=result["id"], status="succeeded")
        return result

    def pose(payload):
        ps = poses.create_pose_set(**payload)
        records.append({"stage": "poses", "status": "succeeded", "pose_set": ps})
        for row in ps["poses"]:
            model_outputs(builder.get_model(row["model_id"]))
        event("poses", pose_set_id=ps["id"], status="succeeded")
        return ps

    def quantum(mid, payload):
        started = time.monotonic()
        event(payload["engine"], model_id=mid, status="running")
        model = builder.get_model(mid)
        model_outputs(model)
        sdf = builder.model_file(mid, "structure.sdf")
        mol = Chem.SDMolSupplier(str(sdf), removeHs=False)[0]
        if mol is None:
            raise ValueError("Invalid molecular input")
        charge = payload["charge"] if payload.get("charge") is not None else model["formal_charge"]
        spin_check(mol, charge, payload["uhf"] if payload["engine"] == "xtb" else payload["multiplicity"] - 1)
        qdir = directory / "quantum" / mid
        qdir.mkdir(parents=True, exist_ok=True)

        def calc(fragment, label, override_charge=None):
            path = qdir / label
            path.mkdir(exist_ok=True)
            config = dict(payload, charge=charge if override_charge is None else override_charge, calculation="singlepoint")
            if payload["engine"] != "xtb":
                return dft(fragment, config, path)
            save_coordinates(fragment, path / "input.xyz")
            spin_check(fragment, config["charge"], config["uhf"])
            command = [xtb._find_xtb(), "input.xyz", "--gfn", "2", "--chrg", str(config["charge"]), "--uhf", str(config["uhf"])]
            if not command[0]:
                raise RuntimeError("xTB unavailable")
            if config["solvent"] == "water": command.extend(["--alpb", "water"])
            run = subprocess.run(command, cwd=path, env=xtb._execution_environment(command[0]), capture_output=True, text=True, timeout=config["timeout_seconds"])
            text = run.stdout + run.stderr
            (path / "xtb.out").write_text(text)
            parsed = xtb._result_from_output(text, 0, command, config["solvent"], False)
            if run.returncode or not xtb.calculation_converged(text, False, path, parsed["energy_hartree"]):
                raise RuntimeError("xTB fragment single point failed")
            return {"energy_hartree": parsed["energy_hartree"], "converged": True, "protocol": {"method": "GFN2-xTB", "solvent_model": "ALPB" if config["solvent"] == "water" else "none", "charge": config["charge"], "uhf": config["uhf"]}}

        if payload["calculation"] == "interaction":
            source = model.get("source", {})
            split = source.get("surface_atom_count")
            if source.get("family") != "complex" or not split or payload["uhf"] or payload["multiplicity"] != 1:
                raise ValueError("Interaction cycles require a mapped closed-shell surface–adsorbate complex")
            if charge != model["formal_charge"]:
                raise ValueError("Interaction cycle charge cannot differ from mapped input fragments")
            def fragment(indices):
                editable = Chem.RWMol(mol)
                for idx in reversed([i for i in range(mol.GetNumAtoms()) if i not in indices]): editable.RemoveAtom(idx)
                part = editable.GetMol(); Chem.SanitizeMol(part)
                return part
            left, right = fragment(set(range(split))), fragment(set(range(split, mol.GetNumAtoms())))
            values = [calc(mol, "complex"), calc(left, "polymer", Chem.GetFormalCharge(left)), calc(right, "adsorbate", Chem.GetFormalCharge(right))]
            de = values[0]["energy_hartree"] - values[1]["energy_hartree"] - values[2]["energy_hartree"]
            result = {"status": "completed", "model_id": mid, "calculation": "interaction", "cycle": values, "delta_E_int_hartree": de, "delta_E_int_kcal_mol": de * 627.509474, "interpretation": "Frozen-fragment electronic/continuum interaction energy, NOT binding free energy", "fragment_geometry": "supplied complex; no fragment relaxation", "source_sdf_sha256": sha(sdf)}
        elif payload["engine"] == "crest":
            if charge!=model["formal_charge"] or payload["uhf"] or payload["multiplicity"]!=1:
                raise ValueError("CREST refinement uses the mapped input charge and closed-shell state; overrides are not supported")
            # CREST appends variants to a set. Fork the snapshot's set so the
            # immutable library set remains intact and the new set is visible.
            import uuid
            original_set = model.get("source",{}).get("pose_set_id")
            if not original_set:
                raise ValueError("CREST needs a saved mapped pose-set member")
            fork = poses.get_pose_set(original_set)
            fork["id"] = "pset_"+uuid.uuid4().hex[:24]
            fork["parent_pose_set_id"] = original_set
            if not any(p["model_id"]==mid for p in fork["poses"]):
                fork["poses"].append({"model_id":mid,"pose_id":model["source"].get("pose_id",mid),"parameters":model["source"].get("pose",{})})
            (poses.POSE_SETS_DIR / (fork["id"]+".json")).write_text(json.dumps(fork,indent=2))
            model["source"]["pose_set_id"] = fork["id"]
            (builder.RUNS_DIR / mid / "manifest.json").write_text(json.dumps(model,indent=2))
            event("crest_pose_set_fork",parent_pose_set_id=original_set,pose_set_id=fork["id"])
            result = load("crest").run_crest_refinement(mid, top_k=payload["top_k"], solvent=payload["solvent"], timeout_seconds=payload["timeout_seconds"])
            if result.get("status")=="completed" and not result.get("imported_variants"):
                result.update(status="failed",reason="CREST produced no usable coordinate variants")
            for ps in poses.list_pose_sets(100):
                for row in ps["poses"]:
                    if row["model_id"] != mid and (builder.RUNS_DIR / row["model_id"]).exists():
                        model_outputs(builder.get_model(row["model_id"]))
        elif payload["engine"] == "xtb":
            result = xtb.run_xtb(mid, solvent=payload["solvent"], optimize=payload["calculation"] == "optimization", simulation_type=payload["calculation"], charge=charge, uhf=payload["uhf"], timeout_seconds=payload["timeout_seconds"], optimization_mode=payload["optimization_mode"])
            if result.get("status") == "completed" and result.get("optimize"):
                optimized_outputs(mid, builder.RUNS_DIR / mid / "xtb/xtbopt.xyz", "xtb_optimized")
        else:
            result = dft(mol, payload, qdir)
            result.update(status="completed", model_id=mid)
            if result.get("optimized_xyz"):
                optimized_outputs(mid, Path(result["optimized_xyz"]), "dft_optimized")
        result["wall_time_seconds"] = time.monotonic() - started
        good = result.get("status") == "completed"
        records.append({"stage": payload["engine"], "status": "succeeded" if good else "failed", "model_id": mid, "result": result})
        (qdir / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False))
        output(qdir / "result.json", mid + " result", format="json", kind="diagnostic")
        for path in qdir.rglob("*.out"):
            output(path, mid + " · " + str(path.relative_to(qdir)), format="log", kind="diagnostic")
        legacy_log = builder.RUNS_DIR / mid / "xtb/xtb.out"
        if legacy_log.exists(): output(legacy_log, mid + " xTB log", format="log", kind="diagnostic")
        if not good: event(payload["engine"], model_id=mid, status="failed", reason=result.get("reason", "Engine did not succeed"))
        else: event(payload["engine"], model_id=mid, status="succeeded")
        return result

    kind = spec["kind"]
    if kind == "build": build(request)
    elif kind == "poses": pose(request)
    elif kind == "quantum":
        for mid in request["model_ids"]:
            try: quantum(mid, request)
            except Exception as exc:
                records.append({"stage": request["engine"], "status": "failed", "model_id": mid, "reason": str(exc)})
                event(request["engine"], model_id=mid, status="failed", reason=str(exc))
    elif kind == "experiment":
        from .schemas import QuantumJob
        builder_models = []
        if not request.get("surface_model_ids") and not request.get("pose_set_ids"):
            for polymer in request["polymers"]:
                for repeat in request["repeats"]:
                    builder_models.append(build({"family": "polymer", "polymer": polymer, "repeats": repeat, "conformers": request["conformers"], "seed": request["seed"], "pH": request["pH"], "model_kind": "molecule", "engine": "rdkit_etkdg"}))
        sets = [poses.get_pose_set(i) for i in request.get("pose_set_ids", [])]
        surfaces = request.get("surface_model_ids", []) + [m["id"] for m in builder_models]
        if surfaces and request.get("generate_poses",True):
            dopamine = build({"family": "small_molecule", "pH": request["pH"], "microstate": "auto", "conformers": request["conformers"], "seed": request["seed"]})
            for mid in surfaces:
                sets.append(pose({"surface_model_id": mid, "adsorbate_model_id": dopamine["id"], "site_count": request["sites"], "orientations_per_site": request["orientations"], "seed": request["seed"]}))
        if request["run_xtb"]:
            for ps in sets:
                for row in ps["poses"][:request["representatives"]]:
                    params = QuantumJob(model_ids=[row["model_id"]], engine="xtb", optimization_mode=request["optimization_mode"], solvent=request["solvent"], timeout_seconds=request["timeout_seconds"], threads=request["threads"]).model_dump()
                    try: quantum(row["model_id"], params)
                    except Exception as exc: records.append({"stage": "xtb", "status": "failed", "model_id": row["model_id"], "reason": str(exc)})
        output(directory / "request.json", request["title"] + " experiment plan", format="json", kind="diagnostic")
    elif kind == "homology":
        from .homology_runtime import runtime
        python, env = runtime()
        script = Path(__file__).with_name("homology_worker.py")
        run = subprocess.run([python, str(script), str(directory)], env=env, timeout=request["timeout_seconds"])
        if run.returncode: raise RuntimeError("ProMod3 failed; inspect worker log")
        result = json.loads((directory / "assessment.json").read_text())
        records.append({"stage": "homology", "status": "succeeded", "assessment": result})
        output(directory / "model.pdb", request["name"], format="pdb", kind="receptor", geometry="homology", assessment=result)
        output(directory / "assessment.json", "Model assessment", format="json", kind="diagnostic")
        output(directory / "alignment.json", "Target/template alignment", format="json", kind="diagnostic")
    # Provider diagnostics also remain downloadable for unsuccessful members.
    for path in (directory / "quantum").rglob("*"):
        if path.is_file() and path.suffix in {".out",".log",".json"}:
            output(path,str(path.relative_to(directory)),format="log" if path.suffix in {".out",".log"} else "json",kind="diagnostic")
    for mid in request.get("model_ids",[]):
        for sub in ("xtb","crest"):
            for path in (builder.RUNS_DIR / mid / sub).rglob("*"):
                if path.is_file() and (path.suffix in {".out",".json",".xyz"} or path.name in {"charges","crest.energies"}):
                    output(path,f"{mid} · {sub} · {path.name}",format="log" if path.suffix==".out" else "diagnostic",kind="diagnostic")
    successes = sum(r["status"] == "succeeded" for r in records)
    failures = len(records) - successes
    return {"state": "partially_succeeded" if successes and failures else ("succeeded" if successes else "failed"), "records": records, "outputs": outputs, "successes": successes, "failures": failures, "warnings": ["Electronic energies are not binding free energies", "Finite oligomers/surface proxies are not equilibrated nanoplastic particles"]}


def main():
    if sys.argv[1] == "--capabilities":
        print(json.dumps(capabilities(), allow_nan=False)); return
    if sys.argv[1] == "--dft":
        from rdkit import Chem
        directory = Path(sys.argv[2]).resolve()
        request = json.loads((directory / "dft_request.json").read_text())
        mol = Chem.SDMolSupplier(str(directory / "dft_input.sdf"),removeHs=False)[0]
        if mol is None:raise ValueError("DFT snapshot SDF invalid")
        result = _dft_compute(mol,request,directory)
        (directory / "dft_result.json").write_text(json.dumps(result,allow_nan=False))
        return
    directory = Path(sys.argv[1]).resolve()
    spec = json.loads((directory / "request.json").read_text())
    try:
        result = execute(directory, spec)
    except Exception as exc:
        traceback.print_exc()
        result = {"state": "failed", "reason": str(exc), "outputs": []}
    (directory / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__": main()
