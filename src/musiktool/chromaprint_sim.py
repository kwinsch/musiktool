"""Chromaprint similarity via bit error rate (hamming distance).

Uses libchromaprint directly via ctypes — no extra pip dependency.
libchromaprint is already required on the system for fpcalc.
"""

from __future__ import annotations

import ctypes
import ctypes.util

_lib_path = ctypes.util.find_library("chromaprint")
if _lib_path is None:
    raise ImportError(
        "libchromaprint not found. Install chromaprint (e.g. pacman -S chromaprint)"
    )
_lib = ctypes.cdll.LoadLibrary(_lib_path)

_lib.chromaprint_decode_fingerprint.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
]
_lib.chromaprint_decode_fingerprint.restype = ctypes.c_int
_lib.chromaprint_dealloc.argtypes = [ctypes.c_void_p]
_lib.chromaprint_dealloc.restype = None


def decode_fingerprint(data: str) -> list[int]:
    """Decode compressed base64 Chromaprint string to raw uint32 list.

    Raises ValueError if decoding fails.
    """
    encoded = data.encode("ascii")
    result_ptr = ctypes.POINTER(ctypes.c_uint32)()
    result_size = ctypes.c_int()
    algorithm = ctypes.c_int()
    ok = _lib.chromaprint_decode_fingerprint(
        encoded,
        len(encoded),
        ctypes.byref(result_ptr),
        ctypes.byref(result_size),
        ctypes.byref(algorithm),
        1,  # base64=True
    )
    if not ok:
        raise ValueError("chromaprint_decode_fingerprint failed")
    try:
        return [result_ptr[i] for i in range(result_size.value)]
    finally:
        _lib.chromaprint_dealloc(result_ptr)


def bit_error_rate(fp_a: list[int], fp_b: list[int]) -> float:
    """Compute BER between two decoded fingerprints.

    Compares the overlapping portion (min length).
    Returns fraction of differing bits (0.0 = identical, 0.5 = uncorrelated).
    """
    length = min(len(fp_a), len(fp_b))
    assert length > 0, "fingerprints must not be empty"
    diff_bits = 0
    for i in range(length):
        xor = (fp_a[i] ^ fp_b[i]) & 0xFFFFFFFF
        diff_bits += bin(xor).count("1")
    return diff_bits / (length * 32)


def album_similarity(
    fps_a: list[list[int]],
    fps_b: list[list[int]],
) -> float | None:
    """Average BER across matched track pairs (by position).

    Returns None if track counts differ or either list is empty.
    """
    if not fps_a or not fps_b:
        return None
    if len(fps_a) != len(fps_b):
        return None
    total_ber = 0.0
    for a, b in zip(fps_a, fps_b):
        if not a or not b:
            return None
        total_ber += bit_error_rate(a, b)
    return total_ber / len(fps_a)
