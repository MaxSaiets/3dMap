"""
Скрипт для перевірки всіх згенерованих моделей
"""
import trimesh
from pathlib import Path
import os
from datetime import datetime

def check_model(filepath: Path, file_type: str = "stl"):
    """Перевіряє модель"""
    try:
        if file_type == "3mf":
            scene = trimesh.load_mesh(str(filepath))
            if isinstance(scene, trimesh.Scene):
                meshes = list(scene.geometry.values())
                if not meshes:
                    return None, "Сцена порожня"
                mesh = meshes[0]
            else:
                mesh = scene
        else:
            mesh = trimesh.load_mesh(str(filepath))
            if isinstance(mesh, trimesh.Scene):
                meshes = list(mesh.geometry.values())
                if not meshes:
                    return None, "Сцена порожня"
                mesh = trimesh.util.concatenate(meshes)
        
        bounds = mesh.bounds
        size = bounds[1] - bounds[0]
        center = mesh.centroid
        
        return {
            "file": filepath.name,
            "vertices": len(mesh.vertices),
            "faces": len(mesh.faces),
            "center": center.tolist(),
            "size": size.tolist(),
            "max_dimension": float(max(size)),
            "is_centered": all(abs(c) < 1.0 for c in center),
            "file_size": filepath.stat().st_size,
            "modified": datetime.fromtimestamp(filepath.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        }, None
    except Exception as e:
        return None, str(e)

if __name__ == "__main__":
    output_dir = Path("output")
    
    print("=" * 80)
    print("ПЕРЕВІРКА ВСІХ ЗГЕНЕРОВАНИХ МОДЕЛЕЙ")
    print("=" * 80)
    print()
    
    # Перевірка STL
    stl_files = sorted(output_dir.glob("*.stl"), key=os.path.getmtime, reverse=True)
    print(f"Знайдено {len(stl_files)} STL файлів")
    print()
    
    if stl_files:
        print("Останні 5 STL файлів:")
        print("-" * 80)
        for f in stl_files[:5]:
            result, error = check_model(f, "stl")
            if result:
                print(f"✅ {result['file']}")
                print(f"   Вершин: {result['vertices']}, Граней: {result['faces']}")
                print(f"   Розміри: {result['size'][0]:.2f} x {result['size'][1]:.2f} x {result['size'][2]:.2f} мм")
                print(f"   Центр: [{result['center'][0]:.6f}, {result['center'][1]:.6f}, {result['center'][2]:.6f}]")
                print(f"   Центрована: {'✅' if result['is_centered'] else '❌'}")
                print(f"   Розмір файлу: {result['file_size']:,} байт")
                print(f"   Створено: {result['modified']}")
            else:
                print(f"❌ {f.name}: {error}")
            print()
    
    # Перевірка 3MF
    mf_files = sorted(output_dir.glob("*.3mf"), key=os.path.getmtime, reverse=True)
    print(f"Знайдено {len(mf_files)} 3MF файлів")
    print()
    
    if mf_files:
        print("Останні 5 3MF файлів:")
        print("-" * 80)
        for f in mf_files[:5]:
            result, error = check_model(f, "3mf")
            if result:
                print(f"✅ {result['file']}")
                print(f"   Вершин: {result['vertices']}, Граней: {result['faces']}")
                print(f"   Розміри: {result['size'][0]:.2f} x {result['size'][1]:.2f} x {result['size'][2]:.2f} мм")
                print(f"   Центр: [{result['center'][0]:.6f}, {result['center'][1]:.6f}, {result['center'][2]:.6f}]")
                print(f"   Центрована: {'✅' if result['is_centered'] else '❌'}")
                print(f"   Розмір файлу: {result['file_size']:,} байт")
                print(f"   Створено: {result['modified']}")
            else:
                print(f"❌ {f.name}: {error}")
            print()
    
    # Статистика
    print("=" * 80)
    print("СТАТИСТИКА")
    print("=" * 80)
    
    all_stl = [check_model(f, "stl")[0] for f in stl_files if check_model(f, "stl")[0]]
    all_3mf = [check_model(f, "3mf")[0] for f in mf_files if check_model(f, "3mf")[0]]
    
    if all_stl:
        avg_vertices = sum(r['vertices'] for r in all_stl) / len(all_stl)
        avg_faces = sum(r['faces'] for r in all_stl) / len(all_stl)
        centered_count = sum(1 for r in all_stl if r['is_centered'])
        print(f"STL: Середня кількість вершин: {avg_vertices:.0f}, граней: {avg_faces:.0f}")
        print(f"      Правильно центрованих: {centered_count}/{len(all_stl)}")
    
    if all_3mf:
        avg_vertices = sum(r['vertices'] for r in all_3mf) / len(all_3mf)
        avg_faces = sum(r['faces'] for r in all_3mf) / len(all_3mf)
        centered_count = sum(1 for r in all_3mf if r['is_centered'])
        print(f"3MF: Середня кількість вершин: {avg_vertices:.0f}, граней: {avg_faces:.0f}")
        print(f"      Правильно центрованих: {centered_count}/{len(all_3mf)}")
    
    print()
    print("=" * 80)
    print("Перевірка завершена!")

