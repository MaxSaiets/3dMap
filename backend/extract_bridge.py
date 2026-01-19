"""
Експорт ТІЛЬКИ моста з STL файлу
"""
import trimesh
import numpy as np

stl_file = r"H:\3dMap\backend\output\7e514567-8aa6-47cf-986a-435d5b6265d9.stl"
output_file = r"H:\3dMap\backend\output\BRIDGE_ONLY.stl"

print(f"Читаю файл: {stl_file}")
mesh = trimesh.load(stl_file)

# Фільтруємо тільки вершини моста (17-22м)
print("Фільтрую міст...")

# Знаходимо грані, які мають хоча б одну вершину на висоті моста
bridge_faces = []
for i, face in enumerate(mesh.faces):
    vertices = mesh.vertices[face]
    if np.any((vertices[:, 2] >= 17) & (vertices[:, 2] <= 22)):
        bridge_faces.append(face)

print(f"Знайдено {len(bridge_faces)} граней моста")

if len(bridge_faces) == 0:
    print("❌ Грані моста не знайдено!")
    exit(1)

# Створюємо новий меш тільки з мостом
bridge_faces = np.array(bridge_faces)
bridge_mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=bridge_faces, process=False)
bridge_mesh.remove_unreferenced_vertices()

print(f"Міст: {len(bridge_mesh.vertices)} вершин, {len(bridge_mesh.faces)} граней")
print(f"Bounds: Z from {bridge_mesh.bounds[0][2]:.2f} to {bridge_mesh.bounds[1][2]:.2f}")

# Експортуємо
bridge_mesh.export(output_file)
print(f"\n✅ Експортовано: {output_file}")
print("Відкрийте цей файл в PrusaSlicer - там ТІЛЬКИ міст!")
