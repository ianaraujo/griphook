"""PyInstaller entry point for the Windows build."""

import multiprocessing

from griphook.main import app

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app()
