"""K230 CanMV media.display module stubs."""
from typing import Any


# Display device constants
LT9611: int
ST7701: int
HX8399: int
NT35516: int
VIRT: int

# Layer
LAYER_VIDEO1: int
LAYER_VIDEO2: int
LAYER_OSD0: int
LAYER_OSD1: int
LAYER_OSD2: int
LAYER_OSD3: int


class Display:
    @staticmethod
    def init(*args: Any, **kwargs: Any) -> None: ...
    @staticmethod
    def deinit(*args: Any, **kwargs: Any) -> None: ...
    @staticmethod
    def show_image(*args: Any, **kwargs: Any) -> None: ...
    @staticmethod
    def bind_layer(*args: Any, **kwargs: Any) -> None: ...
    @staticmethod
    def width(*args: Any, **kwargs: Any) -> int: ...
    @staticmethod
    def height(*args: Any, **kwargs: Any) -> int: ...


def __getattr__(name: str) -> Any: ...
