"""
Запуск сервера та автоматичний тест генерації
"""
import subprocess
import sys
import time
import requests
import json
from pathlib import Path

# Координати
KYIV_CENTER_LAT = 50.4501
KYIV_CENTER_LON = 30.5234
KM_TO_DEGREES_LAT = 0.009
KM_TO_DEGREES_LON = 0.009 / 0.64
HALF_KM = 1.0

north = KYIV_CENTER_LAT + HALF_KM * KM_TO_DEGREES_LAT
south = KYIV_CENTER_LAT - HALF_KM * KM_TO_DEGREES_LAT
east = KYIV_CENTER_LON + HALF_KM * KM_TO_DEGREES_LON
west = KYIV_CENTER_LON - HALF_KM * KM_TO_DEGREES_LON

print("=" * 80)
print("ЗАПУСК СЕРВЕРА ТА ТЕСТУВАННЯ ГЕНЕРАЦІЇ")
print("=" * 80)
print(f"Координати: N={north:.6f}, S={south:.6f}, E={east:.6f}, W={west:.6f}")
print()

# Запускаємо сервер у фоновому режимі
print("Запускаю сервер...")
server_process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
    cwd=str(Path(__file__).parent),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

# Чекаємо поки сервер запуститься
print("Чекаю запуску сервера (10 секунд)...")
time.sleep(10)

# Перевіряємо чи сервер працює
try:
    health_check = requests.get("http://127.0.0.1:8000/docs", timeout=5)
    print("Сервер запущено успішно!")
except:
    print("Сервер не відповідає, але продовжую...")

print()
print("Відправляю запит на генерацію...")

request_data = {
    "north": north,
    "south": south,
    "east": east,
    "west": west,
    "terrain_enabled": True,
    "terrain_resolution": 300,
    "terrain_z_scale": 3.0,
    "terrain_smoothing_sigma": 2.0,
    "terrain_subdivide": True,
    "terrain_subdivide_levels": 1,
    "road_width_multiplier": 1.5,
    "road_height_mm": 1.0,
    "road_embed_mm": 0.3,
    "water_depth": 2.0,
    "model_size_mm": 200,
    "export_format": "3mf",
    "building_min_height": 2.0,
    "building_height_multiplier": 1.0,
    "include_parks": True,
    "include_pois": False,
}

try:
    # Відправляємо запит з великим таймаутом
    response = requests.post(
        "http://127.0.0.1:8000/api/generate",
        json=request_data,
        timeout=30  # 30 секунд на початковий запит
    )
    
    if response.status_code != 200:
        print(f"Помилка: {response.status_code}")
        print(response.text)
        sys.exit(1)
    
    resp_data = response.json()
    task_id = resp_data.get("task_id")
    
    if not task_id:
        print("Помилка: не отримано task_id")
        sys.exit(1)
    
    print(f"Task ID: {task_id}")
    print("Моніторинг прогресу генерації...")
    print()
    
    max_wait = 900  # 15 хвилин
    waited = 0
    last_progress = -1
    
    while waited < max_wait:
        time.sleep(10)  # Перевіряємо кожні 10 секунд
        waited += 10
        
        try:
            status_response = requests.get(
                f"http://127.0.0.1:8000/api/status/{task_id}",
                timeout=10
            )
            
            if status_response.status_code != 200:
                print(f"[{waited}s] Помилка отримання статусу: {status_response.status_code}")
                continue
            
            status_data = status_response.json()
            status = status_data.get("status")
            progress = status_data.get("progress", 0)
            message = status_data.get("message", "")
            
            if progress != last_progress or status != "processing":
                print(f"[{waited:4d}s] Status: {status:12s} | Progress: {progress:3d}% | {message}")
                last_progress = progress
            
            if status == "completed":
                print()
                print("=" * 80)
                print("ГЕНЕРАЦІЯ ЗАВЕРШЕНА УСПІШНО!")
                print("=" * 80)
                print(f"Task ID: {task_id}")
                print(f"Час виконання: {waited} секунд ({waited/60:.1f} хвилин)")
                if "output_file" in status_data:
                    print(f"Файл: {status_data['output_file']}")
                break
                
            elif status == "error":
                print()
                print("=" * 80)
                print("ПОМИЛКА ГЕНЕРАЦІЇ!")
                print("=" * 80)
                print(f"Помилка: {status_data.get('error', 'Unknown')}")
                break
                
        except Exception as e:
            print(f"[{waited}s] Помилка: {e}")
            continue
    
    if waited >= max_wait:
        print(f"\nТаймаут після {max_wait} секунд")
    
    print("\nЗупиняю сервер...")
    server_process.terminate()
    server_process.wait(timeout=5)
    
except KeyboardInterrupt:
    print("\nПерервано користувачем")
    server_process.terminate()
except Exception as e:
    print(f"\nПомилка: {e}")
    import traceback
    traceback.print_exc()
    server_process.terminate()
finally:
    try:
        server_process.terminate()
    except:
        pass


