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
from services.global_center import GlobalCenter
from shapely.ops import transform


def process_pois(
    gdf_pois: gpd.GeoDataFrame,
    size_m: float,
    height_m: float,
    embed_m: float,
    terrain_provider: Optional[TerrainProvider] = None,
    max_count: int = 600,
    global_center: Optional[GlobalCenter] = None,  # Глобальний центр для перетворення координат
) -> Optional[trimesh.Trimesh]:
    if gdf_pois is None or gdf_pois.empty:
        return None

    # hard cap to keep models printable and not overloaded
    if len(gdf_pois) > max_count:
        gdf_pois = gdf_pois.head(max_count)

    # ВАЖЛИВО: Перетворюємо gdf_pois з UTM в локальні координати, якщо використовується глобальний центр
    if global_center is not None:
        try:
            print(f"[DEBUG] Перетворюємо gdf_pois з UTM в локальні координати (глобальний центр)")
            # Створюємо функцію трансформації для Shapely
            def to_local_transform(x, y, z=None):
                """Трансформер: UTM -> локальні координати"""
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)
            
            # Перетворюємо всі геометрії в локальні координати
            gdf_pois_local = gdf_pois.copy()
            gdf_pois_local['geometry'] = gdf_pois_local['geometry'].apply(
                lambda geom: transform(to_local_transform, geom) if geom is not None and not geom.is_empty else geom
            )
            gdf_pois = gdf_pois_local
            print(f"[DEBUG] Перетворено {len(gdf_pois)} геометрій POI в локальні координати")
        except Exception as e:
            print(f"[WARN] Не вдалося перетворити gdf_pois в локальні координати: {e}")
            import traceback
            traceback.print_exc()

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


