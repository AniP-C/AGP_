#!/usr/bin/env python
"""Zero-install entry point: `python run.py phase1 --input cl9ch11`.

Adds ``src/`` to sys.path so the package runs without `pip install -e .`.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))

from agp_extract.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
