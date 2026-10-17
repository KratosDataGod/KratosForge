"""Starts kernel_host for tests, with lakehouse files read from a folder on disk instead of
OneLake (the stand-in test_prove uses in the engine's own process)."""

import shutil
import sys
from pathlib import Path

from fabric_desktop import kernel_host, lakehouse

ROOT = Path(sys.argv[1])


class FolderFiles:
    def __init__(self, token):
        pass

    def download(self, ws, lh, rel, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dest)
        return dest


lakehouse.FileStore = FolderFiles
kernel_host.main()
