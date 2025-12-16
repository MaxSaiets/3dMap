"""
Скрипт для генерації тестової моделі центру Києва (1км x 1км)
"""
import sys
from pathlib import Path

# Додаємо поточну директорію до шляху
sys.path.insert(0, str(Path(__file__).parent))

from services.data_loader import fetch_city_data
from services.road_processor import process_roads, build_road_polygons
from services.building_processor import process_buildings
from services.water_processor import process_water
from services.water_processor import process_water_surface
from services.terrain_generator import create_terrain_mesh
from services.extras_loader import fetch_extras
from services.green_processor import process_green_areas
from services.poi_processor import process_pois
from services.model_exporter import export_scene
from services.model_exporter import export_preview_parts_stl
import trimesh
from pathlib import Path

# Центр Києва
KYIV_CENTER_LAT = 50.4501
KYIV_CENTER_LON = 30.5234

# 1 км = приблизно 0.009 градусів на широті
# 1 км = приблизно 0.009 / cos(latitude) градусів на довготі
KM_TO_DEGREES_LAT = 0.009
KM_TO_DEGREES_LON = 0.009 / 0.64  # cos(50°) ≈ 0.64

# Область 1км x 1км
HALF_KM = 0.5
north = KYIV_CENTER_LAT + HALF_KM * KM_TO_DEGREES_LAT
south = KYIV_CENTER_LAT - HALF_KM * KM_TO_DEGREES_LAT
east = KYIV_CENTER_LON + HALF_KM * KM_TO_DEGREES_LON
west = KYIV_CENTER_LON - HALF_KM * KM_TO_DEGREES_LON

print("=" * 80)
print("ГЕНЕРАЦІЯ ТЕСТОВОЇ МОДЕЛІ ЦЕНТРУ КИЄВА")
print("=" * 80)
print(f"Координати:")
print(f"  Північ: {north:.6f}")
print(f"  Південь: {south:.6f}")
print(f"  Схід: {east:.6f}")
print(f"  Захід: {west:.6f}")
print(f"Розмір: ~1км x 1км")
print()

# Параметри генерації
road_width_multiplier = 1.0
road_height_mm = 0.5
road_embed_mm = 0.3
building_min_height = 2.0
building_height_multiplier = 1.0
building_foundation_mm = 0.6
building_embed_mm = 0.2
include_parks = True
parks_height_mm = 0.6
parks_embed_mm = 0.2
include_pois = True
poi_size_mm = 0.6
poi_height_mm = 0.8
poi_embed_mm = 0.2
water_depth = 2.0
terrain_enabled = True
terrain_z_scale = 1.5
terrain_base_thickness_mm = 2.0
terrain_resolution = 200
terrarium_zoom = 15
terrain_smoothing_sigma = 0.6
export_format = "3mf"
model_size_mm = 100.0  # 10 см

print("Завантаження даних OSM...")
gdf_buildings, gdf_water, G_roads = fetch_city_data(north, south, east, west)
print(f"Завантажено: {len(gdf_buildings)} будівель, {len(gdf_water)} водних об'єктів")

print("Обробка доріг...")
merged_roads_geom = build_road_polygons(G_roads, road_width_multiplier)
road_mesh = process_roads(G_roads, road_width_multiplier, merged_roads=merged_roads_geom)
if road_mesh:
    print(f"Дороги оброблено: {len(road_mesh.vertices)} вершин, {len(road_mesh.faces)} граней")

print("Обробка будівель...")
building_meshes = process_buildings(
    gdf_buildings,
    min_height=building_min_height,
    height_multiplier=building_height_multiplier
)
print(f"Створено {len(building_meshes)} будівель")

print("Обробка води...")
water_mesh = None
if not gdf_water.empty:
    water_mesh = process_water(gdf_water, depth=water_depth)
    if water_mesh:
        print(f"Вода оброблена: {len(water_mesh.vertices)} вершин, {len(water_mesh.faces)} граней")

print("Завантаження extra layers (парки/POI)...")
gdf_green, gdf_pois = fetch_extras(north, south, east, west)
parks_mesh = None
poi_mesh = None

print("Генерація рельєфу...")
terrain_mesh = None
terrain_provider = None
if terrain_enabled:
    # ВАЖЛИВО: рельєф будуємо в UTM, як і OSM-геометрія
    if not gdf_buildings.empty:
        minx, miny, maxx, maxy = gdf_buildings.total_bounds
    else:
        # fallback на дороги
        import osmnx as ox
        gdf_edges = None
        if G_roads is not None:
            if hasattr(G_roads, "total_bounds"):
                gdf_edges = G_roads
            else:
                gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)
        minx, miny, maxx, maxy = gdf_edges.total_bounds if gdf_edges is not None and not gdf_edges.empty else (-500, -500, 500, 500)
    bbox_meters = (float(minx), float(miny), float(maxx), float(maxy))
    bbox_degrees = (north, south, east, west)
    # scale_factor: meters -> model millimeters
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
        else:
            source_crs = None
    except Exception:
        source_crs = None
    # water depth in meters before scaling
    water_depth_m = (float(water_depth) / float(scale_factor)) if scale_factor else None

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
    )
    if terrain_mesh:
        print(f"Рельєф створено: {len(terrain_mesh.vertices)} вершин, {len(terrain_mesh.faces)} граней")
    
    # Переробляємо дороги та будівлі з урахуванням рельєфу
    if terrain_provider is not None:
        print("Проекція доріг на рельєф...")
        road_mesh = process_roads(
            G_roads,
            road_width_multiplier,
            terrain_provider=terrain_provider,
            road_height=(float(road_height_mm) / float(scale_factor)) if scale_factor else 0.8,
            road_embed=(float(road_embed_mm) / float(scale_factor)) if scale_factor else 0.0,
            merged_roads=merged_roads_geom,
        )
        if road_mesh:
            print(f"Дороги оброблено (з рельєфом): {len(road_mesh.vertices)} вершин, {len(road_mesh.faces)} граней")
        
        print("Посадка будівель на рельєф...")
        building_meshes = process_buildings(
            gdf_buildings,
            min_height=building_min_height,
            height_multiplier=building_height_multiplier,
            terrain_provider=terrain_provider,
            foundation_depth=(float(building_foundation_mm) / float(scale_factor)) if scale_factor else 1.0,
            embed_depth=(float(building_embed_mm) / float(scale_factor)) if scale_factor else 0.0,
        )
        print(f"Створено {len(building_meshes)} будівель (на рельєфі)")

        if not gdf_water.empty:
            print("Вода: depression в рельєф + surface для превʼю/3MF...")
            if scale_factor and water_depth_m is not None:
                surface_mm = float(min(max(water_depth, 0.2), 0.6))
                thickness_m = float(surface_mm) / float(scale_factor)
            else:
                thickness_m = 0.001
            water_mesh = process_water_surface(
                gdf_water,
                thickness_m=float(thickness_m),
                depth_meters=float(water_depth_m) if water_depth_m is not None else 0.0,
                terrain_provider=terrain_provider,
            )

        if include_parks and gdf_green is not None and not gdf_green.empty and scale_factor:
            print("Проекція парків на рельєф...")
            parks_mesh = process_green_areas(
                gdf_green,
                height_m=float(parks_height_mm) / float(scale_factor),
                embed_m=float(parks_embed_mm) / float(scale_factor),
                terrain_provider=terrain_provider,
            )

        if include_pois and gdf_pois is not None and not gdf_pois.empty and scale_factor:
            print("POI (лавочки/фонтани) на рельєф...")
            poi_mesh = process_pois(
                gdf_pois,
                size_m=float(poi_size_mm) / float(scale_factor),
                height_m=float(poi_height_mm) / float(scale_factor),
                embed_m=float(poi_embed_mm) / float(scale_factor),
                terrain_provider=terrain_provider,
            )

print("Експорт моделі...")
output_dir = Path("output")
output_dir.mkdir(exist_ok=True)

# Експортуємо і 3MF і STL для надійності
output_file_3mf = output_dir / "test_model_kyiv.3mf"
output_file_stl = output_dir / "test_model_kyiv.stl"
output_file_3mf_abs = output_file_3mf.resolve()
output_file_stl_abs = output_file_stl.resolve()

# Експорт 3MF
export_scene(
    terrain_mesh=terrain_mesh,
    road_mesh=road_mesh,
    building_meshes=building_meshes,
    water_mesh=water_mesh,
    parks_mesh=parks_mesh,
    poi_mesh=poi_mesh,
    filename=str(output_file_3mf_abs),
    format="3mf",
    model_size_mm=model_size_mm,
    add_flat_base=not terrain_enabled,
    base_thickness_mm=2.0,
)

# Експорт STL (для надійності)
export_scene(
    terrain_mesh=terrain_mesh,
    road_mesh=road_mesh,
    building_meshes=building_meshes,
    water_mesh=water_mesh,
    parks_mesh=parks_mesh,
    poi_mesh=poi_mesh,
    filename=str(output_file_stl_abs),
    format="stl",
    model_size_mm=model_size_mm,
    add_flat_base=not terrain_enabled,
    base_thickness_mm=2.0,
)

# Експорт STL частин для кольорового прев'ю
print("Експорт STL частин для прев'ю...")
preview_items = []
if terrain_mesh is not None:
    preview_items.append(("Base", terrain_mesh))
if road_mesh is not None:
    preview_items.append(("Roads", road_mesh))
if building_meshes:
    try:
        combined_buildings = trimesh.util.concatenate([b for b in building_meshes if b is not None])
        if combined_buildings is not None and len(combined_buildings.vertices) > 0:
            preview_items.append(("Buildings", combined_buildings))
    except Exception as e:
        print("Попередження: не вдалося об'єднати будівлі для прев'ю:", e)
if water_mesh is not None:
    preview_items.append(("Water", water_mesh))
if parks_mesh is not None:
    preview_items.append(("Parks", parks_mesh))
if poi_mesh is not None:
    preview_items.append(("POI", poi_mesh))

if preview_items:
    export_preview_parts_stl(
        output_prefix=str((output_dir / "test_model_kyiv").resolve()),
        mesh_items=preview_items,
        model_size_mm=model_size_mm,
        add_flat_base=not terrain_enabled,
        base_thickness_mm=2.0,
        rotate_to_ground=False,
    )

print()
print("=" * 80)
print("ТЕСТОВА МОДЕЛЬ ГОТОВА!")
print("=" * 80)
print(f"3MF файл: {output_file_3mf_abs}")
print(f"Розмір: {output_file_3mf_abs.stat().st_size:,} байт")
print(f"STL файл: {output_file_stl_abs}")
print(f"Розмір: {output_file_stl_abs.stat().st_size:,} байт")
print()
print("Модель збережена як test_model_kyiv.3mf та test_model_kyiv.stl")
print("Вона буде автоматично завантажена при старті додатка")

