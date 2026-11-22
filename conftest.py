"""Pytest root configuration.

Sets basetemp to a project-relative directory so pytest's tmp_path fixture can
create per-test temporary directories even when the Windows system temp folder
is inaccessible. This is test-runner infrastructure only — it changes where
temp files are stored, never any test assertion or behavior.
"""
from __future__ import annotations

import pathlib


def pytest_configure(config):
    config.option.basetemp = str(pathlib.Path(".pytest_basetemp").resolve())
