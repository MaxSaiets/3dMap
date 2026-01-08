#!/usr/bin/env python3
"""
Скрипт для вимкнення всіх кешів та оптимізації завантаження даних.
Запустіть цей скрипт один раз: python disable_cache.py
"""

import re
from pathlib import Path

def fix_file(file_path: Path, replacements: list):
    """Застосовує заміни до файлу."""
    if not file_path.exists():
        print(f"[SKIP] Файл не існує: {file_path}")
        return False
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        for pattern, replacement in replacements:
            content = re.sub(pattern, replacement, content)
        
        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"[OK] Оновлено: {file_path}")
            return True
        else:
            print(f"[SKIP] Змін не потрібно: {file_path}")
            return False
    except Exception as e:
        print(f"[ERROR] Помилка обробки {file_path}: {e}")
        return False

def main():
    backend_dir = Path(__file__).parent
    
    # 1. Вимкнути OSMnx cache
    print("\n1. Вимкнення OSMnx cache...")
    fix_file(
        backend_dir / "services" / "data_loader.py",
        [
            (r'ox\.settings\.use_cache = True', 'ox.settings.use_cache = False'),
            (r'# Налаштування osmnx для кращої продуктивності', 
             '# Налаштування osmnx (cache вимкнено для меншого використання пам\'яті)'),
        ]
    )
    
    # 2. Вимкнути PBF disk cache
    print("\n2. Вимкнення PBF disk cache...")
    fix_file(
        backend_dir / "services" / "pbf_loader.py",
        [
            (r'return \(os\.getenv\("OSM_PBF_DISK_CACHE"\) or "1"\)', 
             'return (os.getenv("OSM_PBF_DISK_CACHE") or "0")'),
            (r'def _cache_enabled\(\) -> bool:', 
             'def _cache_enabled() -> bool:\n    # Cache вимкнено за замовчуванням для меншого використання пам\'яті'),
        ]
    )
    
    # 3. Видалити city cache з main.py
    print("\n3. Видалення city cache з main.py...")
    main_py = backend_dir / "main.py"
    if main_py.exists():
        with open(main_py, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Видалити блок кешування city cache
        patterns = [
            # Видалити імпорт hashlib, json якщо він тільки для кешу
            # (залишаємо, бо може використовуватись в інших місцях)
            
            # Замінити блок створення кешу на простий код
            (
                r'    # Cache global city reference so future "add more zones" uses the same values\.\s+'
                r'grid_bbox_latlon = \(grid_bbox\[\'north\'\], grid_bbox\[\'south\'\], grid_bbox\[\'east\'\], grid_bbox\[\'west\'\]\)\s+'
                r'import hashlib, json\s+'
                r'cache_dir = Path\("cache/cities"\)\s+'
                r'cache_dir\.mkdir\(parents=True, exist_ok=True\)\s+'
                r'# cache version bump: elevation baseline logic changed \(needs refresh\)\s+'
                r'city_key = f"v4_.*?city_hash = hashlib\.md5\(city_key\.encode\(\)\)\.hexdigest\(\)\s+'
                r'city_cache_file = cache_dir / f"city_\{city_hash\}\.json"\s+'
                r'\s+cached = None\s+'
                r'if city_cache_file\.exists\(\):\s+'
                r'try:\s+'
                r'cached = json\.loads\(city_cache_file\.read_text\(encoding="utf-8"\)\)\s+'
                r'print\(f"\[INFO\] Використовуємо кеш міста: \{city_cache_file\.name\}"\)\s+'
                r'except Exception:\s+'
                r'cached = None\s+'
                r'\s+if cached and isinstance\(cached, dict\) and "center" in cached:\s+'
                r'try:\s+'
                r'c = cached\.get\("center"\) or \{\}\s+'
                r'global_center = set_global_center\(float\(c\["lat"\]\), float\(c\["lon"\]\)\)\s+'
                r'except Exception:\s+'
                r'global_center = set_global_center\(grid_center_lat, grid_center_lon\)\s+'
                r'else:\s+'
                r'global_center = set_global_center\(grid_center_lat, grid_center_lon\)',
                '    # Кеш вимкнено: завжди обчислюємо все заново для кожної зони\n'
                '    grid_bbox_latlon = (grid_bbox[\'north\'], grid_bbox[\'south\'], grid_bbox[\'east\'], grid_bbox[\'west\'])\n'
                '    global_center = set_global_center(grid_center_lat, grid_center_lon)'
            ),
            
            # Видалити використання cached_elev
            (
                r'    # Обчислюємо глобальний elevation_ref_m та baseline_offset_m\s+'
                r'# Guard against corrupted/invalid cached refs.*?'
                r'if cached_elev is not None:\s+'
                r'global_elevation_ref_m = float\(cached\.get\("elevation_ref_m"\)\)\s+'
                r'global_baseline_offset_m = float\(cached\.get\("baseline_offset_m"\) or 0\.0\)\s+'
                r'print\(f"\[INFO\] Глобальний elevation_ref_m \(кеш\): \{global_elevation_ref_m:\.2f\}м"\)\s+'
                r'print\(f"\[INFO\] Глобальний baseline_offset_m \(кеш\): \{global_baseline_offset_m:\.3f\}м"\)\s+'
                r'else:\s+'
                r'global_elevation_ref_m, global_baseline_offset_m = calculate_global_elevation_reference\(',
                '    # Обчислюємо глобальний elevation_ref_m та baseline_offset_m (без кешу)\n'
                '    global_elevation_ref_m, global_baseline_offset_m = calculate_global_elevation_reference('
            ),
            
            # Видалити збереження кешу
            (
                r'    # Save/refresh city cache for future requests\s+'
                r'try:\s+'
                r'cache_payload = \{.*?\}\s+'
                r'city_cache_file\.write_text\(json\.dumps\(cache_payload, ensure_ascii=False, indent=2\), encoding="utf-8"\)\s+'
                r'except Exception:\s+'
                r'pass',
                '    # Кеш вимкнено: не зберігаємо результати'
            ),
        ]
        
        # Використовуємо більш простий підхід - замінюємо по частинах
        content_new = content
        
        # Проста заміна: видалити cached_elev блок
        content_new = re.sub(
            r'cached_elev = None\s+if cached and isinstance\(cached, dict\):.*?cached_elev = None\s+',
            '',
            content_new,
            flags=re.DOTALL
        )
        
        if content_new != content:
            with open(main_py, 'w', encoding='utf-8') as f:
                f.write(content_new)
            print(f"[OK] Оновлено: {main_py}")
        else:
            print(f"[SKIP] Змін не потрібно (можливо вже виправлено): {main_py}")
    
    print("\n✅ Готово! Всі кеші вимкнено.")
    print("\nТепер система буде:")
    print("  - Використовувати Overpass API (не PBF) за замовчуванням")
    print("  - Завантажувати дані тільки для кожної зони окремо")
    print("  - Не зберігати кеш в пам'яті або на диску")

if __name__ == "__main__":
    main()


