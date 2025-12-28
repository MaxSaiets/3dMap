"""
Повний тест генерації моделі з реальними координатами
Використовує координати з generate_test_model.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from services.data_loader import fetch_city_data
from services.road_processor import process_roads, build_road_polygons
from services.building_processor import process_buildings
from services.water_processor import process_water_surface
from services.terrain_generator import create_terrain_mesh
from services.model_exporter import export_scene
import trimesh
import numpy as np

# Координати центру Києва
KYIV_CENTER_LAT = 50.4501
KYIV_CENTER_LON = 30.5234

KM_TO_DEGREES_LAT = 0.009
KM_TO_DEGREES_LON = 0.009 / 0.64

# Область 2км x 2км (більша для отримання даних)
HALF_KM = 1.0
north = KYIV_CENTER_LAT + HALF_KM * KM_TO_DEGREES_LAT
south = KYIV_CENTER_LAT - HALF_KM * KM_TO_DEGREES_LAT
east = KYIV_CENTER_LON + HALF_KM * KM_TO_DEGREES_LON
west = KYIV_CENTER_LON - HALF_KM * KM_TO_DEGREES_LON

print("=" * 80)
print("ПОВНИЙ ТЕСТ ГЕНЕРАЦІЇ МОДЕЛІ")
print("=" * 80)
print(f"Координати: N={north:.6f}, S={south:.6f}, E={east:.6f}, W={west:.6f}")
print()

# Параметри
road_width_multiplier = 1.0
road_height_mm = 0.5
road_embed_mm = 0.3
building_min_height = 2.0
building_height_multiplier = 1.0
building_foundation_mm = 0.6
building_embed_mm = 0.2
water_depth = 2.0
terrain_z_scale = 3.0
terrain_base_thickness_mm = 2.0
terrain_resolution = 300
terrarium_zoom = 15
terrain_smoothing_sigma = 2.0
model_size_mm = 100.0

print("Завантаження даних OSM...")
gdf_buildings, gdf_water, G_roads = fetch_city_data(north, south, east, west)
print(f"Завантажено: {len(gdf_buildings)} будівель, {len(gdf_water)} водних об'єктів")

if G_roads is not None:
    import osmnx as ox
    try:
        if hasattr(G_roads, 'edges'):
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)
            print(f"Завантажено: {len(gdf_edges)} сегментів доріг")
        else:
            print(f"Завантажено: {len(G_roads)} сегментів доріг (GeoDataFrame)")
    except:
        print(f"Завантажено дороги (тип: {type(G_roads)})")

# Якщо немає даних, використовуємо fallback
if (gdf_buildings is None or len(gdf_buildings) == 0) and (gdf_water is None or len(gdf_water) == 0) and G_roads is None:
    print("\n[WARN] Немає даних OSM, використовуємо синтетичні дані для тестування...")
    from shapely.geometry import Polygon, LineString, Point
    import geopandas as gpd
    import networkx as nx
    
    # Створюємо синтетичні дані
    # Будівлі
    building_poly = Polygon([(-200, -200), (200, -200), (200, 200), (-200, 200)])
    gdf_buildings = gpd.GeoDataFrame([{'geometry': building_poly, 'height': 10.0}], crs='EPSG:4326')
    gdf_buildings = gdf_buildings.to_crs('EPSG:32636')
    
    # Вода
    water_poly = Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)])
    gdf_water = gpd.GeoDataFrame([{'geometry': water_poly}], crs='EPSG:4326')
    gdf_water = gdf_water.to_crs('EPSG:32636')
    
    # Дороги
    road_line = LineString([(-250, -250), (250, 250)])
    G_roads = nx.MultiDiGraph()
    G_roads.add_node(0, x=30.51, y=50.44, osmid=0)
    G_roads.add_node(1, x=30.53, y=50.46, osmid=1)
    G_roads.add_edge(0, 1, osmid=0, geometry=road_line, highway='primary', length=road_line.length)
    
    print("Створено синтетичні дані для тестування")

# Визначаємо bbox
if not gdf_buildings.empty:
    minx, miny, maxx, maxy = gdf_buildings.total_bounds
elif G_roads is not None:
    import osmnx as ox
    try:
        if hasattr(G_roads, 'total_bounds'):
            minx, miny, maxx, maxy = G_roads.total_bounds
        else:
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)
            minx, miny, maxx, maxy = gdf_edges.total_bounds
    except:
        minx, miny, maxx, maxy = -500, -500, 500, 500
else:
    minx, miny, maxx, maxy = -500, -500, 500, 500

bbox_meters = (float(minx), float(miny), float(maxx), float(maxy))
bbox_degrees = (north, south, east, west)

size_x = float(maxx - minx)
size_y = float(maxy - miny)
avg_xy = (size_x + size_y) / 2.0 if (size_x > 0 and size_y > 0) else max(size_x, size_y)
scale_factor = float(model_size_mm) / float(avg_xy) if avg_xy and avg_xy > 0 else None

source_crs = None
try:
    if not gdf_buildings.empty:
        source_crs = gdf_buildings.crs
    elif G_roads is not None and hasattr(G_roads, "crs"):
        source_crs = getattr(G_roads, "crs", None)
except:
    pass

water_depth_m = (float(water_depth) / float(scale_factor)) if scale_factor else None

print(f"\nBBox (метри): {bbox_meters}")
print(f"Scale factor: {scale_factor}")

# Генерація рельєфу
print("\nГенерація рельєфу...")
terrain_mesh, terrain_provider = create_terrain_mesh(
    bbox_meters,
    z_scale=terrain_z_scale,
    resolution=terrain_resolution,
    latlon_bbox=bbox_degrees,
    source_crs=source_crs,
    terrarium_zoom=terrarium_zoom,
    base_thickness=(float(terrain_base_thickness_mm) / float(scale_factor)) if scale_factor else 5.0,
    flatten_buildings=True,
    building_geometries=list(gdf_buildings.geometry.values) if (gdf_buildings is not None and not gdf_buildings.empty) else None,
    smoothing_sigma=float(terrain_smoothing_sigma),
    water_geometries=list(gdf_water.geometry.values) if (gdf_water is not None and not gdf_water.empty) else None,
    water_depth_m=float(water_depth_m) if water_depth_m is not None else 0.0,
    subdivide=True,
    subdivide_levels=1,
)

if terrain_mesh:
    terrain_bounds = terrain_mesh.bounds
    terrain_min_z = float(terrain_bounds[0][2])
    terrain_max_z = float(terrain_bounds[1][2])
    print(f"Рельєф: {len(terrain_mesh.vertices)} вершин, {len(terrain_mesh.faces)} граней")
    print(f"Рельєф z: [{terrain_min_z:.2f}, {terrain_max_z:.2f}]м")
else:
    print("[ERROR] Рельєф не створено!")
    sys.exit(1)

# Обробка доріг
print("\nОбробка доріг...")
road_mesh = None
if terrain_provider is not None and G_roads is not None:
    try:
        merged_roads_geom = build_road_polygons(G_roads, width_multiplier=road_width_multiplier)
        water_geoms_for_bridges = list(gdf_water.geometry.values) if (gdf_water is not None and not gdf_water.empty) else None
        
        road_mesh = process_roads(
            G_roads,
            road_width_multiplier,
            terrain_provider=terrain_provider,
            road_height=(float(road_height_mm) / float(scale_factor)) if scale_factor else 0.8,
            road_embed=(float(road_embed_mm) / float(scale_factor)) if scale_factor else 0.0,
            merged_roads=merged_roads_geom,
            water_geometries=water_geoms_for_bridges,
            bridge_height_multiplier=1.0,
        )
        
        if road_mesh:
            road_bounds = road_mesh.bounds
            road_min_z = float(road_bounds[0][2])
            road_max_z = float(road_bounds[1][2])
            print(f"Дороги: {len(road_mesh.vertices)} вершин, {len(road_mesh.faces)} граней")
            print(f"Дороги z: [{road_min_z:.2f}, {road_max_z:.2f}]м")
            print(f"Перевірка: дороги {'OK' if terrain_min_z - 1.0 <= road_min_z <= terrain_max_z + 5.0 else 'ERROR'}")
    except Exception as e:
        print(f"[ERROR] Помилка обробки доріг: {e}")
        import traceback
        traceback.print_exc()

# Обробка води
print("\nОбробка води...")
water_mesh = None
if terrain_provider is not None and gdf_water is not None and not gdf_water.empty:
    try:
        water_mesh = process_water_surface(
            gdf_water,
            thickness_m=0.001,
            depth_meters=float(water_depth_m) if water_depth_m is not None else 0.0,
            terrain_provider=terrain_provider,
        )
        
        if water_mesh:
            water_bounds = water_mesh.bounds
            water_min_z = float(water_bounds[0][2])
            water_max_z = float(water_bounds[1][2])
            print(f"Вода: {len(water_mesh.vertices)} вершин, {len(water_mesh.faces)} граней")
            print(f"Вода z: [{water_min_z:.2f}, {water_max_z:.2f}]м")
            print(f"Перевірка: вода {'OK' if terrain_min_z - 1.0 <= water_min_z <= terrain_max_z + 2.0 else 'ERROR'}")
    except Exception as e:
        print(f"[ERROR] Помилка обробки води: {e}")
        import traceback
        traceback.print_exc()

# Обробка будівель
print("\nОбробка будівель...")
building_meshes = []
if terrain_provider is not None and gdf_buildings is not None and not gdf_buildings.empty:
    try:
        building_meshes = process_buildings(
            gdf_buildings,
            min_height=building_min_height,
            height_multiplier=building_height_multiplier,
            terrain_provider=terrain_provider,
            foundation_depth=(float(building_foundation_mm) / float(scale_factor)) if scale_factor else 1.0,
            embed_depth=(float(building_embed_mm) / float(scale_factor)) if scale_factor else 0.0,
        )
        
        if building_meshes:
            combined_buildings = trimesh.util.concatenate([b for b in building_meshes if b is not None])
            if combined_buildings:
                building_bounds = combined_buildings.bounds
                building_min_z = float(building_bounds[0][2])
                building_max_z = float(building_bounds[1][2])
                print(f"Будівлі: {len(combined_buildings.vertices)} вершин, {len(combined_buildings.faces)} граней")
                print(f"Будівлі z: [{building_min_z:.2f}, {building_max_z:.2f}]м")
                print(f"Перевірка: будівлі {'OK' if terrain_min_z - 2.0 <= building_min_z <= terrain_max_z + 20.0 else 'ERROR'}")
    except Exception as e:
        print(f"[ERROR] Помилка обробки будівель: {e}")
        import traceback
        traceback.print_exc()

# Експорт
print("\nЕкспорт моделі...")
output_dir = Path("output")
output_dir.mkdir(exist_ok=True)

output_file = output_dir / "test_full_model.3mf"
export_scene(
    terrain_mesh=terrain_mesh,
    road_mesh=road_mesh,
    building_meshes=building_meshes,
    water_mesh=water_mesh,
    parks_mesh=None,
    poi_mesh=None,
    filename=str(output_file.resolve()),
    format="3mf",
    model_size_mm=model_size_mm,
    add_flat_base=False,
    base_thickness_mm=2.0,
)

print(f"\nМодель збережена: {output_file.resolve()}")
print("=" * 80)

