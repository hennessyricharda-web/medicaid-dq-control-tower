"""Reproducible pair matrix: lists mechanism pairs with write/write or read/write overlap.
Usage: python tools/pair_matrix.py docs/mechanisms.yaml"""
import sys, yaml
m = yaml.safe_load(open(sys.argv[1] if len(sys.argv) > 1 else "docs/mechanisms.yaml"))
ks = sorted(m); rows = []
for i, a in enumerate(ks):
    for b in ks[i + 1:]:
        ra, wa = set(m[a]["reads"]), set(m[a]["writes"])
        rb, wb = set(m[b]["reads"]), set(m[b]["writes"])
        ww, rw = wa & wb, (ra & wb) | (wa & rb)
        if ww or rw:
            rows.append((a, b, sorted(ww), sorted(rw - ww)))
import signal; signal.signal(signal.SIGPIPE, signal.SIG_DFL)
print(f"{len(ks)} mechanisms, {len(rows)} pairs with write overlap")
for a, b, ww, rw in rows:
    print(f"{a} x {b}: WW={','.join(ww) or '-'} RW={','.join(rw) or '-'}")
