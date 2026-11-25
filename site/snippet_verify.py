def matches(value: float, unit: str, decimals: int, evidence: list[float]) -> bool:
    for v, f in _candidates(value, unit):
        # half a unit in the last shown digit (scaled into evidence units: "4.9 GW" = ±50 MW), or 0.5%
        tol = max(0.5 * 10 ** (-decimals) * f, abs(v) * 0.005)
        if any(abs(v - e) <= tol + 1e-9 for e in evidence):
            return True
    return False

def verify(text: str, evidence: list[float]) -> Report:
    rep = Report()
    for raw, value, unit, decimals in extract(text):
        rep.checked.append(raw)
        if not matches(value, unit, decimals, evidence):
            rep.unverified.append(raw)
    return rep
