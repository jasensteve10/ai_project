"""Compatibility entry point; implementation moved to src.evaluation.preflight."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import sys
from src.evaluation import preflight as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
