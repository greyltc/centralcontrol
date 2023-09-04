#!/usr/bin/env python3

from tomli import load
from tempfile import TemporaryDirectory
from pathlib import Path
from pyproject_hooks import BuildBackendHookCaller
from installer.__main__ import _main as installer_cli
import sys

if sys.prefix == sys.base_prefix:
    print("Error: Virtual environment not activated.")
    sys.exit(-1)

dist_dir_name = "dist"

Path(dist_dir_name).mkdir(exist_ok=True)

with open("pyproject.toml", "rb") as ppjt:
    data = load(ppjt)

some_hooks = BuildBackendHookCaller(".", data["build-system"]["build-backend"])

with TemporaryDirectory() as bldd:
    ewhl_file = some_hooks.build_editable(bldd)
    installer_cli([str(Path(bldd) / ewhl_file)])

sys.exit(0)
