"""
Скрипт для тестування генерації моделі з детальним логуванням
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from services.data_loader import fetch_city_data
from services.road_processor import process_roads, build_road_polygons, detect_bridges
from services.building_processor import process_buildings
from services.water_processor import process_water, process_water_surface
from services.terrain_generator import create_terrain_mesh
from services.extras_loader import fetch_extras
from services.green_processor import process_green_areas
from services.poi_processor import process_pois
from services.model_exporter import export_scene
import trimesh
import numpy as np

# Координати центру Києва (з generate_test_model.py)
KYIV_CENTER_LAT = 50.4501
KYIV_CENTER_LON = 30.5234

# 1 км = приблизно 0.009 градусів на широті
KM_TO_DEGREES_LAT = 0.009
KM_TO_DEGREES_LON = 0.009 / 0.64  # cos(50°) ≈ 0.64

# Область 1км x 1км
HALF_KM = 0.5
north = KYIV_CENTER_LAT + HALF_KM * KM_TO_DEGREES_LAT
south = KYIV_CENTER_LAT - HALF_KM * KM_TO_DEGREES_LAT
east = KYIV_CENTER_LON + HALF_KM * KM_TO_DEGREES_LON
west = KYIV_CENTER_LON - HALF_KM * KM_TO_DEGREES_LON

print("=" * 80)
print("ТЕСТУВАННЯ ГЕНЕРАЦІЇ МОДЕЛІ З ДЕТАЛЬНИМ ЛОГУВАННЯМ")
print("=" * 80)
print(f"Координати:")
print(f"  Північ: {north:.6f}")
print(f"  Південь: {south:.6f}")
print(f"  Схід: {east:.6f}")
print(f"  Захід: {west:.6f}")
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
try:
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
    else:
        print("Дороги не завантажені (G_roads = None)")
        
except Exception as e:
    print(f"Помилка завантаження даних: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Перевіряємо чи є дані
if (gdf_buildings is None or len(gdf_buildings) == 0) and (gdf_water is None or len(gdf_water) == 0) and G_roads is None:
    print("\n[ERROR] Немає даних для генерації! Спробуємо інші координати...")
    # Спробуємо більшу область
    HALF_KM = 1.0
    north = KYIV_CENTER_LAT + HALF_KM * KM_TO_DEGREES_LAT
    south = KYIV_CENTER_LAT - HALF_KM * KM_TO_DEGREES_LAT
    east = KYIV_CENTER_LON + HALF_KM * KM_TO_DEGREES_LON
    west = KYIV_CENTER_LON - HALF_KM * KM_TO_DEGREES_LON
    print(f"Спробуємо більшу область (2км x 2км):")
    print(f"  Північ: {north:.6f}, Південь: {south:.6f}, Схід: {east:.6f}, Захід: {west:.6f}")
    try:
        gdf_buildings, gdf_water, G_roads = fetch_city_data(north, south, east, west)
        print(f"Завантажено: {len(gdf_buildings)} будівель, {len(gdf_water)} водних об'єктів")
    except Exception as e:
        print(f"Помилка завантаження даних для більшої області: {e}")
        sys.exit(1)

print("\nГенерація рельєфу...")
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

print(f"BBox (метри): {bbox_meters}")
print(f"Scale factor: {scale_factor}")
print(f"Water depth (метри): {water_depth_m}")

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
    print(f"Рельєф створено: {len(terrain_mesh.vertices)} вершин, {len(terrain_mesh.faces)} граней")
    print(f"Рельєф bounds: {terrain_mesh.bounds}")
    print(f"Рельєф min_z: {float(np.min(terrain_mesh.vertices[:, 2])):.2f}м, max_z: {float(np.max(terrain_mesh.vertices[:, 2])):.2f}м")
else:
    print("[ERROR] Рельєф не створено!")
    sys.exit(1)

# Обробка доріг
print("\nОбробка доріг...")
road_mesh = None
if terrain_provider is not None and G_roads is not None:
    merged_roads_geom = build_road_polygons(G_roads, road_width_multiplier)
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
        print(f"Дороги оброблено: {len(road_mesh.vertices)} вершин, {len(road_mesh.faces)} граней")
        print(f"Дороги bounds: {road_mesh.bounds}")
        print(f"Дороги min_z: {float(np.min(road_mesh.vertices[:, 2])):.2f}м, max_z: {float(np.max(road_mesh.vertices[:, 2])):.2f}м")
        
        # Перевіряємо накладання з рельєфом
        terrain_min_z = float(np.min(terrain_mesh.vertices[:, 2]))
        terrain_max_z = float(np.max(terrain_mesh.vertices[:, 2]))
        road_min_z = float(np.min(road_mesh.vertices[:, 2]))
        road_max_z = float(np.max(road_mesh.vertices[:, 2]))
        
        print(f"\nПеревірка накладання доріг на рельєф:")
        print(f"  Рельєф: z=[{terrain_min_z:.2f}, {terrain_max_z:.2f}]м")
        print(f"  Дороги: z=[{road_min_z:.2f}, {road_max_z:.2f}]м")
        
        if road_min_z < terrain_min_z - 1.0:
            print(f"  [WARN] Дороги занадто низько: {road_min_z:.2f}м < {terrain_min_z:.2f}м - 1.0м")
        if road_max_z > terrain_max_z + 5.0:
            print(f"  [WARN] Дороги занадто високо: {road_max_z:.2f}м > {terrain_max_z:.2f}м + 5.0м")
    else:
        print("[WARN] Дороги не оброблено")

# Обробка води
print("\nОбробка води...")
water_mesh = None
if terrain_provider is not None and gdf_water is not None and not gdf_water.empty:
    water_mesh = process_water_surface(
        gdf_water,
        thickness_m=0.001,
        depth_meters=float(water_depth_m) if water_depth_m is not None else 0.0,
        terrain_provider=terrain_provider,
    )
    
    if water_mesh:
        print(f"Вода оброблена: {len(water_mesh.vertices)} вершин, {len(water_mesh.faces)} граней")
        print(f"Вода bounds: {water_mesh.bounds}")
        print(f"Вода min_z: {float(np.min(water_mesh.vertices[:, 2])):.2f}м, max_z: {float(np.max(water_mesh.vertices[:, 2])):.2f}м")
        
        # Перевіряємо накладання з рельєфом
        terrain_min_z = float(np.min(terrain_mesh.vertices[:, 2]))
        terrain_max_z = float(np.max(terrain_mesh.vertices[:, 2]))
        water_min_z = float(np.min(water_mesh.vertices[:, 2]))
        water_max_z = float(np.max(water_mesh.vertices[:, 2]))
        
        print(f"\nПеревірка накладання води на рельєф:")
        print(f"  Рельєф: z=[{terrain_min_z:.2f}, {terrain_max_z:.2f}]м")
        print(f"  Вода: z=[{water_min_z:.2f}, {water_max_z:.2f}]м")
        
        if water_min_z < terrain_min_z - 1.0:
            print(f"  [WARN] Вода занадто низько: {water_min_z:.2f}м < {terrain_min_z:.2f}м - 1.0м")
        if water_max_z > terrain_max_z + 2.0:
            print(f"  [WARN] Вода занадто високо: {water_max_z:.2f}м > {terrain_max_z:.2f}м + 2.0м")
    else:
        print("[WARN] Вода не оброблена")

# Обробка будівель
print("\nОбробка будівель...")
building_meshes = []
if terrain_provider is not None and gdf_buildings is not None and not gdf_buildings.empty:
    building_meshes = process_buildings(
        gdf_buildings,
        min_height=building_min_height,
        height_multiplier=building_height_multiplier,
        terrain_provider=terrain_provider,
        foundation_depth=(float(building_foundation_mm) / float(scale_factor)) if scale_factor else 1.0,
        embed_depth=(float(building_embed_mm) / float(scale_factor)) if scale_factor else 0.0,
    )
    
    if building_meshes:
        print(f"Створено {len(building_meshes)} будівель")
        if len(building_meshes) > 0:
            combined_buildings = trimesh.util.concatenate([b for b in building_meshes if b is not None])
            if combined_buildings:
                print(f"Будівлі bounds: {combined_buildings.bounds}")
                print(f"Будівлі min_z: {float(np.min(combined_buildings.vertices[:, 2])):.2f}м, max_z: {float(np.max(combined_buildings.vertices[:, 2])):.2f}м")
                
                # Перевіряємо накладання з рельєфом
                terrain_min_z = float(np.min(terrain_mesh.vertices[:, 2]))
                terrain_max_z = float(np.max(terrain_mesh.vertices[:, 2]))
                building_min_z = float(np.min(combined_buildings.vertices[:, 2]))
                building_max_z = float(np.max(combined_buildings.vertices[:, 2]))
                
                print(f"\nПеревірка накладання будівель на рельєф:")
                print(f"  Рельєф: z=[{terrain_min_z:.2f}, {terrain_max_z:.2f}]м")
                print(f"  Будівлі: z=[{building_min_z:.2f}, {building_max_z:.2f}]м")
                
                if building_min_z < terrain_min_z - 2.0:
                    print(f"  [WARN] Будівлі занадто низько: {building_min_z:.2f}м < {terrain_min_z:.2f}м - 2.0м")

# Експорт
print("\nЕкспорт моделі...")
output_dir = Path("output")
output_dir.mkdir(exist_ok=True)

output_file = output_dir / "test_debug_model.3mf"
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


