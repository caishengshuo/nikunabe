"""启动器：python main.py / pythonw main.py / galgame日语助手.exe [--check]。"""
import sys

from galtl.app import diagnostic, main

if __name__ == "__main__":
    if any(a.lower() in ("--check", "-c", "/check") for a in sys.argv[1:]):
        sys.exit(diagnostic())
    sys.exit(main())
