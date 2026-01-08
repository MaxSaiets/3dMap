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
        return {"status": "unknown", "message": f"status fetch failed: {type(e).__name__}"}


def _download(url: str, out_path: Path, timeout_s: int = 180) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            data = resp.read()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return True
    except Exception:
        return False


def _pick_two_adjacent(features: list) -> list:
    by_rc = {}
    for f in features:
        p = f.get("properties") or {}
        by_rc[(str(p.get("row")), str(p.get("col")))] = f
    if not features:
        return []
    first = features[0]
    p0 = first.get("properties") or {}
    r0 = str(p0.get("row"))
    c0 = str(p0.get("col"))
    try:
        c_int = int(c0)
    except Exception:
        return [first]
    neighbor = by_rc.get((r0, str(c_int + 1))) or by_rc.get((r0, str(c_int - 1)))
    return [first, neighbor] if neighbor is not None else [first]


def _shared_edge_from_base(a_stl: Path, b_stl: Path):
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
        zmin = float(m.bounds[0][2])
        v = np.asarray(m.vertices, dtype=float)
        mask = np.abs(v[:, 2] - zmin) < 1e-6
        pts = v[mask][:, :2]
        if len(pts) < 3:
            pts = v[:, :2]
        poly = sg.MultiPoint([tuple(map(float, p)) for p in pts]).convex_hull
        return poly if isinstance(poly, sg.Polygon) else sg.Polygon()

    ma = load_mesh(a_stl)
    mb = load_mesh(b_stl)
    pa = footprint(ma)
    pb = footprint(mb)
    inter = pa.intersection(pb)
    if inter.is_empty:
        return None
    if inter.geom_type == "LineString":
        return inter
    if inter.geom_type == "MultiLineString":
        return max(list(inter.geoms), key=lambda g: g.length, default=None)
    return None


def _edge_dz_for_part(part_a: Path, part_b: Path, edge_line, tol: float = 1e-3, touch_mm: float = 5.0) -> dict:
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

    ma = load_mesh(part_a)
    mb = load_mesh(part_b)

    # sample endpoints of shared edge
    coords = list(getattr(edge_line, "coords", []))
    if len(coords) < 2:
        return {"ok": False, "reason": "edge has no endpoints"}
    e0 = (float(coords[0][0]), float(coords[0][1]))
    e1 = (float(coords[-1][0]), float(coords[-1][1]))

    def nearest_top_vertex(m: trimesh.Trimesh, x: float, y: float) -> tuple[float, float]:
        v = np.asarray(m.vertices, dtype=float)
        zmin = float(m.bounds[0][2])
        keep = v[:, 2] > (zmin + 1e-6)
        v2 = v[keep] if np.any(keep) else v
        d = np.sqrt((v2[:, 0] - float(x)) ** 2 + (v2[:, 1] - float(y)) ** 2)
        j = int(np.argmin(d))
        return float(d[j]), float(v2[j, 2])

    d0a, z0a = nearest_top_vertex(ma, e0[0], e0[1])
    d0b, z0b = nearest_top_vertex(mb, e0[0], e0[1])
    d1a, z1a = nearest_top_vertex(ma, e1[0], e1[1])
    d1b, z1b = nearest_top_vertex(mb, e1[0], e1[1])

    # collect vertices close to the edge and build an XY->Z map (rounded) for robust seam comparison
    def edge_profile_map(m: trimesh.Trimesh) -> dict[tuple[float, float], float]:
        v = np.asarray(m.vertices, dtype=float)
        zmin = float(m.bounds[0][2])
        by: dict[tuple[float, float], float] = {}
        for x, y, z in v:
            if float(z) <= (zmin + 1e-6):
                continue
            if float(edge_line.distance(sg.Point(float(x), float(y)))) <= float(tol):
                key = (round(float(x), 4), round(float(y), 4))
                by[key] = max(by.get(key, -1e9), float(z))
        return by

    ea = edge_profile_map(ma)
    eb = edge_profile_map(mb)
    common = sorted(set(ea.keys()).intersection(set(eb.keys())))
    deltas = [abs(float(ea[k]) - float(eb[k])) for k in common]
    out = {
        "ok": True,
        "endpoint0": {"a": {"dist": d0a, "z": z0a}, "b": {"dist": d0b, "z": z0b}, "abs_dz": abs(z0a - z0b)},
        "endpoint1": {"a": {"dist": d1a, "z": z1a}, "b": {"dist": d1b, "z": z1b}, "abs_dz": abs(z1a - z1b)},
        "edge_profile_counts": {"a": int(len(ea)), "b": int(len(eb)), "common": int(len(common))},
    }
    # If the part doesn't actually touch the shared edge, don't treat nearest-vertex dz as a seam.
    if (d0a > touch_mm and d1a > touch_mm) or (d0b > touch_mm and d1b > touch_mm):
        out["note"] = f"part likely does not touch the shared edge (touch_mm={touch_mm})"
        return out

    if deltas:
        out["profile_max_abs_delta_mm"] = float(np.max(deltas))
        out["profile_mean_abs_delta_mm"] = float(np.mean(deltas))
    return out


def main():
    bounds = {
        "north": float(os.getenv("TEST_NORTH") or 50.429680),
        "south": float(os.getenv("TEST_SOUTH") or 50.420187),
        "east": float(os.getenv("TEST_EAST") or 30.595348),
        "west": float(os.getenv("TEST_WEST") or 30.558576),
        "hex_size_m": float(os.getenv("TEST_HEX_SIZE_M") or 500.0),
        "grid_type": "hexagonal",
    }

    print("[STITCH-OVERLAYS] Requesting grid...")
    grid = _post("/api/hexagonal-grid", bounds, timeout_s=120)
    features = (grid.get("geojson") or {}).get("features") or []
    if not features:
        raise SystemExit("[STITCH-OVERLAYS] No grid features returned")

    zones = _pick_two_adjacent(features)
    if len(zones) < 2:
        raise SystemExit("[STITCH-OVERLAYS] Could not pick two adjacent zones")

    print(
        "[STITCH-OVERLAYS] Selected zones:",
        [(z.get("properties", {}).get("row"), z.get("properties", {}).get("col")) for z in zones],
    )

    params = {
        "terrain_enabled": True,
        "terrain_only": False,
        "flatten_buildings_on_terrain": False,
        "flatten_roads_on_terrain": True,
        "include_parks": True,
        "include_pois": False,
        "water_depth": float(os.getenv("TEST_WATER_MM") or 2.0),
        "terrain_z_scale": float(os.getenv("TEST_ZS") or 1.0),
        "terrain_base_thickness_mm": float(os.getenv("TEST_BASE_MM") or 1.0),
        "terrain_resolution": int(os.getenv("TEST_RES") or 140),
        "terrarium_zoom": int(os.getenv("TEST_ZOOM") or 15),
        "terrain_smoothing_sigma": float(os.getenv("TEST_SMOOTH") or 0.0),
        "terrain_subdivide": False,
        "export_format": "stl",
        "model_size_mm": float(os.getenv("TEST_MODEL_MM") or 100.0),
        "context_padding_m": float(os.getenv("TEST_CONTEXT_PAD_M") or 400.0),
        "preserve_global_xy": True,
        # Keep buildings minimal (API enforces ge constraints)
        "building_height_multiplier": 0.1,
        "building_foundation_mm": 0.1,
        "building_min_height": 1.0,
    }

    payload = {"zones": zones, **bounds, **params}
    print("[STITCH-OVERLAYS] Requesting generate-zones (terrain + overlays)...")
    resp = _post("/api/generate-zones", payload, timeout_s=900)
    all_ids = resp.get("all_task_ids") or []
    if not all_ids:
        all_ids = [resp["task_id"]]
    if len(all_ids) < 2:
        raise SystemExit("[STITCH-OVERLAYS] API did not return 2 task ids")

    pending = set(all_ids[:2])
    start = time.time()
    while pending and time.time() - start < 3600:
        done = []
        for tid in list(pending):
            st = _get_json(f"/api/status/{tid}", timeout_s=20)
            if st.get("status") == "unknown":
                continue
            if st.get("status") in ("completed", "failed"):
                done.append((tid, st.get("status"), st.get("message")))
        for tid, status, msg in done:
            pending.remove(tid)
            print("[STITCH-OVERLAYS] Finished:", tid, status, msg)
        if pending:
            time.sleep(2)
    if pending:
        raise SystemExit("[STITCH-OVERLAYS] Timeout waiting tasks: " + ",".join(sorted(pending)))

    out_dir = Path("output") / "_stitching_overlays"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Download base parts to compute shared edge line
    base_paths = []
    for tid in all_ids[:2]:
        out_path = out_dir / f"{tid}_base.stl"
        url_part = f"{BASE}/api/download/{tid}?format=stl&part=base"
        ok = _download(url_part, out_path, timeout_s=300)
        if not ok:
            url_full = f"{BASE}/api/download/{tid}?format=stl"
            ok = _download(url_full, out_path, timeout_s=300)
        if not ok:
            raise SystemExit("[STITCH-OVERLAYS] Failed to download base STL for " + tid)
        base_paths.append(out_path)

    edge = _shared_edge_from_base(base_paths[0], base_paths[1])
    if edge is None:
        raise SystemExit("[STITCH-OVERLAYS] Could not compute shared edge from base meshes")

    parts = ["roads", "parks", "water"]
    report = {"task_ids": all_ids[:2], "parts": {}}
    for part in parts:
        a = out_dir / f"{all_ids[0]}_{part}.stl"
        b = out_dir / f"{all_ids[1]}_{part}.stl"
        ok_a = _download(f"{BASE}/api/download/{all_ids[0]}?format=stl&part={part}", a, timeout_s=300)
        ok_b = _download(f"{BASE}/api/download/{all_ids[1]}?format=stl&part={part}", b, timeout_s=300)
        if not ok_a or not ok_b:
            report["parts"][part] = {"ok": False, "reason": "part not available (or empty)"}
            continue
        try:
            report["parts"][part] = _edge_dz_for_part(a, b, edge_line=edge, tol=1e-3)
        except Exception as e:
            report["parts"][part] = {"ok": False, "reason": str(e)}

    print("[STITCH-OVERLAYS] Report:", json.dumps(report, ensure_ascii=False, indent=2))
    print("[STITCH-OVERLAYS] Files saved to:", str(out_dir))


if __name__ == "__main__":
    main()


