"""Encoding estimates only, independent of provider policy or clinical approval."""

from dataclasses import dataclass

GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./"
    "0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXTENSION = set("\f^{}\\[~]|€")


@dataclass(frozen=True)
class SegmentEstimate:
    encoding: str
    units: int
    segments: int


def estimate_segments(text: str) -> SegmentEstimate:
    if not isinstance(text, str) or not text:
        raise ValueError("Nonempty text is required")
    if all(char in GSM_BASIC | GSM_EXTENSION for char in text):
        widths = [2 if char in GSM_EXTENSION else 1 for char in text]
        encoding, single, multipart = "GSM-7", 160, 153
    else:
        # UTF-16 code units, including indivisible surrogate pairs for non-BMP characters.
        widths = [len(char.encode("utf-16-be")) // 2 for char in text]
        encoding, single, multipart = "Unicode", 70, 67
    units = sum(widths)
    if units <= single:
        return SegmentEstimate(encoding, units, 1)
    # Never split a GSM escape pair or Unicode surrogate pair across segments.
    segments, used = 1, 0
    for width in widths:
        if used + width > multipart:
            segments += 1
            used = 0
        used += width
    return SegmentEstimate(encoding, units, segments)
