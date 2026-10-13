"""Rastreamento simples de pessoas (vizinho mais próximo) para tripwire e loitering."""
from dataclasses import dataclass, field


def _side(a, b, p) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def crossing(a, b, p0, p1):
    """Direção do cruzamento do segmento p0→p1 sobre a linha a→b: 'ab' (esq→dir de a→b), 'ba' ou None."""
    s0, s1 = _side(a, b, p0), _side(a, b, p1)
    if s0 == 0 or s1 == 0 or (s0 > 0) == (s1 > 0):
        return None
    d0, d1 = _side(p0, p1, a), _side(p0, p1, b)
    if (d0 > 0) == (d1 > 0):  # cruzou a reta, mas fora do segmento a-b
        return None
    return "ab" if s0 < 0 else "ba"


def point_in_polygon(pt, poly) -> bool:
    x, y = pt
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


@dataclass
class Track:
    id: int
    first_seen: float
    last_seen: float
    foot: tuple
    box: tuple
    prev_foot: tuple | None = None
    loiter_fired: bool = False
    wires_fired: set = field(default_factory=set)


class Tracker:
    def __init__(self, max_dist: float = 0.2, max_age: float = 4.0):
        self.max_dist, self.max_age = max_dist, max_age
        self.tracks: list[Track] = []
        self._next = 1

    def update(self, boxes, now: float) -> list[Track]:
        """boxes: (x,y,w,h) normalizados. Associa cada caixa ao track mais próximo (pé = base da caixa)."""
        self.tracks = [t for t in self.tracks if now - t.last_seen <= self.max_age]
        free = list(self.tracks)
        seen = []
        for box in boxes:
            foot = (box[0] + box[2] / 2, box[1] + box[3])
            best = min(free, key=lambda t: (t.foot[0] - foot[0]) ** 2 + (t.foot[1] - foot[1]) ** 2, default=None)
            if best and ((best.foot[0] - foot[0]) ** 2 + (best.foot[1] - foot[1]) ** 2) ** 0.5 <= self.max_dist:
                free.remove(best)
                best.prev_foot, best.foot, best.box, best.last_seen = best.foot, foot, box, now
                seen.append(best)
            else:
                t = Track(self._next, now, now, foot, box)
                self._next += 1
                self.tracks.append(t)
                seen.append(t)
        return seen
