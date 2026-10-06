from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: Literal["polymer", "small_molecule"] = "polymer"
    polymer: Literal["PE", "PP", "PET", "PS"] = "PE"
    repeats: int = Field(3, ge=1, le=18)
    model_kind: Literal["molecule", "surface_patch"] = "molecule"
    engine: Literal["rdkit_etkdg", "psp", "polyply"] = "rdkit_etkdg"
    conformers: int = Field(6, ge=1, le=32)
    seed: int = Field(20261006, ge=0, le=2147483647)
    pH: float = Field(7, ge=0, le=14)
    temperature_K: float = Field(310.15, ge=200, le=500)
    microstate: Literal["auto", "DAH_plus", "DA_neutral", "DA_zwitterion", "DA_anion"] = "auto"
    smiles: str | None = Field(None, max_length=4000)
    chembl_id: str | None = Field(None, pattern=r"^CHEMBL\d+$")
    name: str | None = Field(None, max_length=160)

    @model_validator(mode="after")
    def consistent(self):
        if self.smiles and self.chembl_id:
            raise ValueError("Provide SMILES or ChEMBL, not both")
        if self.family == "small_molecule" and self.model_kind != "molecule":
            raise ValueError("Surface proxies require a polymer")
        if self.family == "polymer" and self.polymer != "PE" and self.repeats > 15:
            raise ValueError("This Studio implementation supports at most 15 units for PP/PET/PS")
        return self


class PoseBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface_model_id: str = Field(pattern=r"^mdl_[A-Za-z0-9_]+$")
    adsorbate_model_id: str = Field(pattern=r"^mdl_[A-Za-z0-9_]+$")
    site_count: int = Field(5, ge=1, le=25)
    orientations_per_site: int = Field(4, ge=1, le=8)
    distance_A: float = Field(3.2, ge=2.5, le=8)
    probe_radius_A: float = Field(1.4, ge=0, le=3)
    seed: int = Field(20261006, ge=0, le=2147483647)
    strategy: Literal["surface_scan", "systematic", "crest_assisted", "manual_seed"] = "surface_scan"

    @model_validator(mode="after")
    def bounded(self):
        if self.site_count * self.orientations_per_site > 100:
            raise ValueError("At most 100 poses per set")
        return self


class QuantumJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ids: list[str] = Field(min_length=1, max_length=100)
    engine: Literal["xtb", "orca", "pyscf", "crest"] = "xtb"
    calculation: Literal["optimization", "singlepoint", "frequency", "interaction"] = "optimization"
    solvent: Literal["water", "none"] = "water"
    charge: int | None = Field(None, ge=-20, le=20)
    uhf: int = Field(0, ge=0, le=20)
    multiplicity: int = Field(1, ge=1, le=21)
    optimization_mode: Literal["full", "surface_fixed"] = "full"
    timeout_seconds: int = Field(300, ge=10, le=7200)
    threads: int = Field(2, ge=1, le=16)
    memory_mb: int = Field(2048, ge=256, le=32768)
    top_k: int = Field(3, ge=1, le=10)

    @model_validator(mode="after")
    def supported(self):
        if any(not __import__("re").fullmatch(r"mdl_[A-Za-z0-9_]+", i) for i in self.model_ids):
            raise ValueError("Invalid model ID")
        if self.engine in {"orca", "pyscf"} and self.optimization_mode != "full":
            raise ValueError("DFT surface constraints are not supported; choose full")
        if self.engine in {"xtb", "crest"} and self.calculation == "frequency":
            raise ValueError("Frequency is supported by the ORCA provider only")
        if self.engine == "pyscf" and self.calculation not in {"singlepoint", "interaction"}:
            raise ValueError("PySCF provider currently supports SP and frozen-fragment interaction")
        if self.engine == "crest" and self.calculation != "optimization":
            raise ValueError("CREST supports constrained pose refinement")
        if len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError("Duplicate model IDs")
        return self


class ChainTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain: str = Field(pattern=r"^[A-Za-z0-9]$")
    template_chain: str = Field(pattern=r"^[A-Za-z0-9]$")
    sequence: str = Field(min_length=3, max_length=2000)
    subtype: str = Field("target", max_length=32)
    target_start: int = Field(1, ge=1, le=10000)
    aligned_target: str | None = None
    aligned_template: str | None = None


class HomologyBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(pattern=r"^tpl_[a-f0-9]{24}$")
    name: str = Field("Homology model", min_length=1, max_length=120)
    targets: list[ChainTarget] = Field(min_length=1, max_length=12)
    timeout_seconds: int = Field(300, ge=10, le=1800)
    threads: int = Field(2, ge=1, le=8)
    refine: bool = True

    @model_validator(mode="after")
    def unique(self):
        if len({r.chain for r in self.targets}) != len(self.targets):
            raise ValueError("Output chain IDs must be unique")
        for row in self.targets:
            row.sequence = "".join(row.sequence.split()).upper()
            if not __import__("re").fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", row.sequence):
                raise ValueError("Target must contain standard amino-acid sequence, without FASTA headers")
            if bool(row.aligned_target) != bool(row.aligned_template):
                raise ValueError("Both aligned sequences must be provided together")
        return self


class ExperimentBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field("Polymer screen", min_length=1, max_length=120)
    polymers: list[Literal["PE", "PET", "PP", "PS"]] = Field(default_factory=lambda: ["PE", "PET", "PP", "PS"], min_length=1, max_length=4)
    repeats: list[int] = Field(default_factory=lambda: [3], min_length=1, max_length=6)
    conformers: int = Field(6, ge=1, le=32)
    seed: int = Field(20261006, ge=0, le=2147483647)
    pH: float = Field(7, ge=0, le=14)
    sites: int = Field(2, ge=1, le=10)
    orientations: int = Field(2, ge=1, le=8)
    representatives: int = Field(2, ge=1, le=100)
    run_xtb: bool = False
    generate_poses: bool = True
    timeout_seconds: int = Field(300, ge=10, le=1800)
    threads: int = Field(2, ge=1, le=8)
    surface_model_ids: list[str] = Field(default_factory=list, max_length=24)
    pose_set_ids: list[str] = Field(default_factory=list, max_length=24)
    optimization_mode: Literal["surface_fixed", "full"] = "surface_fixed"
    solvent: Literal["water", "none"] = "water"

    @model_validator(mode="after")
    def bounded(self):
        if self.run_xtb and not self.generate_poses and not self.pose_set_ids:
            raise ValueError("Model-only experiments do not include xTB; calculate the resulting models from Calculate")
        if any(n < 1 or n > 15 for n in self.repeats):
            raise ValueError("Experiment sizes must be 1–15")
        if self.sites * self.orientations > 100:
            raise ValueError("At most 100 poses per set")
        if len(self.polymers) * len(self.repeats) > 24:
            raise ValueError("At most 24 compositions")
        for i in self.surface_model_ids:
            if not __import__("re").fullmatch(r"mdl_[A-Za-z0-9_]+", i):
                raise ValueError("Invalid saved surface ID")
        for i in self.pose_set_ids:
            if not __import__("re").fullmatch(r"pset_[A-Za-z0-9_]+", i):
                raise ValueError("Invalid pose-set ID")
        return self
