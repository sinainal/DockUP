from __future__ import annotations

from pathlib import Path

from scripts.agent_tests.suite import build_hard10_cases, build_hard30_cases, prepare_seed_bundle


def test_hard30_suite_extends_hard10() -> None:
    hard10_ids = [case.case_id for case in build_hard10_cases()]
    hard30_ids = [case.case_id for case in build_hard30_cases()]

    assert len(hard10_ids) == 10
    assert len(hard30_ids) == 30
    assert hard30_ids[:10] == hard10_ids
    assert len(set(hard30_ids)) == 30


def test_seed_bundle_provides_three_ligands(tmp_path: Path) -> None:
    bundle = prepare_seed_bundle(tmp_path)

    assert bundle.ligand_name in bundle.ligand_names
    assert len(bundle.ligand_names) == 3
    assert set(bundle.ligand_paths.keys()) == {"primary", "secondary", "tertiary"}
    assert bundle.ligand_paths["primary"].exists()
    assert bundle.ligand_paths["secondary"].exists()
    assert bundle.ligand_paths["tertiary"].exists()
