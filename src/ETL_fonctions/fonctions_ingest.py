"""Compatibility entry point; implementation moved to src.preprocessing.pdf_helpers."""
import sys
from src.preprocessing import pdf_helpers as _implementation

if __name__ != "__main__":
    sys.modules[__name__] = _implementation
