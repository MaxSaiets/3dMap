"""
Тестовий скрипт для показу СИРИХ доріг з OSM без обробки
Показує які дороги є в зоні та які з них мають теги bridge
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from services.data_loader import fetch_city_data
from services.global_center import GlobalCenter
import geopandas as gpd
from shapely.geometry import LineString, Point
import matplotlib.pyplot as plt

# Координати зони hex_34_32
bbox = {
    'north': 50.429427,
    'south': 50.420441,
    'east': 30.583167,
    'west': 30.570757
}

print("=" * 80)
print("ТЕСТ: Сирі дороги з OSM для зони hex_34_32")
print("=" * 80)

# Ініціалізація
center_lat = (bbox['north'] + bbox['south']) / 2
center_lon = (bbox['east'] + bbox['west']) / 2
GlobalCenter.initialize(center_lat, center_lon)

# Завантаження даних
print("\nЗавантаження даних...")
data = fetch_city_data(bbox, padding=0.002)

print(f"\nЗавантажено:")
print(f"  - {len(data['roads_graph'].edges())} доріг")
print(f"  - {len(data['water_geometries'])} водних об'єктів")

# Аналіз доріг
print("\n" + "=" * 80)
print("АНАЛІЗ ДОРІГ:")
print("=" * 80)

G = data['roads_graph']
water_geoms = data['water_geometries']

# Створюємо union води
from shapely.ops import unary_union
if len(water_geoms) > 0:
    water_union = unary_union([g['geometry'] for g in water_geoms])
    print(f"\nВода: {water_union.geom_type}, area={water_union.area:.2f}м²")
else:
    water_union = None
    print("\n⚠️ Немає води в зоні!")

# Аналізуємо кожну дорогу
bridges_osm = []
bridges_water = []
roads_touching_water = []
normal_roads = []

for i, (u, v, k, data_edge) in enumerate(G.edges(keys=True, data=True)):
    geom = data_edge.get('geometry')
    if geom is None:
        continue
    
    # Перевіряємо OSM теги
    has_bridge_tag = data_edge.get('bridge') in ['yes', 'viaduct', 'aqueduct']
    layer = data_edge.get('layer', 0)
    if isinstance(layer, str):
        try:
            layer = int(layer)
        except:
            layer = 0
    
    # Перевіряємо перетин з водою
    intersects_water = False
    intersection_length = 0.0
    if water_union is not None and geom.intersects(water_union):
        intersects_water = True
        intersection_length = geom.intersection(water_union).length
    
    # Класифікуємо
    if has_bridge_tag or layer >= 1:
        bridges_osm.append({
            'id': f"{u}-{v}-{k}",
            'geom': geom,
            'bridge_tag': data_edge.get('bridge'),
            'layer': layer,
            'intersects_water': intersects_water,
            'intersection_length': intersection_length,
            'highway': data_edge.get('highway'),
            'name': data_edge.get('name', 'unnamed')
        })
    elif intersects_water:
        if intersection_length >= 1.0:
            bridges_water.append({
                'id': f"{u}-{v}-{k}",
                'geom': geom,
                'intersection_length': intersection_length,
                'highway': data_edge.get('highway'),
                'name': data_edge.get('name', 'unnamed')
            })
        else:
            roads_touching_water.append({
                'id': f"{u}-{v}-{k}",
                'geom': geom,
                'intersection_length': intersection_length,
                'highway': data_edge.get('highway'),
                'name': data_edge.get('name', 'unnamed')
            })
    else:
        normal_roads.append({
            'id': f"{u}-{v}-{k}",
            'geom': geom,
            'highway': data_edge.get('highway'),
            'name': data_edge.get('name', 'unnamed')
        })

# Виводимо результати
print(f"\n📊 СТАТИСТИКА:")
print(f"  - Мости з OSM тегами: {len(bridges_osm)}")
print(f"  - Мости через воду (≥1м): {len(bridges_water)}")
print(f"  - Дороги торкаються води (<1м): {len(roads_touching_water)}")
print(f"  - Звичайні дороги: {len(normal_roads)}")

if bridges_osm:
    print(f"\n🌉 МОСТИ З OSM ТЕГАМИ:")
    for b in bridges_osm:
        print(f"  - {b['name']}: bridge={b['bridge_tag']}, layer={b['layer']}, "
              f"intersects_water={b['intersects_water']}, "
              f"intersection={b['intersection_length']:.2f}м")

if bridges_water:
    print(f"\n🌉 МОСТИ ЧЕРЕЗ ВОДУ (без OSM тегів):")
    for b in bridges_water:
        print(f"  - {b['name']}: intersection={b['intersection_length']:.2f}м, highway={b['highway']}")

if roads_touching_water:
    print(f"\n⚠️ ДОРОГИ ТОРКАЮТЬСЯ ВОДИ:")
    for r in roads_touching_water:
        print(f"  - {r['name']}: intersection={r['intersection_length']:.2f}м, highway={r['highway']}")

# Візуалізація
print(f"\n📊 Створюю візуалізацію...")
fig, ax = plt.subplots(figsize=(12, 10))

# Вода
if water_union is not None:
    if water_union.geom_type == 'Polygon':
        x, y = water_union.exterior.xy
        ax.fill(x, y, alpha=0.3, fc='blue', ec='blue', label='Water')
    elif water_union.geom_type == 'MultiPolygon':
        for poly in water_union.geoms:
            x, y = poly.exterior.xy
            ax.fill(x, y, alpha=0.3, fc='blue', ec='blue')

# Звичайні дороги
for r in normal_roads:
    x, y = r['geom'].xy
    ax.plot(x, y, 'gray', linewidth=0.5, alpha=0.5)

# Дороги торкаються води
for r in roads_touching_water:
    x, y = r['geom'].xy
    ax.plot(x, y, 'orange', linewidth=2, label='Touching water' if r == roads_touching_water[0] else '')

# Мости через воду
for b in bridges_water:
    x, y = b['geom'].xy
    ax.plot(x, y, 'green', linewidth=3, label='Bridge (water)' if b == bridges_water[0] else '')

# Мости з OSM
for b in bridges_osm:
    x, y = b['geom'].xy
    ax.plot(x, y, 'red', linewidth=3, label='Bridge (OSM)' if b == bridges_osm[0] else '')

ax.set_aspect('equal')
ax.legend()
ax.set_title(f'Дороги в зоні hex_34_32\nМости OSM: {len(bridges_osm)}, Мости води: {len(bridges_water)}')
ax.grid(True, alpha=0.3)

output_file = 'h:\\3dMap\\backend\\output\\roads_analysis.png'
plt.savefig(output_file, dpi=150, bbox_inches='tight')
print(f"✅ Збережено: {output_file}")

print("\n" + "=" * 80)
print("ГОТОВО!")
print("=" * 80)
