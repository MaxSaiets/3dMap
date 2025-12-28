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
from shapely.ops import transform


def process_green_areas(
    gdf_green: gpd.GeoDataFrame,
    height_m: float,
    embed_m: float,
    terrain_provider: Optional[TerrainProvider] = None,
    global_center: Optional[GlobalCenter] = None,  # UTM -> local
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

    meshes = []
    for _, row in gdf_green.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        try:
            if not geom.is_valid:
                geom = geom.buffer(0)
        except Exception:
            continue

        if clip_box is not None:
            try:
                geom = geom.intersection(clip_box)
            except Exception:
                continue
            if geom.is_empty:
                continue

        # remove tiny artifacts (area in m^2, because projected)
        try:
            if hasattr(geom, "area") and float(geom.area) < 100.0:
                continue
        except Exception:
            pass

        geoms = []
        if isinstance(geom, Polygon):
            geoms = [geom]
        elif isinstance(geom, MultiPolygon):
            geoms = list(geom.geoms)
        elif hasattr(geom, "geoms"):
            geoms = [g for g in geom.geoms if isinstance(g, Polygon)]
        else:
            continue

        for poly in geoms:
            try:
                poly = poly.simplify(0.5, preserve_topology=True)
            except Exception:
                pass

            try:
                mesh = trimesh.creation.extrude_polygon(poly, height=float(height_m))
            except Exception:
                continue

            if terrain_provider is not None and len(mesh.vertices) > 0:
                v = mesh.vertices.copy()
                old_z = v[:, 2].copy()
                ground = terrain_provider.get_heights_for_points(v[:, :2])
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


