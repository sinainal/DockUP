from __future__ import annotations

from pathlib import Path

from docking_app.pocket_finder import config as pocket_config


def test_candidate_p2rank_paths_are_repo_local_only(monkeypatch, tmp_path: Path) -> None:
    base = tmp_path / "DockUP"
    workspace = base / "docking_app" / "workspace"
    monkeypatch.setattr(pocket_config, "BASE", base)
    monkeypatch.setattr(pocket_config, "WORKSPACE_DIR", workspace)
    monkeypatch.delenv(pocket_config.P2RANK_ENV_VAR, raising=False)

    candidates = pocket_config.candidate_p2rank_paths()

    assert candidates == [
        base / ".venv" / "bin" / "prank",
        workspace / "tools" / "p2rank" / "prank",
        workspace / "tools" / "p2rank" / "distro" / "prank",
    ]
    assert all("pocket_test" not in str(path) for path in candidates)


def test_resolve_p2rank_bin_prefers_explicit_env(monkeypatch, tmp_path: Path) -> None:
    custom_bin = tmp_path / "custom" / "prank"
    custom_bin.parent.mkdir(parents=True, exist_ok=True)
    custom_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    custom_bin.chmod(0o755)

    monkeypatch.setenv(pocket_config.P2RANK_ENV_VAR, str(custom_bin))

    assert pocket_config.resolve_p2rank_bin() == custom_bin.resolve()


def test_candidate_p2rank_java_homes_are_repo_local_only(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "DockUP" / "docking_app" / "workspace"
    monkeypatch.setattr(pocket_config, "WORKSPACE_DIR", workspace)
    monkeypatch.delenv(pocket_config.P2RANK_JAVA_ENV_VAR, raising=False)

    candidates = pocket_config.candidate_p2rank_java_homes()

    assert candidates == [workspace / "tools" / "p2rank_java"]
    assert all("pocket_test" not in str(path) for path in candidates)


def test_resolve_p2rank_java_home_prefers_explicit_env(monkeypatch, tmp_path: Path) -> None:
    custom_home = tmp_path / "custom-java"
    java_exec = custom_home / "bin" / "java"
    java_exec.parent.mkdir(parents=True, exist_ok=True)
    java_exec.write_text("#!/bin/sh\n", encoding="utf-8")
    java_exec.chmod(0o755)

    monkeypatch.setenv(pocket_config.P2RANK_JAVA_ENV_VAR, str(custom_home))

    assert pocket_config.resolve_p2rank_java_home() == custom_home.resolve()
