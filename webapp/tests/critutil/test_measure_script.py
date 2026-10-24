"""`scripts/critical_util.py measure`'s gate tables (pure formatting, no DB)."""

from scripts import critical_util


def gate(failing=()):
    return [{"n": 50 + L, "se_cell": 0.01, "se_ma": 0.01, "pass": L not in failing, "diff_mean": 0.001 * L,
             "diff_sd": 0.01, "matches": 7} for L in range(1, 46)]


def toy():
    return {"gate": {"all": gate((3,)), "yours": gate(), "device": gate(),
                     "by_type": {"all": {"smoke": gate((10,))}, "yours": {"smoke": gate()}}},
            "same_type_overlap": {"smoke": [200.0] + [float(L) for L in range(1, 46)]}}


def test_gate_lines_mark_failures_and_print_overlap():
    lines = critical_util.gate_lines(toy())
    text = "\n".join(lines)
    assert lines[0].startswith("gate per length")
    (l3,) = [x for x in lines if x.lstrip().startswith("3 ") and "all: FAIL" in x]
    assert "yours: pass" in l3 and "device: pass" in l3
    assert any("smoke" in x and "FAIL" in x for x in lines)
    assert "same-type overlap" in text
    assert any("smoke" in x and "10.0%" in x for x in lines), "o_20 / n = 20/200"
