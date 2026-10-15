"""Startskript für PyInstaller und Doppelklick (python run_streamtv.py)."""

import sys

from streamtv.app import main

if __name__ == "__main__":
    sys.exit(main())
