"""Order a batch of renames so chains and swaps apply safely (design §9.2).

`a→b, b→a` becomes `a→__ul_tmp_1, b→a, __ul_tmp_1→b`.
"""

TMP_PREFIX = "__ul_tmp_"


def order_renames(pairs):
    """Turn {old: new} (or a list of pairs) into a list of (src, dst) steps.

    Each step renames something that exists to a name that is free at that moment.
    """
    pending = dict(pairs)
    pending = {old: new for old, new in pending.items() if old != new}
    if len(set(pending.values())) != len(pending):
        raise ValueError("two elements renamed to the same name")

    steps = []
    tmp_count = 0
    while pending:
        # Anything whose target isn't about to be vacated by another pending rename can go now.
        ready = [old for old, new in pending.items() if new not in pending]
        if ready:
            for old in sorted(ready):
                steps.append((old, pending.pop(old)))
            continue
        # Only cycles remain: break one by parking its first element on a temp name.
        old = sorted(pending)[0]
        tmp_count += 1
        tmp = f"{TMP_PREFIX}{tmp_count}"
        steps.append((old, tmp))
        pending[tmp] = pending.pop(old)
    return steps


def apply_to_names(names, steps):
    """Apply ordered steps to a set of names (used to check a batch and in tests)."""
    names = set(names)
    for src, dst in steps:
        if src not in names:
            raise KeyError(f"rename source {src!r} missing")
        if dst in names:
            raise KeyError(f"rename target {dst!r} already exists")
        names.remove(src)
        names.add(dst)
    return names
