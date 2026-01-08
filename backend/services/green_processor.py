"""
Green areas (parks/forests/grass) processor.

Creates a thin embossed mesh that is draped onto terrain:
new_z = ground_z + old_z - embed

This makes parks/green areas stand out visually and be printable (has thickness).
"""

from __future__ import annotations

from typing import Optional

import geopandas as gpd
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon, box

from services.terrain_provider import TerrainProvider
from services.global_center import GlobalCenter
from shapely.ops import transform, unary_union


def process_green_areas(
    gdf_green: gpd.GeoDataFrame,
    height_m: float,
    embed_m: float,
    terrain_provider: Optional[TerrainProvider] = None,
    global_center: Optional[GlobalCenter] = None,  # UTM -> local
    scale_factor: Optional[float] = None,  # model_mm / world_m (used for print-aware thresholds)
    min_feature_mm: float = 0.8,  # drop features thinner than this on the model
    simplify_mm: float = 0.4,  # simplify tolerance on the model
) -> Optional[trimesh.Trimesh]:
    if gdf_green is None or gdf_green.empty:
        return None

    # In the main pipeline (PBF mode), geometries are in projected CRS (UTM meters),
    # while terrain_provider operates in LOCAL coordinates relative to global_center.
    # Convert green polygons to local so clipping + draping are consistent.
    if global_center is not None:
        try:
            def to_local_transform(x, y, z=None):
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)

            gdf_local = gdf_green.copy()
            gdf_local["geometry"] = gdf_local["geometry"].apply(
                lambda geom: transform(to_local_transform, geom) if geom is not None and not geom.is_empty else geom
            )
            gdf_green = gdf_local
        except Exception:
            # If transform fails, keep original (best effort)
            pass

    # clip to terrain bounds if available
    clip_box = None
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip_box = box(min_x, min_y, max_x, max_y)
        except Exception:
            clip_box = None

    # Print-aware thresholds in world meters
    simplify_tol_m = 0.5
    min_width_m = None
    if scale_factor is not None and float(scale_factor) > 0:
        try:
            simplify_tol_m = max(0.05, float(simplify_mm) / float(scale_factor))
        except Exception:
            simplify_tol_m = 0.5
        try:
            min_width_m = max(0.0, float(min_feature_mm) / float(scale_factor))
        except Exception:
            min_width_m = None

    # Collect + clean polygons first (union reduces seams and drops tiny slivers)
    polys: list[Polygon] = []

    def _iter_polys(g):
        if g is None or getattr(g, "is_empty", False):
            return []
        if isinstance(g, Polygon):
            return [g]
        if isinstance(g, MultiPolygon):
            return list(g.geoms)
        if hasattr(g, "geoms"):
            return [gg for gg in g.geoms if isinstance(gg, Polygon)]
        return []

    for _, row in gdf_green.iterrows():
        geom = getattr(row, "geometry", None)
        if geom is None or getattr(geom, "is_empty", False):
            continue
        try:
            if not geom.is_valid:
                geom = geom.buffer(0)
        except Exception:
            pass
        if geom is None or getattr(geom, "is_empty", False):
            continue

        if clip_box is not None:
            try:
                geom = geom.intersection(clip_box)
            except Exception:
                continue
            if geom is None or getattr(geom, "is_empty", False):
                continue

        for poly in _iter_polys(geom):
            if poly is None or getattr(poly, "is_empty", False):
                continue
            try:
                if not poly.is_valid:
                    poly = poly.buffer(0)
            except Exception:
                pass
            if poly is None or getattr(poly, "is_empty", False):
                continue

            # Drop tiny artifacts (area in m^2)
            try:
                if float(getattr(poly, "area", 0.0) or 0.0) < 100.0:
                    continue
            except Exception:
                pass
            polys.append(poly)

    if not polys:
        return None

    # Union to reduce internal cracks and remove slivers created by clipping
    try:
        merged = unary_union(polys)
        polys = []
        for p in _iter_polys(merged):
            if p is not None and not p.is_empty:
                polys.append(p)
    except Exception:
        pass

    # Filter skinny polygons (these often render as "lines" after simplify/clip)
    filtered: list[Polygon] = []
    for poly in polys:
        if poly is None or poly.is_empty:
            continue
        # Simplify in a scale-aware way
        try:
            poly = poly.simplify(float(simplify_tol_m), preserve_topology=True)
        except Exception:
            pass
        if poly is None or poly.is_empty:
            continue

        if min_width_m is not None and float(min_width_m) > 0:
            try:
                mrr = poly.minimum_rotated_rectangle
                coords = list(getattr(mrr, "exterior").coords)
                if len(coords) >= 4:
                    # rectangle has 4 edges; compute two adjacent side lengths
                    d01 = float(np.hypot(coords[1][0] - coords[0][0], coords[1][1] - coords[0][1]))
                    d12 = float(np.hypot(coords[2][0] - coords[1][0], coords[2][1] - coords[1][1]))
                    width = float(min(d01, d12))
                    if width < float(min_width_m):
                        continue
            except Exception:
                pass
            # Extra sliver filter: approximate "mean width" via area/perimeter.
            # This reliably drops very long thin strips created by clipping artifacts.
            try:
                per = float(getattr(poly, "length", 0.0) or 0.0)
                area = float(getattr(poly, "area", 0.0) or 0.0)
                if per > 0 and area > 0:
                    equiv_width = float((2.0 * area) / per)  # ~ mean width for long shapes
                    if equiv_width < float(min_width_m):
                        continue
            except Exception:
                pass
        filtered.append(poly)

    if not filtered:
        return None

    meshes: list[trimesh.Trimesh] = []
    for poly in filtered:
        try:
            mesh = trimesh.creation.extrude_polygon(poly, height=float(height_m))
        except Exception:
            continue

        if terrain_provider is not None and len(mesh.vertices) > 0:
            v = mesh.vertices.copy()
            old_z = v[:, 2].copy()
            ground = terrain_provider.get_surface_heights_for_points(v[:, :2])
            v[:, 2] = ground + old_z - float(embed_m)
            mesh.vertices = v

        if len(mesh.faces) > 0:
            meshes.append(mesh)

    if not meshes:
        return None

    try:
        return trimesh.util.concatenate(meshes)
    except Exception:
        return meshes[0]


