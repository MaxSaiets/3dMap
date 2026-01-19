"""
Тестовий скрипт для генерації ТІЛЬКИ доріг (включаючи мости)
без terrain, water, buildings - щоб перевірити чи міст генерується
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from services.road_processor import process_roads
from services.data_loader import fetch_city_data
from services.global_center import GlobalCenter
import trimesh

# Координати зони з мостом
bbox = {
    'north': 50.429427,
    'south': 50.420441,
    'east': 30.583167,
    'west': 30.570757
}

print("=" * 60)
print("ТЕСТ: Генерація ТІЛЬКИ доріг (з мостами)")
print("=" * 60)

# Ініціалізація глобального центру
center_lat = (bbox['north'] + bbox['south']) / 2
center_lon = (bbox['east'] + bbox['west']) / 2
GlobalCenter.initialize(center_lat, center_lon)
print(f"Глобальний центр: {center_lat}, {center_lon}")

# Завантаження даних
print("\nЗавантаження даних...")
data = fetch_city_data(bbox, padding=0.002)
print(f"Завантажено: {len(data['roads_graph'].edges())} доріг, {len(data['water_geometries'])} водних об'єктів")

# Генерація доріг
print("\nГенерація доріг...")
road_mesh = process_roads(
    G_roads=data['roads_graph'],
    water_geometries=data['water_geometries'],
    terrain_provider=None,  # БЕЗ terrain!
    road_height=1.0,
    scale_factor=0.107180,
    city_cache_key=None
)

if road_mesh is None:
    print("❌ ПОМИЛКА: road_mesh is None!")
    sys.exit(1)

print(f"\n✅ Дороги згенеровано: {len(road_mesh.vertices)} вершин, {len(road_mesh.faces)} граней")
print(f"Bounds: {road_mesh.bounds}")
print(f"Z range: {road_mesh.bounds[0][2]:.2f} to {road_mesh.bounds[1][2]:.2f}")

# Експорт
output_file = "h:\\3dMap\\backend\\output\\test_roads_only.stl"
road_mesh.export(output_file)
print(f"\n✅ Експортовано: {output_file}")
print(f"Розмір файлу: {os.path.getsize(output_file)} байт")

print("\n" + "=" * 60)
print("ГОТОВО! Відкрийте test_roads_only.stl в PrusaSlicer")
print("=" * 60)
