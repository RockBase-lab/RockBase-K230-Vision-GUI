"""ulab package stubs (ulab on K230 is MicroPython's lightweight numpy port)."""
from typing import Any

from . import numpy as numpy
from . import scipy as scipy

__version__: str

def __getattr__(name: str) -> Any: ...
