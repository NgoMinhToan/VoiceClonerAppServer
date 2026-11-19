import os
import sys
from pathlib import Path

# Pin the management key for the whole suite before any test imports `main`.
# Assign (not setdefault) so a MANAGEMENT_KEY exported on the host machine
# cannot leak into the tests.
os.environ["MANAGEMENT_KEY"] = "test_management_key"

# Ensure the parent directory of VoiceClonerAppServer is on sys.path
# so that imports like `from VoiceClonerAppServer...` resolve whether
# tests are run from the monorepo root or directly within VoiceClonerAppServer.
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))
