"""Compatibility entry point; implementation moved to src.preprocessing.ingest."""
import sys
from src.preprocessing import ingest as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
