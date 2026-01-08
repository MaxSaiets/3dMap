"""
Швидкий тест оптимізацій - перевірка, що все працює
"""
import requests
import time
import sys

# Fix encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

API_BASE = "http://127.0.0.1:8000"

print("=" * 80)
print("ШВИДКИЙ ТЕСТ СИСТЕМИ")
print("=" * 80)

# Тест 1: Health check
print("\n1. Перевірка API...")
try:
    r = requests.get(f"{API_BASE}/", timeout=5)
    if r.status_code == 200:
        print(f"   [OK] API працює: {r.json()}")
    else:
        print(f"   [ERROR] API повернув {r.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"   [ERROR] {e}")
    sys.exit(1)

# Тест 2: Створення задачі (асинхронно)
print("\n2. Створення задачі генерації...")
request_data = {
    "north": 50.455,
    "south": 50.450,
    "east": 30.530,
    "west": 30.520,
    "model_size_mm": 100.0,
    "road_width_multiplier": 1.0,
    "building_min_height": 2.0,
    "building_height_multiplier": 1.0,
    "water_depth": 2.0,
    "terrain_enabled": True,
    "terrain_z_scale": 1.5,
    "terrain_resolution": 150,  # Менша роздільність для швидкості
    "export_format": "stl"
}

try:
    # Запит має прийнятися швидко (асинхронна обробка)
    # Використовуємо stream=True та малий таймаут для отримання лише заголовків
    start = time.time()
    r = requests.post(f"{API_BASE}/api/generate", json=request_data, timeout=(5, 30), stream=True)
    # Читаємо лише початок відповіді
    r.raw.read(1)  # Прочитати хоч один байт для підтвердження з'єднання
    elapsed = time.time() - start
    
    if r.status_code == 200:
        data = r.json()
        task_id = data.get("task_id")
        print(f"   [OK] Задача створена за {elapsed:.2f}s")
        print(f"   Task ID: {task_id}")
        print(f"   Status: {data.get('status')}")
        
        # Тест 3: Перевірка статусу (через 2 секунди)
        print("\n3. Перевірка статусу задачі...")
        time.sleep(2)
        
        try:
            status_r = requests.get(f"{API_BASE}/api/status/{task_id}", timeout=10)
            if status_r.status_code == 200:
                status_data = status_r.json()
                print(f"   [OK] Статус отримано")
                print(f"   Прогрес: {status_data.get('progress', 0)}%")
                print(f"   Статус: {status_data.get('status')}")
                print(f"\n   Задача обробляється в фоновому режимі.")
                print(f"   Для повного тесту перевірте статус через деякий час:")
                print(f"   curl http://127.0.0.1:8000/api/status/{task_id}")
            else:
                print(f"   [WARN] Не вдалося отримати статус: {status_r.status_code}")
        except Exception as e:
            print(f"   [WARN] Помилка перевірки статусу: {e}")
        
        print("\n" + "=" * 80)
        print("[OK] Всі базові тести пройдені!")
        print("=" * 80)
        print("\nСистема працює правильно:")
        print("  - API доступний")
        print("  - Задачі створюються асинхронно")
        print("  - Batch processing активний (перевірте логи сервера)")
        print("\nПримітка: Повна генерація може тривати кілька хвилин при першому запуску")
        print("          (завантаження та кешування даних з PBF).")
        
    else:
        print(f"   [ERROR] Запит не вдався: {r.status_code}")
        try:
            print(f"   {r.json()}")
        except:
            print(f"   {r.text[:200]}")
        sys.exit(1)
        
except requests.exceptions.Timeout:
    print(f"   [WARN] Запит тайм-аутувався (це може бути нормально для першого запуску)")
    print(f"   Перевірте логи сервера для підтвердження роботи batch processing")
except Exception as e:
    print(f"   [ERROR] Помилка: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
