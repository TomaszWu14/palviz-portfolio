"""Geometria i trasowanie dla eksportu animacji przepływów do Blendera.

Czysty Python (bez Django), żeby dało się go testować w izolacji. Układ
współrzędnych = układ planu hali z widoku 3D modelu magazynu (three.js):
x → w prawo, y → „w głąb" hali (w three.js to oś Z). Regał ma narożnik w
(x_m, y_m), oś szerokości u_w = (cos θ, −sin θ), oś głębokości u_d = (sin θ, cos θ)
— dokładnie jak `group.rotation.y = θ` w warehouse_model/view.html.
Przód regału (znak z numerem) jest po stronie −u_d.
"""
import heapq
import math

SQRT2 = math.sqrt(2.0)


def rack_axes(angle_deg):
    """(u_w, u_d) — jednostkowe osie szerokości i głębokości regału w układzie hali."""
    t = math.radians(angle_deg or 0.0)
    return (math.cos(t), -math.sin(t)), (math.sin(t), math.cos(t))


def rack_point(rack, along, across):
    """Punkt hali: `along` [m] wzdłuż szerokości, `across` [m] wzdłuż głębokości regału."""
    u_w, u_d = rack_axes(rack["angle"])
    return (rack["x"] + u_w[0] * along + u_d[0] * across,
            rack["y"] + u_w[1] * along + u_d[1] * across)


def rack_corners(rack):
    w, d = rack["width"], rack["depth"]
    return [rack_point(rack, a, c) for a, c in ((0, 0), (w, 0), (w, d), (0, d))]


def _inside(pt, rack, margin):
    """Czy punkt leży w obrysie regału poszerzonym o `margin` [m]."""
    u_w, u_d = rack_axes(rack["angle"])
    dx, dy = pt[0] - rack["x"], pt[1] - rack["y"]
    a = dx * u_w[0] + dy * u_w[1]
    c = dx * u_d[0] + dy * u_d[1]
    return -margin <= a <= rack["width"] + margin and -margin <= c <= rack["depth"] + margin


class FloorGrid:
    """Siatka zajętości posadzki: komórka zablokowana, jeśli leży w obrysie regału.

    Rozmiar komórki rośnie automatycznie dla ogromnych hal (limit `max_cells`),
    żeby A* dla kilkudziesięciu tras nie trwał dłużej niż ułamek sekundy."""

    def __init__(self, width_m, depth_m, racks, cell=0.5, margin=0.15, max_cells=250_000):
        width_m, depth_m = max(1.0, width_m), max(1.0, depth_m)
        while (width_m / cell) * (depth_m / cell) > max_cells:
            cell *= 1.5
        self.cell = cell
        self.nx = int(math.ceil(width_m / cell))
        self.ny = int(math.ceil(depth_m / cell))
        self.blocked = bytearray(self.nx * self.ny)
        for r in racks:
            xs = [p[0] for p in rack_corners(r)]
            ys = [p[1] for p in rack_corners(r)]
            i0, i1 = self._clamp_i(min(xs) - margin), self._clamp_i(max(xs) + margin)
            j0, j1 = self._clamp_j(min(ys) - margin), self._clamp_j(max(ys) + margin)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    if _inside(self.center(i, j), r, margin):
                        self.blocked[j * self.nx + i] = 1

    def _clamp_i(self, x):
        return min(self.nx - 1, max(0, int(x // self.cell)))

    def _clamp_j(self, y):
        return min(self.ny - 1, max(0, int(y // self.cell)))

    def cell_of(self, pt):
        return self._clamp_i(pt[0]), self._clamp_j(pt[1])

    def center(self, i, j):
        return ((i + 0.5) * self.cell, (j + 0.5) * self.cell)

    def is_free(self, i, j):
        return 0 <= i < self.nx and 0 <= j < self.ny and not self.blocked[j * self.nx + i]

    def nearest_free(self, pt):
        """Najbliższa wolna komórka (BFS od komórki punktu) albo None, gdy hala zapchana."""
        start = self.cell_of(pt)
        if self.is_free(*start):
            return start
        seen, frontier = {start}, [start]
        while frontier:
            nxt = []
            for i, j in frontier:
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    c = (i + di, j + dj)
                    if c in seen or not (0 <= c[0] < self.nx and 0 <= c[1] < self.ny):
                        continue
                    if self.is_free(*c):
                        return c
                    seen.add(c)
                    nxt.append(c)
            frontier = nxt
        return None

    def route(self, a, b):
        """Trasa A* (8-sąsiedztwo, bez ścinania narożników regałów) z punktu a do b.

        Zwraca listę punktów [m]: dokładny start, uproszczone węzły, dokładny cel.
        Gdy celu nie da się osiągnąć — odcinek prosty (animacja zawsze powstaje)."""
        s, g = self.nearest_free(a), self.nearest_free(b)
        if s is None or g is None:
            return [a, b]
        cells = self._astar(s, g)
        if cells is None:
            return [a, b]
        pts = [a] + [self.center(i, j) for i, j in _simplify(cells)[1:-1]] + [b]
        return _dedupe(pts)

    def _astar(self, s, g):
        def h(c):
            dx, dy = abs(c[0] - g[0]), abs(c[1] - g[1])
            return (dx + dy) + (SQRT2 - 2) * min(dx, dy)

        best = {s: 0.0}
        came = {}
        heap = [(h(s), 0.0, s)]
        while heap:
            _, cost, cur = heapq.heappop(heap)
            if cur == g:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                return path[::-1]
            if cost > best.get(cur, math.inf):
                continue
            ci, cj = cur
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                ni, nj = ci + di, cj + dj
                if not self.is_free(ni, nj):
                    continue
                if di and dj and not (self.is_free(ci + di, cj) and self.is_free(ci, cj + dj)):
                    continue            # nie tniemy narożnika regału po skosie
                nc = cost + (SQRT2 if di and dj else 1.0)
                if nc < best.get((ni, nj), math.inf):
                    best[(ni, nj)] = nc
                    came[(ni, nj)] = cur
                    heapq.heappush(heap, (nc + h((ni, nj)), nc, (ni, nj)))
        return None


def _simplify(cells):
    """Usuwa węzły leżące na prostej (zostają tylko zakręty)."""
    if len(cells) <= 2:
        return list(cells)
    out = [cells[0]]
    for prev, cur, nxt in zip(cells, cells[1:], cells[2:], strict=False):
        if (cur[0] - prev[0], cur[1] - prev[1]) != (nxt[0] - cur[0], nxt[1] - cur[1]):
            out.append(cur)
    out.append(cells[-1])
    return out


def _dedupe(pts, eps=1e-6):
    out = []
    for p in pts:
        if not out or math.dist(out[-1], p) > eps:
            out.append(p)
    return out


def path_length(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False))


def heading_deg(a, b, fallback=0.0):
    """Kierunek jazdy w układzie hali [°] (0 = +x, 90 = +y)."""
    if math.dist(a, b) < 1e-6:
        return fallback
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def unwrap_deg(prev, new):
    """Najbliższy `new` ± 360° względem `prev` — bez obrotów o 350° między klatkami."""
    while new - prev > 180:
        new -= 360
    while new - prev < -180:
        new += 360
    return new
