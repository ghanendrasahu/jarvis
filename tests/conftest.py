import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("WORKSPACE_ROOT", str(ROOT))
os.environ.setdefault("DATABASE_PATH", str(ROOT / "data" / "test_jarvis.db"))

