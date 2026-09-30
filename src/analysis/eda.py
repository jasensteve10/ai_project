"""Compatibility entry point; implementation moved to src.eda.pipeline."""
import sys
from src.eda import pipeline as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
