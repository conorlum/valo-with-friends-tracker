"""W-a: the match UUID from a `.vrf` header, parsed structurally (synthetic files only)."""

import os
import struct
from pathlib import Path

import pytest

from app.replays import header as hd

MATCH = "00000000-0000-4000-8000-00000000abcd"
OTHER = "00000000-0000-4000-8000-00000000beef"


def guid_bytes(text: str) -> bytes:
    h = text.replace("-", "")
    a, b, c, d = (int(h[i:i + 8], 16) for i in range(0, 32, 8))
    return struct.pack("<IIII", a, b, c, d)


def make_header(name: str = MATCH.upper(), *, utf16: bool = True, magic: int = hd.FILE_MAGIC,
                version: int = hd.FILE_VERSION, customs=None, pad: int = 220, length_override=None) -> bytes:
    customs = customs if customs is not None else [(hd.LOCAL_FILE_REPLAY_GUID, hd.LOCAL_FILE_REPLAY_VERSION)]
    out = struct.pack("<IIi", magic, version, len(customs))
    for key, value in customs:
        out += guid_bytes(key) + struct.pack("<i", value)
    out += struct.pack("<iII", 830_955, 480_767_974, 5_574_446)
    text = name + " " * pad + "\0"
    if utf16:
        encoded = text.encode("utf-16-le")
        length = -(len(encoded) // 2)
    else:
        encoded = text.encode("utf-8")
        length = len(encoded)
    out += struct.pack("<i", length_override if length_override is not None else length) + encoded
    return out + struct.pack("<IqIIi", 0, 0, 1, 0, 0)


def write_vrf(tmp_path: Path, data: bytes, name: str = f"{MATCH}.vrf", ascii_copy: str | None = MATCH) -> Path:
    body = data + b"\x00" * 64
    if ascii_copy:
        body += b"\x03\x00\x00\x00" + ascii_copy.encode("ascii") + b"\x00" * 32
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_a_utf16_header_gives_the_canonical_uuid(tmp_path):
    path = write_vrf(tmp_path, make_header())
    header = hd.read_header(path)
    assert header.match_uuid == MATCH and header.friendly_name_encoding == "utf-16"
    uuid, evidence = hd.header_match_uuid(path)
    assert uuid == MATCH and evidence["ascii_copies"] == 1


def test_an_ascii_encoded_name_is_read_too(tmp_path):
    header = hd.read_header(write_vrf(tmp_path, make_header(utf16=False)))
    assert header.match_uuid == MATCH and header.friendly_name_encoding == "utf-8"


@pytest.mark.parametrize("cut", [3, 11, 40, 70, 90, 200])
def test_a_truncated_header_refuses(tmp_path, cut):
    with pytest.raises(hd.HeaderError, match="truncated"):
        hd.parse_header(make_header()[:cut])


def test_a_renamed_file_keeps_its_header_uuid(tmp_path):
    # The header, not the name, gives the UUID; condense cross-checks the name for local ingest.
    path = write_vrf(tmp_path, make_header(), name="renamed.vrf")
    assert hd.header_match_uuid(path)[0] == MATCH


def test_a_uuid_shaped_string_elsewhere_does_not_change_the_answer(tmp_path):
    data = make_header()
    path = tmp_path / f"{MATCH}.vrf"
    path.write_bytes(OTHER.encode("ascii") + b"-" + data)  # before the magic: not a replay at all
    with pytest.raises(hd.HeaderError, match="magic"):
        hd.read_header(path)
    path.write_bytes(data + (OTHER.encode("ascii") * 5) + MATCH.encode("ascii"))
    assert hd.header_match_uuid(path)[0] == MATCH


def test_a_name_that_is_not_a_uuid_refuses(tmp_path):
    with pytest.raises(hd.HeaderError, match="canonical"):
        hd.parse_header(make_header(name="Replay of my match"))
    with pytest.raises(hd.HeaderError, match="canonical"):
        hd.parse_header(make_header(name=MATCH.replace("-", "")))
    with pytest.raises(hd.HeaderError, match="canonical"):
        hd.parse_header(make_header(name="{" + MATCH + "}"))


@pytest.mark.parametrize("kwargs,match", [
    ({"magic": 0x12345678}, "magic"),
    ({"version": 6}, "file version"),
    ({"customs": []}, "out of range"),
    ({"customs": [(hd.LOCAL_FILE_REPLAY_GUID, 6)]}, "LocalFileReplay"),
    ({"customs": [(hd.LOCAL_FILE_REPLAY_GUID, 7), ("11111111-2222-3333-4444-555555555555", 1)]}, "LocalFileReplay"),
    ({"length_override": 0}, "invalid"),
    ({"length_override": -(2 ** 31)}, "invalid"),
    ({"length_override": -(hd.MAX_FRIENDLY_NAME_BYTES // 2 + 1)}, "bound"),
    ({"length_override": hd.MAX_FRIENDLY_NAME_BYTES + 1}, "bound"),
])
def test_structural_deviations_refuse(kwargs, match):
    with pytest.raises(hd.HeaderError, match=match):
        hd.parse_header(make_header(**kwargs))


def test_a_header_uuid_with_no_ascii_copy_refuses(tmp_path):
    path = write_vrf(tmp_path, make_header(), ascii_copy=None)
    assert hd.read_header(path).match_uuid == MATCH
    with pytest.raises(hd.HeaderError, match="ASCII copy"):
        hd.header_match_uuid(path)


def test_ascii_copies_are_counted_across_read_chunks(tmp_path):
    path = tmp_path / "big.vrf"
    needle = MATCH.encode("ascii")
    # One copy straddles the 1 MiB chunk boundary, one sits after it, in upper case.
    path.write_bytes(b"x" * ((1 << 20) - 10) + needle + b"y" * 50 + needle.upper())
    assert hd.ascii_copies(path, MATCH) == 2


REAL_VRF = Path(os.environ.get("USERPROFILE", "~")) / "ValorantReplayArchive" / "d45b2844-d7dd-4efd-bbf7-551854710350.vrf"


@pytest.mark.skipif(not REAL_VRF.exists(), reason="the real .vrf is only on the owner's machine")
def test_the_real_vrf_header_agrees_with_its_file_name():
    uuid, evidence = hd.header_match_uuid(REAL_VRF)
    assert uuid == REAL_VRF.stem and evidence["encoding"] == "utf-16" and evidence["ascii_copies"] > 0
