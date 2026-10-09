# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Pure numpy/scipy D8 hydrology: depression filling, flat routing, flow
accumulation, catchment delineation, flow length and stream extraction.

Deliberately avoids numba-based packages so it installs cleanly everywhere.
"""
from __future__ import annotations

import numpy as np
from skimage.morphology import reconstruction

# D8 neighbour offsets (drow, dcol); index 0..7 = E, SE, S, SW, W, NW, N, NE
OFFSETS = np.array([(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)])
DIST = np.array([1, np.sqrt(2), 1, np.sqrt(2), 1, np.sqrt(2), 1, np.sqrt(2)])


def _shift(a, dr, dc, fill):
    """out[r, c] = a[r+dr, c+dc] (neighbour value), padded with fill."""
    h, w = a.shape
    out = np.full_like(a, fill)
    rs = slice(max(0, -dr), min(h, h - dr))
    cs = slice(max(0, -dc), min(w, w - dc))
    rs2 = slice(max(0, dr), min(h, h + dr))
    cs2 = slice(max(0, dc), min(w, w + dc))
    out[rs, cs] = a[rs2, cs2]
    return out


class FlowGrid:
    """Hydrologically conditioned D8 flow model of a DEM array."""

    def __init__(self, dem: np.ndarray, res: float):
        self.res = float(res)
        self.shape = dem.shape
        h, w = dem.shape
        dem = dem.astype("float64").copy()
        self.nodata = ~np.isfinite(dem)
        if self.nodata.all():
            raise ValueError("DEM has no valid data")
        low = np.nanmin(dem) - 100.0
        dem[self.nodata] = low
        self.dem = dem

        # 1. fill depressions by morphological reconstruction (outlets = edges + nodata)
        seed = dem.copy()
        interior = np.ones_like(self.nodata)
        interior[0, :] = interior[-1, :] = interior[:, 0] = interior[:, -1] = False
        interior &= ~self.nodata
        seed[interior] = dem.max()
        self.filled = reconstruction(seed, dem, method="erosion")

        # 2. steepest-descent D8 on the filled surface
        F = self.filled
        best = np.zeros(self.shape)
        dirn = np.full(self.shape, -1, dtype=np.int8)
        for k, (dr, dc) in enumerate(OFFSETS):
            nb = _shift(F, dr, dc, np.inf)
            s = (F - nb) / DIST[k]
            better = s > best
            best[better] = s[better]
            dirn[better] = k

        # 3. route flats toward their outlets (geodesic distance on equal-height cells)
        edge = ~interior
        flat = (dirn < 0) & ~edge
        if flat.any():
            # breadth-first search outward from cells that already drain, restricted
            # to neighbours at exactly the same filled height (index-based, O(N))
            Ff = F.ravel()
            unassigned = flat.ravel().copy()
            near_flat = np.zeros(self.shape, dtype=bool)
            for dr, dc in OFFSETS:
                near_flat |= _shift(flat, dr, dc, False)
            frontier = np.flatnonzero((near_flat & ~flat & ~self.nodata).ravel())
            dflat = dirn.ravel()
            cols = np.arange(h * w) % w
            while frontier.size:
                fc = cols[frontier]
                new_all = []
                for k, (dr, dc) in enumerate(OFFSETS):
                    # candidate X with X + (dr,dc) == frontier cell
                    xc = fc - dc
                    ok = (xc >= 0) & (xc < w)
                    X = frontier[ok] - (dr * w + dc)
                    src = frontier[ok]
                    ok2 = (X >= 0) & (X < h * w)
                    X, src = X[ok2], src[ok2]
                    sel = unassigned[X] & (np.abs(Ff[X] - Ff[src]) < 1e-9)
                    X = np.unique(X[sel])
                    if X.size:
                        dflat[X] = k
                        unassigned[X] = False
                        new_all.append(X)
                frontier = np.concatenate(new_all) if new_all else np.array([], dtype=np.int64)
            dirn = dflat.reshape(self.shape)
            # any remaining unassigned flats have no outlet -> they act as sinks
        dirn[edge | self.nodata] = -1
        self.dirn = dirn

        # downstream flat index (-1 = outlet / leaves grid)
        rr, cc = np.indices(self.shape)
        valid = dirn >= 0
        dr = np.where(valid, OFFSETS[np.clip(dirn, 0, 7), 0], 0)
        dc = np.where(valid, OFFSETS[np.clip(dirn, 0, 7), 1], 0)
        rd = rr + dr
        cd = cc + dc
        ok = valid & (rd >= 0) & (rd < h) & (cd >= 0) & (cd < w)
        ds = np.where(ok, rd * w + cd, -1).ravel()
        self.ds = ds
        self.step = np.where(ok, DIST[np.clip(dirn, 0, 7)], 0).ravel() * self.res

        # 4. topological levels (Kahn) + flow accumulation
        n = h * w
        indeg = np.bincount(ds[ds >= 0], minlength=n)
        acc = np.ones(n, dtype=np.float64)
        acc[self.nodata.ravel()] = 0
        levels = []
        frontier = np.flatnonzero(indeg == 0)
        while frontier.size:
            levels.append(frontier)
            tgt = ds[frontier]
            m = tgt >= 0
            np.add.at(acc, tgt[m], acc[frontier[m]])
            np.subtract.at(indeg, tgt[m], 1)
            cand = np.unique(tgt[m])
            frontier = cand[indeg[cand] == 0]
        self.levels = levels
        self.acc = acc.reshape(self.shape)  # in cells

    # ------------------------------------------------------------------ helpers
    @property
    def acc_km2(self):
        return self.acc * self.res * self.res / 1e6

    def idx(self, r, c):
        return r * self.shape[1] + c

    def rc(self, i):
        return divmod(int(i), self.shape[1])

    def snap(self, r, c, radius_cells, min_cells=0, prefer="nearest", min_acc=None):
        """Snap (r,c) to a channel cell inside radius."""
        h, w = self.shape
        r0, r1 = max(0, r - radius_cells), min(h, r + radius_cells + 1)
        c0, c1 = max(0, c - radius_cells), min(w, c + radius_cells + 1)
        sub = self.acc[r0:r1, c0:c1]
        rr, cc = np.indices(sub.shape)
        d = np.hypot(rr + r0 - r, cc + c0 - c)
        inside = d <= radius_cells
        if min_acc is not None:
            cand = inside & (sub >= min_acc)
            if cand.any():
                a = np.where(cand, sub, np.inf)
                k = np.unravel_index(np.argmin(a), a.shape)  # smallest consistent channel
                return int(k[0] + r0), int(k[1] + c0)
        cand = inside & (sub >= min_cells)
        if prefer == "nearest" and cand.any():
            dd = np.where(cand, d - 1e-6 * sub, np.inf)
            k = np.unravel_index(np.argmin(dd), dd.shape)
        else:
            a = np.where(inside, sub, -1)
            k = np.unravel_index(np.argmax(a), a.shape)
        return int(k[0] + r0), int(k[1] + c0)

    def catchment(self, r, c):
        """Boolean mask of all cells draining through (r,c)."""
        n = self.ds.size
        target = self.idx(r, c)
        inb = np.zeros(n, dtype=bool)
        inb[target] = True
        # process from downstream to upstream: ds of a cell always has higher level
        for lev in reversed(self.levels):
            t = self.ds[lev]
            m = t >= 0
            inb[lev[m]] |= inb[t[m]]
        inb[target] = True
        return inb.reshape(self.shape)

    def flow_length_to(self, mask, r, c):
        """Flow distance (m) of each cell in mask to outlet (r,c)."""
        n = self.ds.size
        dist = np.zeros(n)
        for lev in reversed(self.levels):
            t = self.ds[lev]
            m = t >= 0
            dist[lev[m]] = dist[t[m]] + self.step[lev[m]]
        out = dist.reshape(self.shape) - dist[self.idx(r, c)]
        return np.where(mask, out, np.nan)

    def downstream_path(self, r, c, n):
        path = [(r, c)]
        i = self.idx(r, c)
        for _ in range(n):
            j = self.ds[i]
            if j < 0:
                break
            path.append(self.rc(j))
            i = j
        return path

    def upstream_main_path(self, r, c, n):
        path = [(r, c)]
        h, w = self.shape
        for _ in range(n):
            best, bestacc = None, 0
            i = self.idx(r, c)
            for dr, dc in OFFSETS:
                rr, cc = r + dr, c + dc
                if 0 <= rr < h and 0 <= cc < w and self.ds[self.idx(rr, cc)] == i:
                    if self.acc[rr, cc] > bestacc:
                        best, bestacc = (rr, cc), self.acc[rr, cc]
            if best is None:
                break
            r, c = best
            path.append(best)
        return path

    def stream_lines(self, mask, min_cells, max_lines=4000):
        """Return list of polylines [(r,c),...] for channels with acc>=min_cells inside mask."""
        stream = mask & (self.acc >= min_cells)
        idxs = np.flatnonzero(stream.ravel())
        if idxs.size == 0:
            return []
        s_flat = stream.ravel()
        ds = self.ds
        # number of stream donors per stream cell
        donors = np.zeros(ds.size, dtype=np.int32)
        dsi = ds[idxs]
        ok = dsi >= 0
        ok[ok] &= s_flat[dsi[ok]]
        np.add.at(donors, dsi[ok], 1)
        starts = idxs[donors[idxs] != 1]  # heads (0) and confluences (>1)
        lines = []
        for s in starts:
            line = [self.rc(s)]
            i = s
            while True:
                j = ds[i]
                if j < 0 or not s_flat[j]:
                    break
                line.append(self.rc(j))
                if donors[j] != 1:
                    break
                i = j
            if len(line) > 1:
                lines.append(line)
            if len(lines) >= max_lines:
                break
        return lines


def slope_percent(dem: np.ndarray, res: float) -> np.ndarray:
    gy, gx = np.gradient(dem, res)
    return np.hypot(gx, gy) * 100.0
