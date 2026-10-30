"""Smoke test that the tmp_locus_home fixture works."""

from __future__ import annotations

from pathlib import Path

from locus.core import config as config_mod


def test_fixture_redirects_default_config_dir(tmp_locus_home: Path) -> None:
    assert config_mod.DEFAULT_CONFIG_DIR == tmp_locus_home
    assert tmp_locus_home.exists()
    assert (tmp_locus_home / ".state").exists()
    assert (tmp_locus_home / ".state" / "models").exists()


def test_fixture_redirects_config_path(tmp_locus_home: Path) -> None:
    assert config_mod.DEFAULT_CONFIG_PATH == tmp_locus_home / "config.toml"
