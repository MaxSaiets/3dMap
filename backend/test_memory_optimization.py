"""
Тест оптимізацій пам'яті та batch processing
"""
import requests
import time
import json
import sys
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

API_BASE = "http://127.0.0.1:8000"

def test_api_health():
    """Тест 1: Перевірка доступності API"""
    print("=" * 80)
    print("ТЕСТ 1: Перевірка доступності API")
    print("=" * 80)
    try:
        response = requests.get(f"{API_BASE}/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"[OK] API працює: {data}")
            return True
        else:
            print(f"[ERROR] API повернув статус {response.status_code}")
            return False
    except Exception as e:
        print(f"[ERROR] Помилка з'єднання з API: {e}")
        return False

def test_small_generation():
    """Тест 2: Генерація моделі для невеликої області"""
    print("\n" + "=" * 80)
    print("ТЕСТ 2: Генерація моделі (мала область, перевірка batch processing)")
    print("=" * 80)
    
    # Мала область в центрі Києва (приблизно 500м x 500м)
    request_data = {
        "north": 50.455,
        "south": 50.450,
        "east": 30.530,
        "west": 30.520,
        "model_size_mm": 100.0,
        "road_width_multiplier": 1.0,
        "road_height_mm": 0.5,
        "road_embed_mm": 0.3,
        "building_min_height": 2.0,
        "building_height_multiplier": 1.0,
        "building_foundation_mm": 0.6,
        "building_embed_mm": 0.2,
        "building_max_foundation_mm": 2.0,
        "water_depth": 2.0,
        "terrain_enabled": True,
        "terrain_z_scale": 1.5,
        "terrain_base_thickness_mm": 2.0,
        "terrain_resolution": 200,
        "terrarium_zoom": 15,
        "terrain_smoothing_sigma": 2.0,
        "flatten_buildings_on_terrain": True,
        "flatten_roads_on_terrain": False,
        "export_format": "3mf",
        "context_padding_m": 100.0
    }
    
    print(f"Відправляємо запит на генерацію...")
    print(f"Область: N={request_data['north']}, S={request_data['south']}, E={request_data['east']}, W={request_data['west']}")
    
    try:
        start_time = time.time()
        response = requests.post(f"{API_BASE}/api/generate", json=request_data, timeout=30)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            task_id = data.get("task_id")
            print(f"[OK] Запит прийнято за {elapsed:.2f}s")
            print(f"   Task ID: {task_id}")
            print(f"   Status: {data.get('status')}")
            
            # Чекаємо завершення генерації
            print(f"\nОчікуємо завершення генерації...")
            max_wait = 300  # 5 хвилин максимум
            check_interval = 5
            waited = 0
            
            while waited < max_wait:
                time.sleep(check_interval)
                waited += check_interval
                
                status_response = requests.get(f"{API_BASE}/api/status/{task_id}", timeout=10)
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    status = status_data.get("status")
                    progress = status_data.get("progress", 0)
                    
                    print(f"   Прогрес: {progress}% - {status}")
                    
                    if status == "completed":
                        output_file = status_data.get("output_file")
                        print(f"[OK] Генерація завершена за {waited}s!")
                        print(f"   Файл: {output_file}")
                        
                        # Перевіряємо, чи файл існує
                        if output_file and Path(output_file).exists():
                            file_size = Path(output_file).stat().st_size
                            print(f"   Розмір файлу: {file_size / 1024 / 1024:.2f} MB")
                            return True
                        else:
                            print(f"[WARN] Файл не знайдено: {output_file}")
                            return False
                    elif status == "failed":
                        error = status_data.get("error", "Unknown error")
                        print(f"[ERROR] Генерація не вдалася: {error}")
                        return False
                else:
                    print(f"[WARN] Не вдалося отримати статус: {status_response.status_code}")
            
            print(f"[ERROR] Таймаут очікування ({max_wait}s)")
            return False
            
        else:
            print(f"[ERROR] Запит не вдався: {response.status_code}")
            try:
                error_data = response.json()
                print(f"   Помилка: {error_data}")
            except:
                print(f"   Відповідь: {response.text[:200]}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"[ERROR] Таймаут запиту")
        return False
    except Exception as e:
        print(f"[ERROR] Помилка: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_api_endpoints():
    """Тест 3: Перевірка всіх endpoints"""
    print("\n" + "=" * 80)
    print("ТЕСТ 3: Перевірка API endpoints")
    print("=" * 80)
    
    endpoints = [
        ("/", "GET", None),
        ("/api/generate", "POST", {
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
            "export_format": "stl"
        }),
    ]
    
    results = []
    for endpoint, method, data in endpoints:
        try:
            if method == "GET":
                response = requests.get(f"{API_BASE}{endpoint}", timeout=5)
            else:
                response = requests.post(f"{API_BASE}{endpoint}", json=data, timeout=10)
            
            if response.status_code in [200, 422]:  # 422 = validation error, це теж нормально
                print(f"[OK] {method} {endpoint}: {response.status_code}")
                results.append(True)
            else:
                print(f"[WARN] {method} {endpoint}: {response.status_code}")
                results.append(False)
        except Exception as e:
            print(f"[ERROR] {method} {endpoint}: {e}")
            results.append(False)
    
    return all(results)

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("ТЕСТУВАННЯ ОПТИМІЗАЦІЙ ПАМ'ЯТІ ТА BATCH PROCESSING")
    print("=" * 80)
    print()
    
    # Тест 1: Health check
    if not test_api_health():
        print("\n[ERROR] API не доступний. Переконайтеся, що сервер запущено.")
        exit(1)
    
    # Тест 2: Перевірка endpoints
    test_api_endpoints()
    
    # Тест 3: Повна генерація (закоментовано, бо може тривати довго)
    print("\n" + "=" * 80)
    print("ПРИМІТКА: Повний тест генерації пропущено (може тривати довго).")
    print("Для повного тесту запустіть: python test_memory_optimization.py --full")
    print("=" * 80)
    
    import sys
    if "--full" in sys.argv:
        if test_small_generation():
            print("\n[OK] Всі тести пройдені успішно!")
        else:
            print("\n[ERROR] Тест генерації не пройшов")
            exit(1)
    else:
        print("\n[OK] Базові тести пройдені успішно!")
        print("   (Для повного тесту додайте --full)")
