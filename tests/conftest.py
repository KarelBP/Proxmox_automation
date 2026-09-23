"""
Makes the project root (one level up from tests/) importable as plain
modules - config.py, proxy.py and main.py are standalone scripts deployed
into /opt/pdm-api-proxy, not a package. Hence the "add parent dir to
sys.path" pattern rather than restructuring the deployment into a proper
Python package just to make imports nicer for tests.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
