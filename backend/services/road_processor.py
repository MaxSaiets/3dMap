"""
Сервіс для обробки доріг з буферизацією та об'єднанням
Покращена версія з фізичною шириною доріг
Використовує trimesh.creation.extrude_polygon для надійної тріангуляції
"""
import osmnx as ox
import trimesh
import numpy as np
import warnings
from shapely.ops import unary_union
from shapely.geometry import Polygon, MultiPolygon, box
from typing import Optional
import geopandas as gpd
from services.terrain_provider import TerrainProvider

# Придушення deprecation warnings
warnings.filterwarnings('ignore', category=DeprecationWarning, module='pandas')


def build_road_polygons(
    G_roads,
    width_multiplier: float = 1.0,
) -> Optional[object]:
    """
    Builds merged road polygons (2D) from a roads graph/edges gdf.
    This is useful for terrain-first operations (flattening terrain under roads) and
    also allows reusing the merged geometry for mesh generation.
    """
    if G_roads is None:
        return None

    # Support graph or edges GeoDataFrame
    gdf_edges = None
    if isinstance(G_roads, gpd.GeoDataFrame):
        gdf_edges = G_roads
    else:
        if not hasattr(G_roads, "edges") or len(G_roads.edges) == 0:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)

    width_map = {
        'motorway': 12,
        'motorway_link': 10,
        'trunk': 10,
        'trunk_link': 8,
        'primary': 8,
        'primary_link': 6,
        'secondary': 6,
        'secondary_link': 5,
        'tertiary': 5,
        'tertiary_link': 4,
        'residential': 4,
        'living_street': 3,
        'service': 3,
        'unclassified': 3,
        'footway': 2,
        'path': 1.5,
        'cycleway': 2,
        'pedestrian': 2,
        'steps': 1
    }

    def get_width(row):
        highway = row.get('highway')
        if isinstance(highway, list):
            highway = highway[0] if highway else None
        elif not highway:
            return 3.0
        width = width_map.get(highway, 3.0)
        return width * width_multiplier

    if 'highway' in gdf_edges.columns:
        gdf_edges = gdf_edges.copy()
        gdf_edges['width'] = gdf_edges.apply(get_width, axis=1)
    else:
        gdf_edges = gdf_edges.copy()
        gdf_edges['width'] = 3.0 * width_multiplier

    road_polygons = []
    for _, row in gdf_edges.iterrows():
        try:
            geom = row.geometry
            if geom is None:
                continue
            if not geom.is_valid:
                geom = geom.buffer(0)
            buffer = geom.buffer(row.width / 2, cap_style=2, join_style=2)
            if buffer and hasattr(buffer, 'area') and buffer.area > 0:
                road_polygons.append(buffer)
        except Exception:
            continue

    if not road_polygons:
        return None

    try:
        return unary_union(road_polygons)
    except Exception:
        return road_polygons[0]


def process_roads(
    G_roads,
    width_multiplier: float = 1.0,
    terrain_provider: Optional[TerrainProvider] = None,
    road_height: float = 1.0,  # Висота дороги у "світових" одиницях (звичайно метри в UTM-проєкції)
    road_embed: float = 0.0,   # Наскільки "втиснути" в рельєф (м), щоб гарантовано не висіла
    merged_roads: Optional[object] = None,  # Optional precomputed merged road polygons
) -> Optional[trimesh.Trimesh]:
    """
    Обробляє дорожню мережу, створюючи 3D меші з правильною шириною
    
    Args:
        G_roads: OSMnx граф доріг
        width_multiplier: Множник для ширини доріг
    
    Returns:
        Trimesh об'єкт з об'єднаними дорогами
    """
    if G_roads is None:
        return None

    # Підтримка 2 режимів:
    # - OSMnx graph (як було)
    # - GeoDataFrame ребер (pyrosm network edges)
    gdf_edges = None
    if isinstance(G_roads, gpd.GeoDataFrame):
        gdf_edges = G_roads
    else:
        if not hasattr(G_roads, "edges") or len(G_roads.edges) == 0:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)
    
    # Build or reuse merged road geometry
    if merged_roads is None:
        print("Створення буферів доріг...")
        merged_roads = build_road_polygons(G_roads, width_multiplier=width_multiplier)
    if merged_roads is None:
        return None
    
    # Якщо є рельєф — кліпимо дороги в межі рельєфу (буферизація може виходити за bbox і давати "провали")
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip = box(min_x, min_y, max_x, max_y)
            merged_roads = merged_roads.intersection(clip)
        except Exception:
            pass

    # Конвертація в список полігонів для обробки
    if isinstance(merged_roads, Polygon):
        road_geoms = [merged_roads]
    elif isinstance(merged_roads, MultiPolygon):
        road_geoms = list(merged_roads.geoms)
    else:
        print("Невідомий тип геометрії після об'єднання")
        return None
    
    print(f"Створення 3D мешу доріг з {len(road_geoms)} полігонів...")
    road_meshes = []
    
    for poly in road_geoms:
        try:
            # Використовуємо trimesh.creation.extrude_polygon для надійної екструзії
            # Це автоматично обробляє дірки (holes) та правильно тріангулює
            try:
                # Екструдуємо полігон на висоту road_height
                rh = max(float(road_height), 0.0001)
                # embed не має бути > road_height, інакше вся дорога “піде під землю”
                re = float(road_embed) if road_embed is not None else 0.0
                re = max(0.0, min(re, rh * 0.8))
                mesh = trimesh.creation.extrude_polygon(poly, height=rh)
                
                # Проектуємо дорогу на рельєф, якщо TerrainProvider доступний
                if terrain_provider is not None:
                    # ВАЖЛИВО: не "вбиваємо" екструзію.
                    # extrude_polygon дає вершини з old_z у [0..road_height].
                    # Потрібно додати рельєф: new_z = ground_z + old_z
                    vertices = mesh.vertices.copy()
                    old_z = vertices[:, 2].copy()
                    ground_z_values = terrain_provider.get_heights_for_points(vertices[:, :2])
                    # Втиснення дороги в рельєф: низ дороги піде в землю на re
                    vertices[:, 2] = ground_z_values + old_z - float(re)
                    mesh.vertices = vertices
                else:
                    # Без рельєфу: "втиснемо" дороги трохи вниз, щоб не було щілин з плоскою базою
                    if float(re) > 0:
                        vertices = mesh.vertices.copy()
                        vertices[:, 2] = vertices[:, 2] - float(re)
                        mesh.vertices = vertices
                
                # Перевірка на валідність
                if len(mesh.faces) > 0 and len(mesh.vertices) > 0:
                    if not mesh.is_volume:
                        try:
                            mesh.fill_holes()
                            mesh.update_faces(mesh.unique_faces())
                            mesh.remove_unreferenced_vertices()
                        except Exception as fix_error:
                            print(f"  Попередження при виправленні мешу: {fix_error}")
                    
                    road_meshes.append(mesh)
                    print(f"  [OK] Створено меш дороги: {len(mesh.vertices)} вершин, {len(mesh.faces)} граней, volume={mesh.is_volume}")
                else:
                    print(f"  ❌ Меш дороги невалідний: {len(mesh.faces)} граней, {len(mesh.vertices)} вершин")
                    
            except Exception as extrude_error:
                print(f"  Помилка екструзії полігону: {extrude_error}")
                # Fallback: спробуємо створити простий меш
                continue
                
        except Exception as e:
            print(f"Помилка обробки полігону дороги: {e}")
            continue
    
    if not road_meshes:
        print("Попередження: Не вдалося створити жодного мешу доріг")
        return None
    
    print(f"Створено {len(road_meshes)} мешів доріг")
    
    # Об'єднання всіх мешів доріг
    print("Об'єднання мешів доріг...")
    try:
        combined_roads = trimesh.util.concatenate(road_meshes)
        print(f"Дороги об'єднано: {len(combined_roads.vertices)} вершин, {len(combined_roads.faces)} граней")
        return combined_roads
    except Exception as e:
        print(f"Помилка об'єднання доріг: {e}")
        # Повертаємо перший меш якщо не вдалося об'єднати
        if road_meshes:
            return road_meshes[0]
        return None

