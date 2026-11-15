"""Keep assets and saved user state at the project root."""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
