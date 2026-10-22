"""Prints the engine's runtime key: a SHA-256 naming its Python and every package except the
engine's own (fabric-desktop). Run by CI with the engine's own python.exe. Builds with the same key
have the same Python and packages, so the app updates an installed engine with that key from the
small update zip instead of downloading the whole engine (app/src-tauri/src/engine_patch.rs).

Also in the key: SIGNED (whether CI signs the packages' DLLs, which changes the files), and
LAYOUT, to bump by hand when the engine job changes the folder in another way (python312._pth, say).
"""

import hashlib
import importlib.metadata
import os
import sys

LAYOUT = "1"


def main() -> None:
    lines = [f"layout {LAYOUT}", f"python {sys.version}", f"signed {os.environ.get('SIGNED', '')}"]
    packages = set()
    for dist in importlib.metadata.distributions():
        name = (dist.metadata["Name"] or "").lower().replace("_", "-")
        if name != "fabric-desktop":
            packages.add(f"package {name}=={dist.version}")
    lines += sorted(packages)
    print(hashlib.sha256("\n".join(lines).encode()).hexdigest())


if __name__ == "__main__":
    main()
