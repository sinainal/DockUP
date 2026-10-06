"""ProMod3 environment discovery; configured runtimes are never pip-installed here."""
import os
from pathlib import Path


def runtime():
    env = os.environ.copy()
    bundle = Path(env.get("DOCKUP_PROMOD3_ROOT", str(Path(__file__).resolve().parents[2].parent / "serotonin/software/promod3_ubuntu_local")))
    if bundle.is_dir():
        usr = bundle / "usr"
        env.update(LD_LIBRARY_PATH=os.pathsep.join([str(usr / "lib/x86_64-linux-gnu"), str(usr / "lib/x86_64-linux-gnu/openmm/plugins")]),
                   PYTHONPATH=os.pathsep.join([str(usr / "lib/python3/dist-packages"), str(bundle.parent.parent / ".model_validation_env/lib/python3.12/site-packages")]),
                   OST_ROOT=str(usr), OST_COMPOUNDS_CHEMLIB=str(bundle / "var/cache/openstructure/compounds.chemlib"),
                   PROMOD3_SHARED_DATA_PATH=str(usr / "share/promod3"), OPENMM_PLUGIN_DIR=str(usr / "lib/x86_64-linux-gnu/openmm/plugins"),
                   DOCKUP_PROMOD3_LOADER=str(bundle / "libplugin_loader.so"))
    env["PM3_OPENMM_CPU_THREADS"] = env.get("OMP_NUM_THREADS", "2")
    return env.get("DOCKUP_HOMOLOGY_PYTHON", "/usr/bin/python3"), env
