"""Offline contract tests do not import Cognee, dotenv, or provider credentials."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
