"""Tests for Chromaprint similarity computation."""

import pytest

from musiktool.chromaprint_sim import album_similarity, bit_error_rate, decode_fingerprint


class TestDecodeFingerprint:
    def test_decodes_valid_fingerprint(self):
        # Use a known short fingerprint (from fpcalc output)
        # We can't hardcode raw values without a real fingerprint,
        # so encode/decode roundtrip using the library
        from musiktool.chromaprint_sim import _lib
        import ctypes

        # Create a minimal valid fingerprint by encoding known integers
        raw = (ctypes.c_uint32 * 4)(0xDEADBEEF, 0x12345678, 0xCAFEBABE, 0x00000000)
        encoded_ptr = ctypes.c_char_p()
        encoded_size = ctypes.c_int()
        _lib.chromaprint_encode_fingerprint.argtypes = [
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_int,
        ]
        _lib.chromaprint_encode_fingerprint.restype = ctypes.c_int
        ok = _lib.chromaprint_encode_fingerprint(
            raw, 4, 1, ctypes.byref(encoded_ptr), ctypes.byref(encoded_size), 1,
        )
        assert ok
        encoded = encoded_ptr.value.decode("ascii")
        _lib.chromaprint_dealloc(encoded_ptr)

        result = decode_fingerprint(encoded)
        assert len(result) == 4
        assert result[0] == 0xDEADBEEF
        assert result[1] == 0x12345678
        assert result[2] == 0xCAFEBABE
        assert result[3] == 0x00000000

    def test_invalid_data_raises(self):
        with pytest.raises(ValueError, match="failed"):
            decode_fingerprint("not-valid-chromaprint-data!!!")

    def test_empty_string_raises(self):
        with pytest.raises((ValueError, Exception)):
            decode_fingerprint("")


class TestBitErrorRate:
    def test_identical_is_zero(self):
        fp = [0xDEADBEEF, 0x12345678, 0xCAFEBABE]
        assert bit_error_rate(fp, fp) == 0.0

    def test_all_bits_different(self):
        fp_a = [0x00000000, 0x00000000]
        fp_b = [0xFFFFFFFF, 0xFFFFFFFF]
        assert bit_error_rate(fp_a, fp_b) == 1.0

    def test_one_bit_different(self):
        fp_a = [0x00000000]
        fp_b = [0x00000001]
        assert bit_error_rate(fp_a, fp_b) == pytest.approx(1 / 32)

    def test_half_bits_different(self):
        fp_a = [0x00000000]
        fp_b = [0x0000FFFF]  # 16 bits set
        assert bit_error_rate(fp_a, fp_b) == pytest.approx(16 / 32)

    def test_uses_shorter_length(self):
        fp_a = [0x00000000, 0x00000000, 0x00000000]
        fp_b = [0xFFFFFFFF]
        # Only compares first element
        assert bit_error_rate(fp_a, fp_b) == 1.0

    def test_empty_asserts(self):
        with pytest.raises(AssertionError):
            bit_error_rate([], [0x00000000])

    def test_symmetric(self):
        fp_a = [0xDEADBEEF, 0x12345678]
        fp_b = [0xCAFEBABE, 0x87654321]
        assert bit_error_rate(fp_a, fp_b) == bit_error_rate(fp_b, fp_a)


class TestAlbumSimilarity:
    def test_identical_albums(self):
        fps = [[0xDEADBEEF, 0x12345678], [0xCAFEBABE, 0x00000000]]
        assert album_similarity(fps, fps) == 0.0

    def test_different_track_count_returns_none(self):
        fps_a = [[0x00000000]]
        fps_b = [[0x00000000], [0xFFFFFFFF]]
        assert album_similarity(fps_a, fps_b) is None

    def test_empty_returns_none(self):
        assert album_similarity([], [[0x00000000]]) is None
        assert album_similarity([[0x00000000]], []) is None
        assert album_similarity([], []) is None

    def test_averages_track_bers(self):
        # Track 1: identical (BER=0), Track 2: all different (BER=1)
        fps_a = [[0x00000000], [0x00000000]]
        fps_b = [[0x00000000], [0xFFFFFFFF]]
        assert album_similarity(fps_a, fps_b) == pytest.approx(0.5)

    def test_none_for_empty_track_fingerprint(self):
        fps_a = [[0x00000000], []]
        fps_b = [[0x00000000], [0xFFFFFFFF]]
        assert album_similarity(fps_a, fps_b) is None
