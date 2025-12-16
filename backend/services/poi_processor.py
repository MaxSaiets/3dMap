"""
POI processor (benches, fountains, etc.) for "wow" detail.

These are tiny printable markers placed on terrain.
"""

from __future__ import annotations

from typing import Optional

import geopandas as gpd
import numpy as np
import trimesh
from shapely.geometry import Point

from services.terrain_provider import TerrainProvider


def process_pois(
    gdf_pois: gpd.GeoDataFrame,
    size_m: float,
    height_m: float,
    embed_m: float,
    terrain_provider: Optional[TerrainProvider] = None,
    max_count: int = 600,
) -> Optional[trimesh.Trimesh]:
    if gdf_pois is None or gdf_pois.empty:
        return None

    # hard cap to keep models printable and not overloaded
    if len(gdf_pois) > max_count:
        gdf_pois = gdf_pois.head(max_count)

    meshes = []
    half = float(size_m) / 2.0

    for _, row in gdf_pois.iterrows():
        geom = row.geometry
        if geom is None:
            continue

        # MultiPoint -> take each point (but still capped by max_count)
        points = []
        if isinstance(geom, Point):
            points = [geom]
        elif hasattr(geom, "geoms"):
            points = [p for p in geom.geoms if isinstance(p, Point)]
        else:
            continue

        for p in points:
            x, y = float(p.x), float(p.y)
            ground = 0.0
            if terrain_provider is not None:
                ground = float(terrain_provider.get_height_at(x, y))

            # A small box marker is more printable than a thin cylinder
            box = trimesh.creation.box(extents=[size_m, size_m, height_m])
            # Place it so it intersects terrain a bit (embed)
            z_center = ground + (height_m / 2.0) - float(embed_m)
            box.apply_translation([x, y, z_center])
            meshes.append(box)

    if not meshes:
        return None

    try:
        return trimesh.util.concatenate(meshes)
    except Exception:
        return meshes[0]


