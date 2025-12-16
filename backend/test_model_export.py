"""
Скрипт для тестування та перевірки експортованої моделі
"""
import trimesh
import sys
from pathlib import Path

def check_stl_file(filepath: str):
    """Перевіряє STL файл"""
    print(f"\nПеревірка файлу: {filepath}")
    print("=" * 60)
    
    try:
        # Завантажуємо модель
        mesh = trimesh.load(filepath)
        
        if isinstance(mesh, trimesh.Scene):
            print(f"Сцена містить {len(mesh.geometry)} об'єктів")
            # Об'єднуємо всі об'єкти
            meshes = list(mesh.geometry.values())
            if meshes:
                mesh = trimesh.util.concatenate(meshes)
            else:
                print("ПОМИЛКА: Сцена порожня!")
                return False
        elif isinstance(mesh, trimesh.Trimesh):
            print("Завантажено один меш")
        else:
            print(f"Невідомий тип: {type(mesh)}")
            return False
        
        # Перевіряємо основні параметри
        print(f"\nСтатистика моделі:")
        print(f"  Вершини: {len(mesh.vertices)}")
        print(f"  Грані: {len(mesh.faces)}")
        print(f"  Ребра: {len(mesh.edges)}")
        
        if len(mesh.vertices) == 0:
            print("ПОМИЛКА: Модель не має вершин!")
            return False
        
        if len(mesh.faces) == 0:
            print("ПОМИЛКА: Модель не має граней!")
            return False
        
        # Перевіряємо розміри
        bounds = mesh.bounds
        size = bounds[1] - bounds[0]
        center = mesh.centroid
        
        print(f"\nРозміри:")
        print(f"  Мінімум: {bounds[0]}")
        print(f"  Максимум: {bounds[1]}")
        print(f"  Розмір: {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f}")
        print(f"  Центр: {center}")
        
        # Перевіряємо нормалі
        print(f"\nНормалі:")
        print(f"  Валідні: {mesh.is_watertight}")
        print(f"  Об'єм: {mesh.volume:.2f}")
        
        # Перевіряємо проблеми
        issues = []
        if max(size) < 0.001:
            issues.append("Модель занадто мала")
        if max(size) > 10000:
            issues.append("Модель занадто велика")
        if not mesh.is_watertight:
            issues.append("Модель не watertight (може бути нормально)")
        if mesh.volume == 0:
            issues.append("Модель має нульовий об'єм")
        
        if issues:
            print(f"\nПопередження:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("\nМодель виглядає валідною!")
        
        # Перевіряємо, чи модель видима
        if max(size) > 0.001 and max(size) < 10000:
            print("\nМодель має розумні розміри для перегляду")
        else:
            print("\nУВАГА: Модель може не відображатися через проблеми з масштабом!")
        
        return True
        
    except Exception as e:
        print(f"ПОМИЛКА при завантаженні: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Шукаємо останній STL файл
    output_dir = Path("output")
    if not output_dir.exists():
        print("Директорія output не існує!")
        sys.exit(1)
    
    stl_files = list(output_dir.glob("*.stl"))
    if not stl_files:
        print("Не знайдено STL файлів!")
        sys.exit(1)
    
    # Беремо останній файл
    latest_file = max(stl_files, key=lambda p: p.stat().st_mtime)
    
    print(f"Знайдено {len(stl_files)} STL файлів")
    print(f"Перевіряю останній: {latest_file.name}")
    
    success = check_stl_file(str(latest_file))
    
    if success:
        print("\n" + "=" * 60)
        print("Перевірка завершена успішно!")
    else:
        print("\n" + "=" * 60)
        print("Знайдено проблеми з моделлю!")
        sys.exit(1)

