import json
import os
import time
import urllib.request
from pathlib import Path


BASE = os.getenv("API_BASE") or "http://127.0.0.1:8000"


def _post(path: str, payload: dict, timeout_s: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = "<no body>"
        raise RuntimeError(f"HTTP {e.code} for {path}: {body}") from e


def _get_json(path: str, timeout_s: int = 60) -> dict:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # In this project, heavy CPU work can temporarily block the single-process server
        # (background task runs in-process), causing status requests to time out.
        # Treat as transient and let the polling loop retry.
        return {"status": "unknown", "message": f"status fetch failed: {type(e).__name__}"}


def _download(url: str, out_path: Path, timeout_s: int = 120) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            data = resp.read()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return True
    except Exception:
        return False


def _pick_two_adjacent(features: list) -> list:
    # Prefer known problematic area if present (row/col from older logs)
    wanted = {("34", "31"), ("34", "32")}
    sel = []
    for f in features:
        p = f.get("properties") or {}
        row = str(p.get("row"))
        col = str(p.get("col"))
        if (row, col) in wanted:
            sel.append(f)
    if len(sel) >= 2:
        return sel[:2]

    # Fallback: pick first and a neighbor by (row,col)
    by_rc = {}
    for f in features:
        p = f.get("properties") or {}
        by_rc[(str(p.get("row")), str(p.get("col")))] = f
    first = features[0]
    p0 = first.get("properties") or {}
    r0 = str(p0.get("row"))
    c0 = str(p0.get("col"))
    try:
        c_int = int(c0)
    except Exception:
        return [first]
    neighbor = by_rc.get((r0, str(c_int + 1))) or by_rc.get((r0, str(c_int - 1)))
    if neighbor is None:
        return [first]
    return [first, neighbor]


def _compute_shared_edge_height_delta(a_stl: Path, b_stl: Path) -> dict:
    """
    Focused stitching validation:
    - Build 2D footprints from bottom-plane vertices (z ~= zmin)
    - Compute intersection (should be a LineString for adjacent hexes)
    - Sample points along the line and raycast down to get top surface z for each tile
    - Report max abs delta
    """
    import numpy as np
    import shapely.geometry as sg
    import trimesh

    def load_mesh(p: Path) -> trimesh.Trimesh:
        m = trimesh.load(str(p))
        if isinstance(m, trimesh.Scene):
            geoms = list(m.geometry.values())
            m = trimesh.util.concatenate(geoms) if geoms else None
        if m is None or not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
            raise ValueError(f"invalid mesh: {p}")
        return m

    def footprint(m: trimesh.Trimesh) -> sg.Polygon:
        b = m.bounds
        zmin = float(b[0][2])
        v = np.asarray(m.vertices, dtype=float)
        # vertices on bottom plane
        mask = np.abs(v[:, 2] - zmin) < 1e-6
        pts = v[mask][:, :2]
        if len(pts) < 3:
            # fallback to convex hull of all verts
            pts = v[:, :2]
        poly = sg.MultiPoint([tuple(map(float, p)) for p in pts]).convex_hull
        if isinstance(poly, sg.Polygon):
            return poly
        # fallback
        return sg.Polygon()

    def nearest_top_z(m: trimesh.Trimesh, x: float, y: float) -> float:
        """
        Robust fallback without rtree/pyembree:
        use nearest vertex in XY (from vertices above the bottom plane).
        This is enough to validate border stitching because stitching_mode forces border vertices to match.
        """
        v = np.asarray(m.vertices, dtype=float)
        zmin = float(m.bounds[0][2])
        # exclude bottom plane vertices
        keep = v[:, 2] > (zmin + 0.5)
        v2 = v[keep]
        if len(v2) == 0:
            v2 = v
        try:
            from scipy.spatial import cKDTree

            tree = cKDTree(v2[:, :2])
            dist, idx = tree.query([float(x), float(y)], k=1)
            if not np.isfinite(dist):
                return float("nan")
            return float(v2[int(idx), 2])
        except Exception:
            d = np.sqrt((v2[:, 0] - float(x)) ** 2 + (v2[:, 1] - float(y)) ** 2)
            j = int(np.argmin(d))
            return float(v2[j, 2])

    ma = load_mesh(a_stl)
    mb = load_mesh(b_stl)

    pa = footprint(ma)
    pb = footprint(mb)
    inter = pa.intersection(pb)
    if inter.is_empty:
        return {"ok": False, "reason": "footprints do not intersect (not adjacent?)"}

    # We expect a LineString/ MultiLineString
    line = None
    if inter.geom_type == "LineString":
        line = inter
    elif inter.geom_type == "MultiLineString":
        # pick the longest
        line = max(list(inter.geoms), key=lambda g: g.length, default=None)
    else:
        # Could be Polygon (overlap) or Point (touch at vertex)
        return {"ok": False, "reason": f"unexpected intersection type: {inter.geom_type}"}

    if line is None or line.length <= 0:
        return {"ok": False, "reason": "shared edge line not found"}

    # Endpoints-based checks (more reliable than nearest-vertex along the whole edge)
    coords = list(getattr(line, "coords", []))
    if len(coords) < 2:
        return {"ok": False, "reason": "shared edge has no endpoints"}
    e0 = (float(coords[0][0]), float(coords[0][1]))
    e1 = (float(coords[-1][0]), float(coords[-1][1]))

    def nearest_vertex(m: trimesh.Trimesh, x: float, y: float) -> tuple[float, float]:
        v = np.asarray(m.vertices, dtype=float)
        d = np.sqrt((v[:, 0] - float(x)) ** 2 + (v[:, 1] - float(y)) ** 2)
        j = int(np.argmin(d))
        return float(d[j]), float(v[j, 2])

    d0a, z0a = nearest_vertex(ma, e0[0], e0[1])
    d0b, z0b = nearest_vertex(mb, e0[0], e0[1])
    d1a, z1a = nearest_vertex(ma, e1[0], e1[1])
    d1b, z1b = nearest_vertex(mb, e1[0], e1[1])

    # How many distinct "top" vertices lie along the shared edge?
    # We do this by taking vertices close to the line and grouping by XY (rounded),
    # then taking max Z per XY to ignore bottom-wall duplicates.
    def edge_profile(m: trimesh.Trimesh, tol: float = 1e-3) -> list[tuple[float, float, float]]:
        v = np.asarray(m.vertices, dtype=float)
        zmin = float(m.bounds[0][2])
        pts = []
        for x, y, z in v:
            if float(z) <= (zmin + 0.5):
                continue
            p = sg.Point(float(x), float(y))
            if float(line.distance(p)) <= float(tol):
                pts.append((float(x), float(y), float(z)))
        if not pts:
            return []
        # group by xy
        by = {}
        for x, y, z in pts:
            key = (round(x, 4), round(y, 4))
            by[key] = max(by.get(key, -1e9), z)
        prof = [(kx, ky, float(z)) for (kx, ky), z in by.items()]
        # sort along line parameter
        def t_of(xy):
            p = sg.Point(xy[0], xy[1])
            try:
                return float(line.project(p))
            except Exception:
                return 0.0
        prof.sort(key=lambda it: t_of((it[0], it[1])))
        return prof

    prof_a = edge_profile(ma)
    prof_b = edge_profile(mb)

    # If both profiles have same number of points, compare by index (they should match in stitching mode)
    deltas = []
    if prof_a and prof_b and len(prof_a) == len(prof_b):
        for (xa, ya, za), (xb, yb, zb) in zip(prof_a, prof_b):
            deltas.append(abs(float(za) - float(zb)))

    out = {
        "ok": True,
        "edge_len_mm": float(line.length),
        "endpoint0": {"xy": [e0[0], e0[1]], "a": {"dist": d0a, "z": z0a}, "b": {"dist": d0b, "z": z0b}, "abs_dz": abs(z0a - z0b)},
        "endpoint1": {"xy": [e1[0], e1[1]], "a": {"dist": d1a, "z": z1a}, "b": {"dist": d1b, "z": z1b}, "abs_dz": abs(z1a - z1b)},
        "edge_profile_counts": {"a": int(len(prof_a)), "b": int(len(prof_b))},
    }
    if deltas:
        out["profile_max_abs_delta_mm"] = float(np.max(deltas))
        out["profile_mean_abs_delta_mm"] = float(np.mean(deltas))
    return out


def main():
    # small bbox for quick grid; user can override via env if needed
    bounds = {
        "north": float(os.getenv("TEST_NORTH") or 50.429680),
        "south": float(os.getenv("TEST_SOUTH") or 50.420187),
        "east": float(os.getenv("TEST_EAST") or 30.595348),
        "west": float(os.getenv("TEST_WEST") or 30.558576),
        "hex_size_m": float(os.getenv("TEST_HEX_SIZE_M") or 500.0),
        "grid_type": "hexagonal",
    }

    print("[STITCH] Requesting grid...")
    grid = _post("/api/hexagonal-grid", bounds, timeout_s=120)
    features = (grid.get("geojson") or {}).get("features") or []
    if not features:
        raise SystemExit("[STITCH] No grid features returned")

    zones = _pick_two_adjacent(features)
    if len(zones) < 2:
        raise SystemExit("[STITCH] Could not pick two adjacent zones")

    print(
        "[STITCH] Selected zones:",
        [(z.get("properties", {}).get("id"), z.get("properties", {}).get("row"), z.get("properties", {}).get("col")) for z in zones],
    )

    # Terrain-only request (fast): no roads/buildings/water/parks/poi.
    params = {
        "terrain_enabled": True,
        "terrain_only": True,
        "flatten_buildings_on_terrain": False,
        "flatten_roads_on_terrain": False,
        "include_parks": False,
        "include_pois": False,
        # API schema currently requires >= 0.1mm, so use the minimum even though terrain_only ignores water anyway
        "water_depth": 0.1,
        "terrain_z_scale": 1.0,
        "terrain_base_thickness_mm": float(os.getenv("TEST_BASE_MM") or 2.0),
        "terrain_resolution": int(os.getenv("TEST_RES") or 140),
        "terrarium_zoom": int(os.getenv("TEST_ZOOM") or 15),
        "terrain_smoothing_sigma": float(os.getenv("TEST_SMOOTH") or 0.0),
        "terrain_subdivide": False,
        "export_format": "stl",
        "model_size_mm": float(os.getenv("TEST_MODEL_MM") or 100.0),
        "context_padding_m": float(os.getenv("TEST_CONTEXT_PAD_M") or 0.0),
        # preserve XY to evaluate real stitching in global space
        "preserve_global_xy": True,
    }

    payload = {"zones": zones, **bounds, **params}
    print("[STITCH] Requesting generate-zones (terrain-only)...")
    resp = _post("/api/generate-zones", payload, timeout_s=900)
    all_ids = resp.get("all_task_ids") or []
    if not all_ids:
        all_ids = [resp["task_id"]]
    if len(all_ids) < 2:
        raise SystemExit("[STITCH] API did not return 2 task ids")

    pending = set(all_ids)
    start = time.time()
    while pending and time.time() - start < 2400:
        done = []
        for tid in list(pending):
            st = _get_json(f"/api/status/{tid}", timeout_s=20)
            if st.get("status") == "unknown":
                # server may be busy; retry later
                continue
            if st.get("status") in ("completed", "failed"):
                done.append((tid, st.get("status"), st.get("message")))
        for tid, status, msg in done:
            pending.remove(tid)
            print("[STITCH] Finished:", tid, status, msg)
        if pending:
            time.sleep(2)
    if pending:
        raise SystemExit("[STITCH] Timeout waiting tasks: " + ",".join(sorted(pending)))

    out_dir = Path("output") / "_stitching_only"
    out_dir.mkdir(parents=True, exist_ok=True)

    base_paths = []
    for tid in all_ids[:2]:
        out_path = out_dir / f"{tid}.stl"
        # Prefer preview part=base if available, fallback to full STL.
        url_part = f"{BASE}/api/download/{tid}?format=stl&part=base"
        ok = _download(url_part, out_path, timeout_s=300)
        if not ok:
            url_full = f"{BASE}/api/download/{tid}?format=stl"
            ok = _download(url_full, out_path, timeout_s=300)
        if not ok:
            raise SystemExit("[STITCH] Failed to download STL for " + tid)
        base_paths.append(out_path)

    import trimesh
    for p in base_paths:
        m = trimesh.load(str(p))
        if isinstance(m, trimesh.Scene):
            geoms = list(m.geometry.values())
            m = trimesh.util.concatenate(geoms) if geoms else None
        b = m.bounds
        print("[STITCH] Bounds", p.name, "zmin", float(b[0][2]), "zmax", float(b[1][2]))

    report = _compute_shared_edge_height_delta(base_paths[0], base_paths[1])
    print("[STITCH] Shared edge report:", json.dumps(report, ensure_ascii=False, indent=2))
    print("[STITCH] Files saved to:", str(out_dir))


if __name__ == "__main__":
    main()


