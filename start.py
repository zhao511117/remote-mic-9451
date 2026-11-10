# SPDX-License-Identifier: GPL-3.0-only
"""Stable pythonw entry; no terminal, mouse control, or focus changes."""
from pathlib import Path
import sys,traceback

root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'src'))
try:
    from rm9451.__main__ import main
    raise SystemExit(main())
except Exception:
    from rm9451.config import evidence_dir
    folder=evidence_dir();folder.mkdir(parents=True,exist_ok=True)
    with (folder/'startup-error.log').open('a',encoding='utf-8') as stream:
        traceback.print_exc(file=stream)
    raise
