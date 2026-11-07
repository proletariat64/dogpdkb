"""Bounded full decoding shared by provenance and OCR reuse verification."""

import io
import warnings

from PIL import Image

from .io import InputError

MAX_PREPARED_PIXELS = 20_000_000


def is_prepared_jpeg(data: bytes) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG":
                    return False
                if image.width * image.height > MAX_PREPARED_PIXELS:
                    raise InputError("OCR_PREPARED_IMAGE_BUDGET_EXCEEDED")
                image.load()
        return True
    except InputError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InputError("OCR_PREPARED_IMAGE_BUDGET_EXCEEDED") from exc
    except (OSError, ValueError, SyntaxError):
        return False
