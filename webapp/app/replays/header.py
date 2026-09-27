"""The match UUID from a `.vrf` file's header, parsed structurally (W-a).

The layout follows upstream `ReplayInfoReader.cs:47-95` (`2b66c65`):

    uint32  magic                 0x43F4EFDD
    uint32  file version          7 (LocalFileReplayCustomVersions.CustomVersions)
    int32   custom version count  then per entry: 16-byte GUID + int32 version;
                                  exactly one entry, LocalFileReplay at version 7
    int32   LengthInMs
    uint32  NetworkVersion
    uint32  Changelist
    FString FriendlyName          int32 length; negative = UTF-16LE of -length code units,
                                  positive = UTF-8 bytes; trailing NULs trimmed

The Valorant client writes the match UUID as the `FriendlyName` (padded with spaces in the
first real file, UTF-16), and again in ASCII later in the file. Every read is bounded, and
any deviation raises `HeaderError`. No fixed byte offsets beyond the structure itself.

The header is an uploader-controlled claim, like the file name: it stops accidental
mislabelling but proves nothing. Only the linker's proof ties a replay to a match.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from app.replays.contract import ContractError

FILE_MAGIC = 0x43F4EFDD
FILE_VERSION = 7
LOCAL_FILE_REPLAY_GUID = "95A4F03E-7E0B-49E4-BA43-D35694FF87D9"
LOCAL_FILE_REPLAY_VERSION = 7
MAX_CUSTOM_VERSIONS = 64
MAX_FRIENDLY_NAME_BYTES = 64 * 1024
# The header plus the longest FriendlyName: nothing past this is read for the header.
MAX_HEADER_BYTES = 12 + MAX_CUSTOM_VERSIONS * 20 + 16 + MAX_FRIENDLY_NAME_BYTES

CANONICAL_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class HeaderError(ContractError):
    def __init__(self, detail: str):
        super().__init__("vrf_header", detail)


@dataclass(frozen=True)
class Header:
    match_uuid: str               # canonical: lower-case, hyphenated
    length_ms: int
    network_version: int
    changelist: int
    friendly_name_encoding: str   # "utf-16" | "utf-8"


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int, what: str) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise HeaderError(f"truncated header: {what} needs {n} bytes at offset {self.pos}")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u32(self, what: str) -> int:
        return struct.unpack("<I", self.take(4, what))[0]

    def i32(self, what: str) -> int:
        return struct.unpack("<i", self.take(4, what))[0]

    def guid(self, what: str) -> str:
        a, b, c, d = struct.unpack("<IIII", self.take(16, what))
        return f"{a:08X}-{b >> 16:04X}-{b & 0xFFFF:04X}-{c >> 16:04X}-{c & 0xFFFF:04X}{d:08X}"


def parse_header(data: bytes) -> Header:
    r = _Reader(data)
    magic = r.u32("magic")
    if magic != FILE_MAGIC:
        raise HeaderError(f"not a replay file (magic {magic:#010x})")
    version = r.u32("file version")
    if version != FILE_VERSION:
        raise HeaderError(f"unsupported replay file version {version}, expected {FILE_VERSION}")
    count = r.i32("custom version count")
    if not 0 < count <= MAX_CUSTOM_VERSIONS:
        raise HeaderError(f"custom version count {count} out of range")
    entries = [(r.guid("custom version key"), r.i32("custom version")) for _ in range(count)]
    if entries != [(LOCAL_FILE_REPLAY_GUID, LOCAL_FILE_REPLAY_VERSION)]:
        raise HeaderError("the custom versions are not exactly LocalFileReplay at version "
                          f"{LOCAL_FILE_REPLAY_VERSION}")
    length_ms = r.i32("LengthInMs")
    network_version = r.u32("NetworkVersion")
    changelist = r.u32("Changelist")
    length = r.i32("FriendlyName length")
    if length == 0 or length == -(2 ** 31):
        raise HeaderError(f"FriendlyName length {length} is invalid")
    if length < 0:
        size, encoding, codec = -length * 2, "utf-16", "utf-16-le"
    else:
        size, encoding, codec = length, "utf-8", "utf-8"
    if size > MAX_FRIENDLY_NAME_BYTES:
        raise HeaderError(f"FriendlyName of {size} bytes is over the {MAX_FRIENDLY_NAME_BYTES}-byte bound")
    try:
        name = r.take(size, "FriendlyName").decode(codec).rstrip("\0").strip()
    except UnicodeDecodeError as error:
        raise HeaderError(f"FriendlyName is not valid {encoding}") from error
    uuid = name.lower()
    if not CANONICAL_UUID.match(uuid):
        raise HeaderError("FriendlyName is not a canonical match UUID")
    return Header(uuid, length_ms, network_version, changelist, encoding)


def read_header(path: Path) -> Header:
    with path.open("rb") as handle:
        return parse_header(handle.read(MAX_HEADER_BYTES))


def ascii_copies(path: Path, match_uuid: str) -> int:
    """How many times the header's UUID appears in ASCII in the file (any case)."""
    needle = match_uuid.lower().encode("ascii")
    count = 0
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            block = (tail + chunk).lower()
            count += block.count(needle)
            # Keep an overlap shorter than the needle, so no match is counted twice.
            tail = chunk[-(len(needle) - 1):]
    return count


def header_match_uuid(path: Path) -> tuple[str, dict]:
    """(the match UUID, evidence) from a `.vrf`: the structural header, corroborated by an
    ASCII copy. PROVISIONAL(D7): a header UUID with no ASCII copy refuses."""
    header = read_header(path)
    copies = ascii_copies(path, header.match_uuid)
    if copies == 0:
        raise HeaderError("the header's match UUID has no ASCII copy in the file")
    return header.match_uuid, {"encoding": header.friendly_name_encoding, "ascii_copies": copies,
                               "length_ms": header.length_ms}
