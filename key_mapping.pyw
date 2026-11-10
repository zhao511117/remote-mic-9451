# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import sys
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/'src'),str(root/'vendor')]
from rm9451.key_mapping import main
raise SystemExit(main())
