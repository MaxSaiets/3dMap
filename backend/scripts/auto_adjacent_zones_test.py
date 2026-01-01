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
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(path: str, timeout_s: int = 60) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
    # Prefer known problematic area (from prior logs)
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

    # Fallback: pick the first feature and a +/-1 neighbor by (row,col)
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


def _mesh_diagnostics(stl_path: Path) -> dict:
    # Import here so the script still runs even if trimesh isn't installed for some users
    import numpy as np
    import trimesh

    mesh = trimesh.load(str(stl_path))
    if isinstance(mesh, trimesh.Scene):
        geoms = list(mesh.geometry.values())
        mesh = trimesh.util.concatenate(geoms) if geoms else None
    if mesh is None or not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        return {"ok": False, "reason": "empty/invalid mesh"}

    bounds = mesh.bounds
    size = (bounds[1] - bounds[0]).astype(float)

    # Detect "curtains/sheets": many large-area near-vertical faces
    # near-vertical faces have small |normal_z|
    try:
        normals = mesh.face_normals
        areas = mesh.area_faces
        vertical = np.abs(normals[:, 2]) < 0.15
        big = areas > float(np.quantile(areas, 0.95))
        curtain_score = float(np.mean(vertical & big))  # 0..1
    except Exception:
        curtain_score = 0.0

    return {
        "ok": True,
        "verts": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "size_mm": [float(size[0]), float(size[1]), float(size[2])],
        "z_min": float(bounds[0][2]),
        "z_max": float(bounds[1][2]),
        "curtain_score": curtain_score,
        "watertight": bool(getattr(mesh, "is_watertight", False)),
    }


def main():
    # Use the bbox observed in prior successful runs (Kyiv area)
    bounds = {
        "north": 50.429680,
        "south": 50.420187,
        "east": 30.595348,
        "west": 30.558576,
        "hex_size_m": 500.0,
        "grid_type": "hexagonal",
    }

    print("[AUTO] Requesting grid...")
    grid = _post("/api/hexagonal-grid", bounds, timeout_s=120)
    features = (grid.get("geojson") or {}).get("features") or []
    print(f"[AUTO] Grid features: {len(features)}")
    if not features:
        raise SystemExit("[AUTO] No grid features returned")

    zones = _pick_two_adjacent(features)
    if len(zones) < 2:
        raise SystemExit("[AUTO] Could not pick two adjacent zones from returned grid")
    print("[AUTO] Selected zones:", [(z.get("properties", {}).get("id"), z.get("properties", {}).get("row"), z.get("properties", {}).get("col")) for z in zones])

    export_format = (os.getenv("EXPORT_FORMAT") or "stl").lower()
    if export_format not in ("stl", "3mf"):
        export_format = "stl"

    params = {
        "road_width_multiplier": 0.8,
        "road_height_mm": 0.5,
        "road_embed_mm": 0.3,
        "building_min_height": 5.0,
        "building_height_multiplier": 1.8,
        "building_foundation_mm": 0.6,
        "building_embed_mm": 0.2,
        "building_max_foundation_mm": 5.0,
        "water_depth": 2.0,
        "terrain_enabled": True,
        "terrain_z_scale": 0.8,
        "terrain_base_thickness_mm": 2.0,
        "terrain_resolution": 160,
        "terrarium_zoom": 15,
        "terrain_smoothing_sigma": 1.5,
        "terrain_subdivide": False,
        "terrain_subdivide_levels": 1,
        "flatten_buildings_on_terrain": True,
        "flatten_roads_on_terrain": False,
        "export_format": export_format,
        "model_size_mm": 100.0,
        "context_padding_m": 400.0,
    }

    payload = {"zones": zones, **bounds, **params}
    print("[AUTO] Requesting generate-zones... (can take a few minutes)")
    # generate-zones can be slow because it computes global elevation reference and may fetch DEM tiles
    resp = _post("/api/generate-zones", payload, timeout_s=900)
    all_ids = resp.get("all_task_ids") or []
    if not all_ids:
        all_ids = [resp["task_id"]]
    print("[AUTO] Task ids:", all_ids)

    pending = set(all_ids)
    start = time.time()
    while pending and time.time() - start < 2400:
        done = []
        for tid in list(pending):
            st = _get_json(f"/api/status/{tid}", timeout_s=60)
            if st.get("status") in ("completed", "failed"):
                done.append((tid, st.get("status"), st.get("message")))
        for tid, status, msg in done:
            pending.remove(tid)
            print("[AUTO] Finished:", tid, status, msg)
        if pending:
            time.sleep(2)
    if pending:
        raise SystemExit("[AUTO] Timeout waiting tasks: " + ",".join(sorted(pending)))

    out_dir = Path("output")
    dl_dir = out_dir / "_auto_adjacent"
    dl_dir.mkdir(parents=True, exist_ok=True)

    parts = ("base", "roads", "parks")
    report = {}
    for tid in all_ids:
        report[tid] = {}
        for part in parts:
            out_path = dl_dir / f"{tid}_{part}.stl"
            url = f"{BASE}/api/download/{tid}?format=stl&part={part}"
            ok = _download(url, out_path, timeout_s=120)
            if not ok:
                report[tid][part] = {"downloaded": False}
                continue
            diag = _mesh_diagnostics(out_path)
            diag["downloaded"] = True
            report[tid][part] = diag

    print("\n[AUTO] Diagnostics summary (curtain_score ~ 0 is good):")
    for tid in all_ids:
        print(f"  Task {tid}:")
        for part in parts:
            d = report[tid].get(part) or {}
            if not d.get("downloaded"):
                print(f"    - {part}: download failed")
                continue
            print(
                f"    - {part}: size_mm={d.get('size_mm')} z=[{d.get('z_min'):.2f},{d.get('z_max'):.2f}] "
                f"faces={d.get('faces')} curtain_score={d.get('curtain_score'):.3f}"
            )

    print("\n[AUTO] Done. Files saved to:", str(dl_dir))


if __name__ == "__main__":
    main()


