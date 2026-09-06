"""Shared: match-level bootstrap. Resamples MATCHES with replacement, because
kills cluster within rounds, rounds within matches, and matches within a small
recurring friend group -- so raw n badly overstates precision."""
import random, collections

def boot_rate(obs, n_boot=2000, seed=17):
    """obs: list of (match_id, hit:bool). Returns (rate, lo, hi, n, n_matches)."""
    if not obs: return None
    by_match = collections.defaultdict(lambda: [0, 0])
    for mid, hit in obs:
        by_match[mid][0] += bool(hit); by_match[mid][1] += 1
    keys = list(by_match)
    tot_h = sum(by_match[k][0] for k in keys); tot_n = sum(by_match[k][1] for k in keys)
    if tot_n == 0: return None
    rng = random.Random(seed); out = []
    for _ in range(n_boot):
        h = n = 0
        for _ in range(len(keys)):
            a, b = by_match[keys[rng.randrange(len(keys))]]
            h += a; n += b
        if n: out.append(h / n)
    out.sort()
    return (tot_h/tot_n, out[int(.025*len(out))], out[int(.975*len(out))], tot_n, len(keys))

def fmt(r, width=26):
    if r is None: return f"{'--':>{width}}"
    rate, lo, hi, n, nm = r
    return f"{100*rate:5.1f}% [{100*lo:4.1f},{100*hi:4.1f}] n={n:,}/{nm}m".rjust(width)
