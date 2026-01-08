"""
Тест для перевірки накладання мешів на правильних висотах
Створює синтетичні дані для тестування
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import trimesh
import numpy as np
from shapely.geometry import Polygon, LineString
import geopandas as gpd
from services.terrain_generator import create_terrain_mesh
from services.road_processor import process_roads, build_road_polygons
from services.water_processor import process_water_surface
from services.building_processor import process_buildings
from services.model_exporter import export_scene

print("=" * 80)
print("ТЕСТ НАКЛАДАННЯ МЕШІВ")
print("=" * 80)

# Створюємо синтетичні дані
# Область 500м x 500м
bbox_meters = (-250.0, -250.0, 250.0, 250.0)
bbox_degrees = (50.45, 50.44, 30.52, 30.51)

# Створюємо синтетичний рельєф
print("\n1. Створення рельєфу...")
terrain_mesh, terrain_provider = create_terrain_mesh(
    bbox_meters,
    z_scale=3.0,
    resolution=200,
    latlon_bbox=bbox_degrees,
    source_crs=None,
    terrarium_zoom=15,
    base_thickness=5.0,
    flatten_buildings=False,
    building_geometries=None,
    smoothing_sigma=2.0,
    water_geometries=None,
    water_depth_m=0.0,
    subdivide=True,
    subdivide_levels=1,
)

if terrain_mesh is None:
    print("[ERROR] Рельєф не створено!")
    sys.exit(1)

terrain_bounds = terrain_mesh.bounds
terrain_min_z = float(terrain_bounds[0][2])
terrain_max_z = float(terrain_bounds[1][2])
print(f"Рельєф: {len(terrain_mesh.vertices)} вершин, {len(terrain_mesh.faces)} граней")
print(f"Рельєф bounds: min_z={terrain_min_z:.2f}м, max_z={terrain_max_z:.2f}м")
print(f"TerrainProvider bounds: {terrain_provider.get_bounds()}")

# Створюємо синтетичну дорогу (LineString)
print("\n2. Створення синтетичної дороги...")
road_line = LineString([(-200, -200), (200, 200)])  # Діагональна дорога
road_width = 10.0  # 10 метрів

# Створюємо буфер для дороги
road_poly = road_line.buffer(road_width / 2.0)

# Створюємо GeoDataFrame для дороги
gdf_road = gpd.GeoDataFrame([{'geometry': road_poly}], crs='EPSG:4326')
gdf_road = gdf_road.to_crs('EPSG:32636')  # UTM Zone 36N для Києва

# Створюємо граф доріг (простий)
import osmnx as ox
G_roads = ox.graph_from_place("Kyiv, Ukraine", network_type="drive", simplify=False)
if G_roads is None or len(G_roads.edges) == 0:
    # Створюємо простий граф вручну
    print("Створення простого графу доріг...")
    G_roads = ox.graph_from_bbox(50.45, 50.44, 30.52, 30.51, network_type="drive")
    if G_roads is None or len(G_roads.edges) == 0:
        # Якщо не вдалося, створюємо мінімальний граф
        print("Створення мінімального графу доріг...")
        from shapely.geometry import Point
        import networkx as nx
        
        G_roads = nx.MultiDiGraph()
        # Додаємо вузли
        G_roads.add_node(0, x=30.51, y=50.44, osmid=0)
        G_roads.add_node(1, x=30.52, y=50.45, osmid=1)
        # Додаємо ребро (дорогу)
        G_roads.add_edge(0, 1, osmid=0, geometry=road_line, highway='primary', length=road_line.length)

print(f"Дороги: {len(G_roads.edges) if hasattr(G_roads, 'edges') else 'N/A'} сегментів")

# Обробка доріг
print("\n3. Обробка доріг...")
road_mesh = None
try:
    merged_roads = build_road_polygons(G_roads, width_multiplier=1.0)
    if merged_roads is not None:
        print(f"Merged roads: {type(merged_roads)}")
        
        road_mesh = process_roads(
            G_roads,
            road_width_multiplier=1.0,
            terrain_provider=terrain_provider,
            road_height=0.8,  # 0.8 метра
            road_embed=0.0,
            merged_roads=merged_roads,
            water_geometries=None,
            bridge_height_multiplier=1.0,
        )
        
        if road_mesh:
            road_bounds = road_mesh.bounds
            road_min_z = float(road_bounds[0][2])
            road_max_z = float(road_bounds[1][2])
            print(f"Дороги: {len(road_mesh.vertices)} вершин, {len(road_mesh.faces)} граней")
            print(f"Дороги bounds: min_z={road_min_z:.2f}м, max_z={road_max_z:.2f}м")
            
            # Перевірка накладання
            print(f"\nПеревірка накладання доріг:")
            print(f"  Рельєф: z=[{terrain_min_z:.2f}, {terrain_max_z:.2f}]м")
            print(f"  Дороги: z=[{road_min_z:.2f}, {road_max_z:.2f}]м")
            
            if road_min_z < terrain_min_z - 1.0:
                print(f"  [ERROR] Дороги занадто низько: {road_min_z:.2f}м < {terrain_min_z:.2f}м - 1.0м")
            elif road_min_z > terrain_max_z + 1.0:
                print(f"  [ERROR] Дороги занадто високо: {road_min_z:.2f}м > {terrain_max_z:.2f}м + 1.0м")
            else:
                print(f"  [OK] Дороги правильно накладені на рельєф")
        else:
            print("[WARN] Дороги не оброблено")
    else:
        print("[WARN] Merged roads = None")
except Exception as e:
    print(f"[ERROR] Помилка обробки доріг: {e}")
    import traceback
    traceback.print_exc()

# Створюємо синтетичну воду
print("\n4. Створення синтетичної води...")
water_poly = Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)])
gdf_water = gpd.GeoDataFrame([{'geometry': water_poly}], crs='EPSG:4326')
gdf_water = gdf_water.to_crs('EPSG:32636')

water_mesh = None
try:
    water_mesh = process_water_surface(
        gdf_water,
        thickness_m=0.001,
        depth_meters=2.0,
        terrain_provider=terrain_provider,
    )
    
    if water_mesh:
        water_bounds = water_mesh.bounds
        water_min_z = float(water_bounds[0][2])
        water_max_z = float(water_bounds[1][2])
        print(f"Вода: {len(water_mesh.vertices)} вершин, {len(water_mesh.faces)} граней")
        print(f"Вода bounds: min_z={water_min_z:.2f}м, max_z={water_max_z:.2f}м")
        
        # Перевірка накладання
        print(f"\nПеревірка накладання води:")
        print(f"  Рельєф: z=[{terrain_min_z:.2f}, {terrain_max_z:.2f}]м")
        print(f"  Вода: z=[{water_min_z:.2f}, {water_max_z:.2f}м]")
        
        # Вода повинна бути на рівні рельєфу + depth
        expected_water_z = terrain_min_z + 2.0
        if abs(water_min_z - expected_water_z) > 1.0:
            print(f"  [ERROR] Вода на неправильній висоті: {water_min_z:.2f}м, очікувалось ~{expected_water_z:.2f}м")
        else:
            print(f"  [OK] Вода правильно накладені на рельєф")
    else:
        print("[WARN] Вода не оброблена")
except Exception as e:
    print(f"[ERROR] Помилка обробки води: {e}")
    import traceback
    traceback.print_exc()

# Створюємо синтетичну будівлю
print("\n5. Створення синтетичної будівлі...")
building_poly = Polygon([(50, 50), (150, 50), (150, 150), (50, 150)])
gdf_buildings = gpd.GeoDataFrame([{'geometry': building_poly, 'height': 10.0}], crs='EPSG:4326')
gdf_buildings = gdf_buildings.to_crs('EPSG:32636')

building_meshes = []
try:
    building_meshes = process_buildings(
        gdf_buildings,
        min_height=2.0,
        height_multiplier=1.0,
        terrain_provider=terrain_provider,
        foundation_depth=1.0,
        embed_depth=0.0,
    )
    
    if building_meshes:
        combined_buildings = trimesh.util.concatenate([b for b in building_meshes if b is not None])
        if combined_buildings:
            building_bounds = combined_buildings.bounds
            building_min_z = float(building_bounds[0][2])
            building_max_z = float(building_bounds[1][2])
            print(f"Будівлі: {len(combined_buildings.vertices)} вершин, {len(combined_buildings.faces)} граней")
            print(f"Будівлі bounds: min_z={building_min_z:.2f}м, max_z={building_max_z:.2f}м")
            
            # Перевірка накладання
            print(f"\nПеревірка накладання будівель:")
            print(f"  Рельєф: z=[{terrain_min_z:.2f}, {terrain_max_z:.2f}]м")
            print(f"  Будівлі: z=[{building_min_z:.2f}, {building_max_z:.2f}]м")
            
            if building_min_z < terrain_min_z - 2.0:
                print(f"  [ERROR] Будівлі занадто низько: {building_min_z:.2f}м < {terrain_min_z:.2f}м - 2.0м")
            else:
                print(f"  [OK] Будівлі правильно накладені на рельєф")
    else:
        print("[WARN] Будівлі не оброблені")
except Exception as e:
    print(f"[ERROR] Помилка обробки будівель: {e}")
    import traceback
    traceback.print_exc()

# Експорт
print("\n6. Експорт моделі...")
output_dir = Path("output")
output_dir.mkdir(exist_ok=True)

output_file = output_dir / "test_alignment.3mf"
export_scene(
    terrain_mesh=terrain_mesh,
    road_mesh=road_mesh,
    building_meshes=building_meshes,
    water_mesh=water_mesh,
    parks_mesh=None,
    poi_mesh=None,
    filename=str(output_file.resolve()),
    format="3mf",
    model_size_mm=100.0,
    add_flat_base=False,
    base_thickness_mm=2.0,
)

print(f"\nМодель збережена: {output_file.resolve()}")
print("=" * 80)


