"""K230 CanMV libs.Utils module stubs."""
from typing import Any, Tuple


def letterbox_pad_param(input_size, model_input_size) -> Tuple[int, int, int, int, float]:
    """Letterbox pad (top,bottom,left,right,scale) from input to model size.

    Measured on board: libs.Utils exports this but NOT ALIGN_UP (that lives in
    media.media); importing ALIGN_UP from here raises ImportError.
    """
    ...


def read_image(img_path: str) -> Tuple[Any, Any]:
    """Read an image file and return (img, img_ori)."""
    ...


def get_pd_buffer(*args: Any, **kwargs: Any) -> Any: ...
def two_side_pad_param(*args: Any, **kwargs: Any) -> Any: ...


class ScopedTiming:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def __enter__(self) -> "ScopedTiming": ...
    def __exit__(self, *args: Any, **kwargs: Any) -> None: ...


def __getattr__(name: str) -> Any: ...
