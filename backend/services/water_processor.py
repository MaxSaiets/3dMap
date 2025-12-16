"""
Сервіс для обробки водних об'єктів з булевим відніманням
"""
import geopandas as gpd
import trimesh
import numpy as np
from shapely.geometry import Polygon, box
from typing import Optional
from services.terrain_provider import TerrainProvider


def process_water(
    gdf_water: gpd.GeoDataFrame,
    depth_mm: float = 2.0,  # мм (для UI/сумісності)
    depth_meters: Optional[float] = None,  # якщо задано — використовуємо як "метри до масштабування"
    terrain_provider: Optional[TerrainProvider] = None,
    # backward compatibility:
    depth: Optional[float] = None,
) -> Optional[trimesh.Trimesh]:
    """
    Створює меш для води (западини для булевого віднімання)
    
    Args:
        gdf_water: GeoDataFrame з водними об'єктами
        depth: Глибина води (міліметри)
    
    Returns:
        Trimesh об'єкт води або None
    """
    if gdf_water.empty:
        return None
    
    water_meshes = []
    # ВАЖЛИВО:
    # - depth_mm у UI означає ММ НА МОДЕЛІ (після масштабування),
    # - але геометрію ми будуємо в метрах (UTM), і потім масштабуємо до мм.
    # Тому коректний шлях: main.py обчислює depth_meters і передає сюди.
    if depth is not None:
        depth_mm = float(depth)
    if depth_meters is None:
        depth_meters = depth_mm / 1000.0  # fallback (старий режим)

    # Кліп по межах рельєфу (щоб вода "не з'являлась де не треба")
    clip_box = None
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip_box = box(min_x, min_y, max_x, max_y)
        except Exception:
            clip_box = None
    
    for idx, row in gdf_water.iterrows():
        try:
            geom = row.geometry
            
            if not geom:
                continue

            try:
                if not geom.is_valid:
                    geom = geom.buffer(0)
            except Exception:
                continue

            # Кліпимо до bbox (особливо важливо для великих water polygons, які перетинають bbox)
            if clip_box is not None:
                try:
                    geom = geom.intersection(clip_box)
                except Exception:
                    continue
                if geom.is_empty:
                    continue

            # Фільтр по площі (прибирає випадкові артефакти/дуже дрібні плями)
            try:
                if hasattr(geom, "area") and geom.area < 25.0:  # < 25 м²
                    continue
            except Exception:
                pass
            
            # Створюємо западину для води
            # Трохи спрощуємо, щоб прибрати "розпливи" від мікросегментів
            try:
                geom = geom.simplify(0.5, preserve_topology=True)
            except Exception:
                pass

            if isinstance(geom, Polygon):
                mesh = create_water_depression(geom, float(depth_meters), terrain_provider=terrain_provider)
                if mesh:
                    water_meshes.append(mesh)
            elif hasattr(geom, 'geoms'):
                for poly in geom.geoms:
                    if isinstance(poly, Polygon):
                        if hasattr(poly, "area") and poly.area < 25.0:
                            continue
                        try:
                            poly = poly.simplify(0.5, preserve_topology=True)
                        except Exception:
                            pass
                        mesh = create_water_depression(poly, float(depth_meters), terrain_provider=terrain_provider)
                        if mesh:
                            water_meshes.append(mesh)
        except Exception as e:
            print(f"Помилка обробки води {idx}: {e}")
            continue
    
    if not water_meshes:
        return None
    
    # Об'єднуємо всі водні об'єкти
    combined_water = trimesh.util.concatenate(water_meshes)
    return combined_water


def process_water_surface(
    gdf_water: gpd.GeoDataFrame,
    thickness_m: float,
    depth_meters: float,
    terrain_provider: Optional[TerrainProvider] = None,
) -> Optional[trimesh.Trimesh]:
    """
    Creates a thin "water surface" mesh for preview / multi-color printing.
    Assumes the terrain was already depressed by depth_meters, so we place the surface at:
      surface_z = ground_z + depth_meters
    """
    if gdf_water is None or gdf_water.empty:
        return None
    if thickness_m <= 0:
        return None

    meshes = []
    clip_box = None
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip_box = box(min_x, min_y, max_x, max_y)
        except Exception:
            clip_box = None

    for _, row in gdf_water.iterrows():
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

        try:
            geom = geom.simplify(0.5, preserve_topology=True)
        except Exception:
            pass

        polys = [geom] if isinstance(geom, Polygon) else list(getattr(geom, "geoms", []))
        for poly in polys:
            if not isinstance(poly, Polygon) or poly.is_empty:
                continue
            try:
                mesh = trimesh.creation.extrude_polygon(poly, height=float(thickness_m))
            except Exception:
                continue

            if terrain_provider is not None and len(mesh.vertices) > 0:
                v = mesh.vertices.copy()
                old_z = v[:, 2].copy()
                ground = terrain_provider.get_heights_for_points(v[:, :2])
                # ground already includes depression bottom; add depth to get surface level
                v[:, 2] = ground + float(depth_meters) + old_z
                mesh.vertices = v
            else:
                # No terrain: just keep near Z=0
                mesh.apply_translation([0, 0, 0.0])

            if len(mesh.faces) > 0:
                meshes.append(mesh)

    if not meshes:
        return None
    try:
        return trimesh.util.concatenate(meshes)
    except Exception:
        return meshes[0]


def create_water_depression(
    polygon: Polygon,
    depth: float,
    terrain_provider: Optional[TerrainProvider] = None
) -> Optional[trimesh.Trimesh]:
    """
    Створює западину для води (для булевого віднімання з бази)
    
    Args:
        polygon: Полігон води
        depth: Глибина западини (метри)
    
    Returns:
        Trimesh об'єкт западини
    """
    try:
        # Надійний шлях з підтримкою holes: trimesh.creation.extrude_polygon сам тріангулює Shapely polygon (з отворами)
        # Створює volume висотою depth над z=0 → зсуваємо вниз, щоб top був на 0.
        mesh = trimesh.creation.extrude_polygon(polygon, height=float(depth))
        mesh.apply_translation([0, 0, -float(depth)])

        # Драпіруємо на рельєф: new_z = ground_z + old_z
        if terrain_provider is not None and len(mesh.vertices) > 0:
            verts = mesh.vertices.copy()
            old_z = verts[:, 2].copy()
            ground = terrain_provider.get_heights_for_points(verts[:, :2])
            verts[:, 2] = ground + old_z
            mesh.vertices = verts

        return mesh
    except Exception as e:
        print(f"Помилка створення западини води: {e}")
        return None

