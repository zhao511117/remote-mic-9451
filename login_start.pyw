# SPDX-License-Identifier: GPL-3.0-only
"""Hidden per-user logon entry. Invoked by the Windows Startup shortcut."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from rm9451.login_start import main
raise SystemExit(main())
