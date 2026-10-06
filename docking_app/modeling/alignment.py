"""Deterministic global sequence alignment for preview; explicit alignments allowed."""


def align(target, template):
    n, m = len(target), len(template)
    if not n or not m or max(n, m) > 2000:
        raise ValueError("Alignment sequences must contain 1–2000 residues")
    directions = [bytearray(m + 1) for _ in range(n + 1)]
    previous = [-3 * j for j in range(m + 1)]
    for i in range(1, n + 1):
        current = [-3 * i] + [0] * m
        for j in range(1, m + 1):
            candidates = (previous[j-1] + (3 if target[i-1] == template[j-1] else -1), previous[j] - 3, current[j-1] - 3)
            move = max(range(3), key=lambda k: candidates[k])
            current[j], directions[i][j] = candidates[move], move
        previous = current
    a, b, i, j = [], [], n, m
    while i or j:
        move = directions[i][j] if i and j else (1 if i else 2)
        if move == 0: a.append(target[i-1]); b.append(template[j-1]); i -= 1; j -= 1
        elif move == 1: a.append(target[i-1]); b.append("-"); i -= 1
        else: a.append("-"); b.append(template[j-1]); j -= 1
    return "".join(reversed(a)), "".join(reversed(b))


def metrics(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x != "-" and y != "-"]
    return {"identity": sum(x == y for x, y in pairs) / len(pairs) if pairs else 0,
            "coverage": len(pairs) / len(a.replace("-", "")), "matched_positions": len(pairs),
            "target_gaps": a.count("-"), "template_gaps": b.count("-")}
