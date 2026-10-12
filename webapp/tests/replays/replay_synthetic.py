"""A synthetic parser export (schema 8) for the replay tests.

Every identity here is synthetic: Subjects are `00000000-0000-4000-8000-0000000000NN`, the
match is `00000000-0000-4000-8000-00000000abcd`. Two row shapes:

- `shape="legacy"`: what the parser source (upstream `2b66c65`) suggested: BombPlayerState
  with Subjects, BombGameState with Phase/MatchID/RoundResults, a pawn per round;
- `shape="swiftplay"`: what the first real export (Swiftplay, 13.06) had: one pawn per player
  for the whole match, found by its `Default__<Code>_PC_C` archetype and pointing at an
  undecoded player state through `PlayerState`; phases as `ClientGamePhaseBegin` RPCs; no
  Subjects, MatchID, RoundResults or map path in the export (the map is in the `.vrf`).

Two teams of five spawn in two clusters on Ascent, walk toward mid at `hz` samples per
second, and kill each other on a fixed script. Tests edit `SyntheticMatch` fields or the
row lists before `write()` to build each case.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

PIN_COMMIT = "2b66c65a7b116154e18ebb84d9f6795f2b080233"
BUILD = "++Ares-Core+release-13.06"
MATCH_UUID = "00000000-0000-4000-8000-00000000abcd"
AGENT_CODES = ["Wushu", "Sarge", "Thorne", "Hunter", "Grenadier", "Vampire", "Wraith", "Clay", "Gumshoe", "Rift"]
AGENT_NAMES = ["Jett", "Brimstone", "Sage", "Sova", "KAY/O", "Reyna", "Omen", "Raze", "Cypher", "Astra"]
SPAWNS = [(-2000.0, 500.0), (6000.0, -1000.0)]  # world (x, y) centre per team
ROUND_MS = 40_000
BUY_MS = 30_000
GAP_MS = 10_000


def vrf_header(friendly_name: str) -> bytes:
    """The `.vrf` header upstream's ReplayInfoReader parses: magic, file version 7, the
    LocalFileReplay custom version, three ints, a UTF-16 FriendlyName padded with spaces."""
    out = struct.pack("<IIi", 0x43F4EFDD, 7, 1)
    out += struct.pack("<IIII", 0x95A4F03E, 0x7E0B49E4, 0xBA43D356, 0x94FF87D9) + struct.pack("<i", 7)
    out += struct.pack("<iII", 1, 1, 1)
    name = (friendly_name + " " * 32 + "\0").encode("utf-16-le")
    out += struct.pack("<i", -(len(name) // 2)) + name
    return out + struct.pack("<IqIIi", 0, 0, 1, 0, 0)


def _intpacked(value: int) -> list[int]:
    """Unreal's packed int as bytes: 7 value bits per byte above a continue bit (bit 0)."""
    out = []
    while True:
        byte = (value & 0x7F) << 1
        value >>= 7
        out.append(byte | (1 if value else 0))
        if not value:
            return out


def life_change_events(sections: list[tuple[int, float, float, int | None]]) -> dict:
    """A damage notify's `LifeChangeEvents` ({BitCount, Data}) holding `sections`, each (component guid,
    LifeResult, DeltaLife, bAliveAfterChange | None), the way the real export packs them (W3 EVIDENCE B):
    bits LSB-first; an intpacked count, then per element an intpacked 1-based index and its fields as
    (intpacked handle, intpacked size, size bits), handle 0 ending the element. Handles: 11 the component (an
    intpacked guid), 12 the result (f32), 13 the delta (f32), 14 the alive bit."""
    bits: list[int] = []

    def put(value: int, size: int) -> None:
        bits.extend((value >> i) & 1 for i in range(size))

    def packed(value: int) -> None:
        for byte in _intpacked(value):
            put(byte, 8)

    packed(len(sections))
    for index, (guid, result, delta, alive) in enumerate(sections, 1):
        packed(index)
        guid_bytes = _intpacked(guid)
        packed(11)
        packed(8 * len(guid_bytes))
        for byte in guid_bytes:
            put(byte, 8)
        for handle, value in ((12, result), (13, delta)):
            packed(handle)
            packed(32)
            put(struct.unpack("<I", struct.pack("<f", value))[0], 32)
        if alive is not None:
            packed(14)
            packed(1)
            put(alive, 1)
        packed(0)
    put(0, 8)
    data = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        data[i >> 3] |= bit << (i & 7)
    return {"BitCount": len(bits), "Data": base64.b64encode(bytes(data)).decode("ascii"), "TypeName": "LifeChangeEvents"}


def damage_notify(t_ms: int, victim_pawn: int, by_pawn: int, taken: float, sections, *, index: int, respawn: int = 0,
                  lethal: bool = False, point: bool = True, killed_key: bool = True) -> dict:
    """A real-shaped damage notify on a player's pawn (the fields the condenser reads; the rest of the real
    payload is omitted). `killed_key=False` leaves `DamageKilledTarget` out of the row."""
    payload = {"AliveAfterDamage": not lethal, "Character": victim_pawn, "DamageTaken": taken, "DamageDealt": taken,
               "EventInstigatorPawn": by_pawn, "LifeChangeEventIndex": index, "VictimRespawnNumber": respawn,
               "LifeChangeEvents": life_change_events(sections), "DamageType": 4263}
    if killed_key:
        payload["DamageKilledTarget"] = lethal
    name = "MulticastNotifyDamage_Point" if point else "MulticastNotifyDamage_Base"
    return {"type": "rpc_received", "time_ms": t_ms, "packet_id": 30, "actor_net_guid": victim_pawn,
            "object_net_guid": victim_pawn + 52, "channel": 70, "class_path": "/Script/ShooterGame.DamageableComponent",
            "function_name": name, "payload": payload}


def bomb_state(t_ms: int, state: int) -> dict:
    """The game state's replicated `BombState` (1 spawned, 2 dropped, 3 carried, 4 planted, 5 detonated, 6 defused)."""
    return {"type": "export_group_received", "time_ms": t_ms, "packet_id": 31, "actor_net_guid": 50,
            "object_net_guid": 50, "channel": 2, "export_group_path": "/Game/GameModes/Bomb/BombGameState.BombGameState_C",
            "payload": {"BombState": state}}


def player_state_events(match: "SyntheticMatch") -> list[dict]:
    """Round 1's health and spike records for a swiftplay-shaped match (pawns 1000 + slot): slot 5 (killed by
    slot 0 at 10.0 s) buys Light armor, is hit at 5.0 s (HP 55, shield 0) and dies at 10.0 s; its HP section is
    guid 1075. Slot 1 falls at 3.25 s with no armor, its HP section unproven (never killed). The spike: carried in
    the buy phase, dropped at 15.0 s, picked up at 18.0 s, planted at 30.0 s by slot 6 (the TimedBomb's
    Instigator), defused at 39.0 s. A damage notify without `DamageKilledTarget` (on slot 2) must survive the
    streaming loader's needles."""
    start = match.round_start(1)
    pawn = 1000
    return [
        {"type": "actor_spawned", "time_ms": start - 20_000, "actor_net_guid": 800, "channel": 90,
         "archetype_path": "Default__LightArmorItem_C", "location": {"x": 0.0, "y": 0.0, "z": 0.0}},
        damage_notify(start + 5_000, pawn + 5, pawn + 0, 70.0, [(1077, 0.0, 0.0, 1), (802, 0.0, -25.0, 1),
                                                                 (1075, 55.0, -45.0, 1)], index=1),
        damage_notify(start + 10_000, pawn + 5, pawn + 0, 55.0, [(1077, 0.0, 0.0, 1), (802, 0.0, 0.0, 1),
                                                                  (1075, 0.0, -55.0, 0)], index=2, lethal=True),
        damage_notify(start + 3_250, pawn + 1, pawn + 1, 15.1, [(1027, 84.9, -15.1, 1)], index=1, point=False),
        damage_notify(start + 4_500, pawn + 2, pawn + 7, 12.0, [(1037, 88.0, -12.0, 1)], index=1, killed_key=False),
        bomb_state(start - 25_000, 1), bomb_state(start - 20_000, 3), bomb_state(start + 15_000, 2),
        bomb_state(start + 18_000, 3), bomb_state(start + 30_000, 4), bomb_state(start + 39_000, 6),
        {"type": "actor_spawned", "time_ms": start + 29_990, "actor_net_guid": 900, "channel": 91,
         "archetype_path": "Default__TimedBomb_C", "location": {"x": 1000.0, "y": 2000.0, "z": 0.0},
         "rotation": {"pitch": 0, "yaw": 0, "roll": 0}},
        {"type": "export_group_received", "time_ms": start + 29_990, "actor_net_guid": 900, "channel": 91,
         "is_actor": True, "export_group_path": "/Game/Gear/TimedBomb.TimedBomb_C", "payload": {"Instigator": pawn + 6}},
    ]


def subject(i: int) -> str:
    return f"00000000-0000-4000-8000-{i:012d}"


def team_of(slot: int) -> int:
    return 0 if slot < 5 else 1


# Default kill script per round: (seconds after InRound, killer slot, victim slot).
DEFAULT_KILLS = {
    1: [(10.0, 0, 5), (12.0, 1, 6), (15.0, 2, 7), (20.0, 3, 8), (25.0, 4, 9)],
    2: [(8.0, 5, 0), (9.0, 6, 1), (18.0, 0, 5), (22.0, 7, 2), (30.0, 8, 3), (31.0, 9, 4)],
    3: [(5.0, 2, 9), (6.0, 9, 2), (6.05, 8, 3), (20.0, 4, 8)],
}
# Winner per round as the parser decodes it (Red/Blue FName), team 0 = Red.
DEFAULT_WINNERS = {1: "Red", 2: "Blue", 3: "Red"}


@dataclass
class SyntheticMatch:
    hz: int = 16
    rounds: int = 3
    kills: dict[int, list[tuple[float, int, int]]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_KILLS.items()})
    winners: dict[int, str] = field(default_factory=lambda: dict(DEFAULT_WINNERS))
    map_code: str = "Ascent"
    agent_codes: list[str] = field(default_factory=lambda: list(AGENT_CODES))
    manifest_extra: dict = field(default_factory=dict)
    drop_final_round_end: bool = False
    extra_events: list[dict] = field(default_factory=list)
    vrf_bytes: bytes = b"synthetic replay bytes"
    shape: str = "legacy"
    leaver: tuple[int, int] | None = None   # (slot, ms): that player's pawn closes then (swiftplay shape)

    def __post_init__(self):
        if self.vrf_bytes == b"synthetic replay bytes":
            # A structurally valid header (W-a) naming the match, then its ASCII copy and (for
            # the Swiftplay shape, whose export names no map) the map path.
            body = MATCH_UUID.encode("ascii") + b" tail"
            if self.shape == "swiftplay":
                body = f"/Game/Maps/{self.map_code}/{self.map_code} ".encode("ascii") + body
            self.vrf_bytes = vrf_header(MATCH_UUID.upper()) + b"synthetic replay bytes " + body

    def round_start(self, n: int) -> int:
        return 60_000 + (n - 1) * (BUY_MS + ROUND_MS + GAP_MS) + BUY_MS

    def pawn(self, n: int, slot: int) -> int:
        if self.shape == "swiftplay":
            return 1000 + slot
        return 1000 + n * 20 + slot

    def gone_ms(self, slot: int) -> int | None:
        return self.leaver[1] if self.leaver and self.leaver[0] == slot else None

    def spawn_position(self, n: int, slot: int) -> tuple[float, float]:
        # Teams swap sides after half (round 3 here stands in for the second half).
        team = team_of(slot) if n < 3 else 1 - team_of(slot)
        cx, cy = SPAWNS[team]
        angle = (slot % 5) * 2 * math.pi / 5
        return cx + 300 * math.cos(angle), cy + 300 * math.sin(angle)

    def events(self) -> list[dict]:
        if self.shape == "swiftplay":
            return self._swiftplay_events()
        rows: list[dict] = []
        add = rows.append
        add({"type": "actor_spawned", "time_ms": 0, "packet_id": 1, "actor_net_guid": 5, "channel": 1,
             "is_dynamic": False, "actor_path": f"/Game/Maps/{self.map_code}/{self.map_code}.{self.map_code}:PersistentLevel.Door_1",
             "archetype_path": None})
        add({"type": "export_group_received", "time_ms": 100, "packet_id": 2, "actor_net_guid": 50,
             "object_net_guid": 50, "channel": 2, "export_group_path": "/Game/GameModes/Bomb/BombGameState.BombGameState_C",
             "payload": {"MatchID": MATCH_UUID.upper(), "Phase": 0, "RoundNumber": 0}})
        for slot in range(10):
            add({"type": "export_group_received", "time_ms": 200 + slot, "packet_id": 3,
                 "actor_net_guid": 100 + slot, "object_net_guid": 100 + slot, "channel": 3,
                 "export_group_path": "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C",
                 "payload": {"Subject": subject(slot), "CompetitiveTier": 0}})
        for n in range(1, self.rounds + 1):
            start = self.round_start(n)
            buy = start - BUY_MS
            add(self._phase_state(buy, 3, n - 1))
            for slot in range(10):
                pawn = self.pawn(n, slot)
                add({"type": "actor_spawned", "time_ms": buy + 10, "packet_id": 4, "actor_net_guid": pawn,
                     "channel": 10 + slot, "is_dynamic": True, "actor_path": None,
                     "archetype_path": f"/Game/Characters/{self.agent_codes[slot]}/{self.agent_codes[slot]}_PC.Default__{self.agent_codes[slot]}_PC_C",
                     "location": dict(zip("xyz", (*self.spawn_position(n, slot), 100.0)))})
                add({"type": "export_group_received", "time_ms": buy + 20, "packet_id": 5,
                     "actor_net_guid": 100 + slot, "object_net_guid": 100 + slot, "channel": 3,
                     "export_group_path": "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C",
                     "payload": {"SpawnedCharacter": pawn, "PossessedCharacter": pawn}})
            add(self._phase_state(start, 4, n - 1))
            add({"type": "rpc_received", "time_ms": start, "packet_id": 6, "actor_net_guid": 50,
                 "object_net_guid": 50, "channel": 2, "function_name": "MulticastSetPhase",
                 "payload": {"NewPhase": 4}})
            for t, killer, victim in self.kills.get(n, []):
                at = start + int(round(t * 1000))
                add({"type": "rpc_received", "time_ms": at, "packet_id": 7, "actor_net_guid": self.pawn(n, killer),
                     "object_net_guid": self.pawn(n, killer), "channel": 10 + killer,
                     "function_name": "MulticastNotifyKilledEnemy",
                     "payload": {"KillerCharacter": self.pawn(n, killer), "KilledCharacter": self.pawn(n, victim),
                                 "MultikillLevel": 1}})
            if n == self.rounds and self.drop_final_round_end:
                continue
            end = start + ROUND_MS
            add(self._phase_state(end, 5, n - 1))
            add({"type": "export_group_received", "time_ms": end, "packet_id": 8, "actor_net_guid": 50,
                 "object_net_guid": 50, "channel": 2,
                 "export_group_path": "/Game/GameModes/Bomb/BombGameState.BombGameState_C",
                 "payload": {"RoundResults": [{"RoundNumber": n - 1, "WinningTeam": self.winners.get(n),
                                               "WinningTeamRole": "attacker", "RoundResult": "elimination"}]}})
        rows.extend(self.extra_events)
        return rows

    def _swiftplay_events(self) -> list[dict]:
        rows: list[dict] = []
        add = rows.append

        running = [1]  # the phase before the recording began, as in the real export

        def phase(t: int, new: int) -> None:
            # The real export pairs every Begin with an Ended of the running phase, at the same time.
            add({"type": "rpc_received", "time_ms": t, "packet_id": 6, "actor_net_guid": 2, "object_net_guid": 2,
                 "channel": 1, "function_name": "ClientGamePhaseEnded", "payload": {"OldPhase": running[0]}})
            add({"type": "rpc_received", "time_ms": t, "packet_id": 6, "actor_net_guid": 2, "object_net_guid": 2,
                 "channel": 1, "function_name": "ClientGamePhaseBegin", "payload": {"NewPhase": new}})
            running[0] = new

        for slot in range(10):
            code = self.agent_codes[slot]
            add({"type": "actor_spawned", "time_ms": 73, "packet_id": 4, "actor_net_guid": self.pawn(1, slot),
                 "channel": 10 + slot, "is_dynamic": True, "actor_path": None,
                 "archetype_path": f"Default__{code}_PC_C",
                 "location": dict(zip("xyz", (*self.spawn_position(1, slot), 100.0)))})
            add({"type": "export_group_received", "time_ms": 80, "packet_id": 5, "actor_net_guid": self.pawn(1, slot),
                 "object_net_guid": self.pawn(1, slot), "channel": 10 + slot,
                 "export_group_path": f"/Game/Characters/{code}/{code}_PC.{code}_PC_C",
                 "payload": {"PlayerState": 200 + slot, "Controller": 300 + slot, "IsPlayerCharacter": True}})
        # Clove's post-death form: a character archetype that isn't a player.
        add({"type": "actor_spawned", "time_ms": 90, "packet_id": 4, "actor_net_guid": 4242, "channel": 60,
             "is_dynamic": True, "actor_path": None, "archetype_path": "Default__Smonk_PostDeath_PC_C"})
        for n in range(1, self.rounds + 1):
            start = self.round_start(n)
            phase(start - BUY_MS, 3)
            phase(start, 4)
            for t, killer, victim in self.kills.get(n, []):
                at = start + int(round(t * 1000))
                add({"type": "rpc_received", "time_ms": at, "packet_id": 7, "actor_net_guid": self.pawn(n, killer),
                     "object_net_guid": self.pawn(n, killer), "channel": 10 + killer,
                     "function_name": "MulticastNotifyKilledEnemy",
                     "payload": {"KillerCharacter": self.pawn(n, killer), "KilledCharacter": self.pawn(n, victim),
                                 "MultikillLevel": 1}})
            if n == self.rounds and self.drop_final_round_end:
                continue
            phase(start + ROUND_MS, 5)
        if self.leaver:
            slot, t = self.leaver
            add({"type": "actor_closed", "time_ms": t, "packet_id": 9, "actor_net_guid": self.pawn(1, slot),
                 "channel": 10 + slot, "reason": "destroyed"})
        rows.extend(self.extra_events)
        return rows

    def _phase_state(self, t: int, phase: int, round_number: int) -> dict:
        return {"type": "export_group_received", "time_ms": t, "packet_id": 9, "actor_net_guid": 50,
                "object_net_guid": 50, "channel": 2,
                "export_group_path": "/Game/GameModes/Bomb/BombGameState.BombGameState_C",
                "payload": {"Phase": phase, "RoundNumber": round_number}}

    def window_end(self, n: int) -> int:
        """Where round n's playback window ends: the next phase after its RoundEnding (the next
        buy phase), or the recording's last event for the final round."""
        if n < self.rounds:
            return self.round_start(n + 1) - BUY_MS
        return max(row["time_ms"] for row in self.events())

    def death_ms(self, n: int, slot: int) -> int | None:
        times = [t for t, _, victim in self.kills.get(n, []) if victim == slot]
        return self.round_start(n) + int(round(min(times) * 1000)) if times else None

    def movement(self) -> list[dict]:
        rows = []
        step = 1000 / self.hz
        for n in range(1, self.rounds + 1):
            start = self.round_start(n)
            for slot in range(10):
                x0, y0 = self.spawn_position(n, slot)
                dead = self.death_ms(n, slot)
                stop = dead if dead is not None else self.window_end(n)
                gone = self.gone_ms(slot)
                if gone is not None:
                    stop = min(stop, gone)
                k = 0
                while True:
                    t = start - 2000 + int(round(k * step))
                    if t > stop:
                        break
                    along = max(0.0, (t - start) / 1000.0)
                    rows.append({"type": "remote_character_movement", "time_ms": t, "packet_id": 20,
                                 "actor_net_guid": self.pawn(n, slot), "object_net_guid": self.pawn(n, slot),
                                 "channel": 10 + slot, "shooter_character_net_guid": self.pawn(n, slot),
                                 "position": {"x": x0 + 40.0 * along, "y": y0, "z": 100.0},
                                 "yaw": 90.0, "pitch": 0.0, "velocity": None, "timestamp": t / 1000.0,
                                 "movement_state": 1, "error_sentinel": False})
                    k += 1
        return rows

    def manifest(self) -> dict:
        manifest = {
            "schema_version": 8, "source_file": f"{MATCH_UUID}.vrf",
            "source_sha256": hashlib.sha256(self.vrf_bytes).hexdigest(), "source_size_bytes": len(self.vrf_bytes),
            "replay_build": BUILD, "replay_version": "13.6.0", "replay_changelist": 1, "duration_ms": 1,
            "parse_profile": "default", "parser_assembly": "Replay.Valorant",
            "parser_version": f"1.0.0+{PIN_COMMIT}",
            "stats": {"packet_count": 1, "packets_with_bunches": 1, "bunch_count": 1, "malformed_packet_count": 0,
                      "partial_error_count": 0, "total_packet_bytes": 1},
            "parse_status": "completed", "total_diagnostic_count": 0, "suppressed_diagnostic_count": 0,
            "diagnostics": [],
        }
        manifest.update(self.manifest_extra)
        return manifest

    def write(self, directory: Path, events: list[dict] | None = None, movement: list[dict] | None = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(json.dumps(self.manifest(), indent=2), encoding="utf-8")
        for name, rows in (("events.ndjson", self.events() if events is None else events),
                           ("movement.ndjson", self.movement() if movement is None else movement)):
            with (directory / name).open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
        return directory

    def write_vrf(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.vrf_bytes)
        return path

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.vrf_bytes).hexdigest()
