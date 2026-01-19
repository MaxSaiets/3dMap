"""
Генерація ТІЛЬКИ доріг в STL без обробки
Просто екструдує всі дороги на висоту 1м
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from services.data_loader import fetch_city_data
from services.global_center import GlobalCenter
import trimesh
from shapely.geometry import LineString
import numpy as np

# Координати зони hex_34_32
bbox = {
    'north': 50.429427,
    'south': 50.420441,
    'east': 30.583167,
    'west': 30.570757
}

print("=" * 80)
print("ГЕНЕРАЦІЯ СИРИХ ДОРІГ В STL")
print("=" * 80)

# Ініціалізація
center_lat = (bbox['north'] + bbox['south']) / 2
center_lon = (bbox['east'] + bbox['west']) / 2
GlobalCenter(center_lat, center_lon)

# Завантаження даних
print("\nЗавантаження даних...")
data = fetch_city_data(
    north=bbox['north'],
    south=bbox['south'],
    east=bbox['east'],
    west=bbox['west'],
    padding=0.002
)

buildings, water_geoms, G = data
print(f"Завантажено {len(G.edges())} доріг")

# Створюємо меші для всіх доріг
road_meshes = []
road_width = 5.0  # метрів
road_height = 1.0  # метрів

print("\nГенерація мешів доріг...")
for i, (u, v, k, data_edge) in enumerate(G.edges(keys=True, data=True)):
    geom = data_edge.get('geometry')
    if geom is None or not isinstance(geom, LineString):
        continue
    
    try:
        # Буферизуємо лінію до полігону
        poly = geom.buffer(road_width / 2.0, cap_style=2)  # cap_style=2 = flat ends
        
        if poly.is_empty or poly.area < 0.1:
            continue
        
        # Екструдуємо
        mesh = trimesh.creation.extrude_polygon(poly, height=road_height)
        
        if mesh is None or len(mesh.vertices) == 0:
            continue
        
        road_meshes.append(mesh)
        
        if (i + 1) % 50 == 0:
            print(f"  Оброблено {i + 1}/{len(G.edges())} доріг...")
    
    except Exception as e:
        print(f"  Помилка на дорозі {i}: {e}")
        continue

print(f"\n✅ Створено {len(road_meshes)} мешів доріг")

if len(road_meshes) == 0:
    print("❌ Немає доріг для експорту!")
    sys.exit(1)

# Об'єднуємо всі меші
print("\nОб'єднання мешів...")
combined = trimesh.util.concatenate(road_meshes)
print(f"Об'єднано: {len(combined.vertices)} вершин, {len(combined.faces)} граней")
print(f"Bounds: {combined.bounds}")
print(f"Z range: {combined.bounds[0][2]:.2f} to {combined.bounds[1][2]:.2f} метрів")

# Експортуємо
output_file = r"H:\3dMap\backend\output\RAW_ROADS_ONLY.stl"
combined.export(output_file)

file_size = os.path.getsize(output_file)
print(f"\n✅ Експортовано: {output_file}")
print(f"Розмір файлу: {file_size:,} байт")

print("\n" + "=" * 80)
print("ГОТОВО! Відкрийте RAW_ROADS_ONLY.stl в PrusaSlicer")
print("Модель в МЕТРАХ! Масштабуйте на 10.718% для правильного розміру")
print("=" * 80)
