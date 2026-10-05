"""Every traversal and sight consumer in app/control and app/gaps, found by walking their syntax trees, so the
map-features contract doc (docs/superpowers/specs/2026-10-04-map-features-contract.md) can be checked to
name a policy for each one (review finding 9: generic call shapes and helper names hide consumers).

A consumer is a function that calls a topology method (through `topo`, `self.topo`, `topology.of(...)` or any
name containing "topo"), reads a topology's `links`, calls `cast`, `los`, `seen_from` or `special_links`, or
reads a geometry's `specials`. Keys are `module:Class.function` (nested functions keep their outer chain)."""

from __future__ import annotations

import ast
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[2]
ROOTS = (WEBAPP / "app" / "control", WEBAPP / "app" / "gaps")
SKIP = {"features.py"}          # the adapters themselves
TOPO_METHODS = {"label", "dilate", "edge_out", "dist", "back", "around", "spread"}
SIGHT = {"cast", "los", "seen_from", "special_links"}


def _topoish(node) -> bool:
    if isinstance(node, ast.Name):
        return "topo" in node.id
    if isinstance(node, ast.Attribute):
        return "topo" in node.attr
    if isinstance(node, ast.Call):
        f = node.func
        return isinstance(f, ast.Attribute) and f.attr == "of" and isinstance(f.value, ast.Name) and f.value.id == "topology"
    return False


def consumers() -> dict[str, set[str]]:
    """{module:Class.function: {what it uses}}."""
    out: dict[str, set[str]] = {}
    for root in ROOTS:
        for path in sorted(root.glob("*.py")):
            if path.name in SKIP:
                continue
            module = f"{root.name}/{path.stem}"
            tree = ast.parse(path.read_text(encoding="utf-8"))

            def visit(node, chain):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        visit(child, chain + [child.name])
                        continue
                    used = None
                    if isinstance(child, ast.Call):
                        f = child.func
                        if isinstance(f, ast.Attribute) and f.attr in TOPO_METHODS and _topoish(f.value):
                            used = f"topo.{f.attr}"
                        elif isinstance(f, ast.Name) and f.id in SIGHT:
                            used = f.id
                        elif isinstance(f, ast.Attribute) and f.attr in SIGHT:
                            used = f.attr
                    elif isinstance(child, ast.Attribute):
                        if child.attr == "links" and _topoish(child.value):
                            used = "topo.links"
                        elif child.attr == "specials":
                            used = "specials"
                    if used and chain:
                        out.setdefault(f"{module}:{'.'.join(chain)}", set()).add(used)
                    visit(child, chain)

            visit(tree, [])
    return out


if __name__ == "__main__":
    for key, used in sorted(consumers().items()):
        print(f"{key}\t{', '.join(sorted(used))}")
