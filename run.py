# -*- coding: utf-8 -*-
"""工作台命令入口，用法见 jys/cli.py 顶部说明。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jys.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
