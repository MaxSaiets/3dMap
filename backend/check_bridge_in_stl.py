"""
Скрипт для перевірки наявності моста в STL файлі
"""
import trimesh
import sys

stl_file = r"H:\3dMap\backend\output\7e514567-8aa6-47cf-986a-435d5b6265d9.stl"

print(f"Читаю файл: {stl_file}")
mesh = trimesh.load(stl_file)

print(f"\nМеш: {len(mesh.vertices)} вершин, {len(mesh.faces)} граней")
print(f"Bounds: {mesh.bounds}")
print(f"Z range: {mesh.bounds[0][2]:.2f} to {mesh.bounds[1][2]:.2f} метрів")

# Перевіряємо, чи є вершини на висоті моста (17-22м)
bridge_vertices = mesh.vertices[(mesh.vertices[:, 2] >= 17) & (mesh.vertices[:, 2] <= 22)]
print(f"\nВершини на висоті 17-22м (міст): {len(bridge_vertices)}")

if len(bridge_vertices) > 0:
    print("✅ МІСТ Є В ФАЙЛІ!")
    print(f"Bounds моста: Z from {bridge_vertices[:, 2].min():.2f} to {bridge_vertices[:, 2].max():.2f}")
else:
    print("❌ МОСТА НЕМАЄ!")

# Показуємо розподіл вершин по висоті
import numpy as np
z_values = mesh.vertices[:, 2]
print(f"\nРозподіл вершин по висоті:")
print(f"  Min Z: {z_values.min():.2f}м")
print(f"  25%: {np.percentile(z_values, 25):.2f}м")
print(f"  50% (median): {np.percentile(z_values, 50):.2f}м")
print(f"  75%: {np.percentile(z_values, 75):.2f}м")
print(f"  Max Z: {z_values.max():.2f}м")
