"""Streaming Zstandard input without third-party Python dependencies."""
from __future__ import annotations

import codecs
import ctypes
import ctypes.util
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterator


class CompressionError(OSError):
    pass


class _Buffer(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]


@lru_cache(maxsize=1)
def _library():
    name = ctypes.util.find_library("zstd")
    if not name:
        return None
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.ZSTD_createDStream.restype = ctypes.c_void_p
    lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.restype = ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Buffer), ctypes.POINTER(_Buffer)]
    lib.ZSTD_decompressStream.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
    lib.ZSTD_getErrorName.restype = ctypes.c_char_p
    return lib


def _native_chunks(path: Path, lib) -> Iterator[bytes]:
    stream = lib.ZSTD_createDStream()
    if not stream:
        raise CompressionError("Could not allocate Zstandard decoder")
    def checked(result: int) -> int:
        if lib.ZSTD_isError(result):
            raise CompressionError(lib.ZSTD_getErrorName(result).decode("utf-8", "replace"))
        return result
    try:
        checked(lib.ZSTD_initDStream(stream))
        remaining = 1
        output = ctypes.create_string_buffer(131072)
        with path.open("rb") as source:
            while chunk := source.read(65536):
                data = ctypes.create_string_buffer(chunk)
                incoming = _Buffer(ctypes.cast(data, ctypes.c_void_p), len(chunk), 0)
                while True:
                    outgoing = _Buffer(ctypes.cast(output, ctypes.c_void_p), len(output), 0)
                    remaining = checked(lib.ZSTD_decompressStream(stream, ctypes.byref(outgoing), ctypes.byref(incoming)))
                    if outgoing.pos:
                        yield output.raw[:outgoing.pos]
                    if incoming.pos == incoming.size and outgoing.pos < outgoing.size:
                        break
        if remaining:
            raise CompressionError("Truncated Zstandard frame")
    finally:
        lib.ZSTD_freeDStream(stream)


def _chunks(path: Path) -> Iterator[bytes]:
    try:
        from compression import zstd  # Python 3.14+
    except ImportError:
        zstd = None
    if zstd is not None:
        try:
            with zstd.open(path, "rb") as stream:
                while chunk := stream.read(65536):
                    yield chunk
        except (OSError, EOFError, zstd.ZstdError) as error:
            raise CompressionError(str(error)) from error
        return
    lib = _library()
    if lib is not None:
        yield from _native_chunks(path, lib)
        return
    executable = shutil.which("zstd")
    if not executable:
        raise CompressionError("Reading .jsonl.zst requires Python 3.14+, system libzstd, or the zstd command")
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([executable, "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=errors)
        try:
            assert process.stdout is not None
            while chunk := process.stdout.read(65536):
                yield chunk
            if process.wait():
                errors.seek(0)
                raise CompressionError(errors.read(4096).decode("utf-8", "replace"))
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait()
            if process.stdout:
                process.stdout.close()


def text_lines(path: Path) -> Iterator[str]:
    if path.suffix != ".zst":
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            yield from stream
        return
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    chunks = _chunks(path)
    try:
        for chunk in chunks:
            pending += decoder.decode(chunk)
            lines = pending.split("\n")
            pending = lines.pop()
            yield from lines
        pending += decoder.decode(b"", final=True)
        if pending:
            yield pending
    finally:
        chunks.close()
