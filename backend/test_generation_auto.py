"""
Автоматичний тест генерації моделі з моніторингом
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import requests
import json
import time
from datetime import datetime

# Координати центру Києва (з тестового файлу)
KYIV_CENTER_LAT = 50.4501
KYIV_CENTER_LON = 30.5234
KM_TO_DEGREES_LAT = 0.009
KM_TO_DEGREES_LON = 0.009 / 0.64

# Область 1км x 1км
HALF_KM = 1.0
north = KYIV_CENTER_LAT + HALF_KM * KM_TO_DEGREES_LAT
south = KYIV_CENTER_LAT - HALF_KM * KM_TO_DEGREES_LAT
east = KYIV_CENTER_LON + HALF_KM * KM_TO_DEGREES_LON
west = KYIV_CENTER_LON - HALF_KM * KM_TO_DEGREES_LON

print("=" * 80)
print("АВТОМАТИЧНИЙ ТЕСТ ГЕНЕРАЦІЇ МОДЕЛІ (ТІЛЬКИ РЕЛЬЄФ)")
print("=" * 80)
print(f"Координати: N={north:.6f}, S={south:.6f}, E={east:.6f}, W={west:.6f}")
print()
print("РЕЖИМ ТЕСТУВАННЯ: Генерація тільки рельєфу (без будівель, доріг, води)")
print()

# Параметри генерації (тільки рельєф для тестування)
request_data = {
    "north": north,
    "south": south,
    "east": east,
    "west": west,
    "terrain_enabled": True,
    "terrain_only": True,  # ТЕСТОВИЙ РЕЖИМ: тільки рельєф
    "terrain_resolution": 300,
    "terrain_z_scale": 3.0,
    "terrain_smoothing_sigma": 2.0,
    "terrain_subdivide": True,
    "terrain_subdivide_levels": 1,
    "model_size_mm": 200,
    "export_format": "3mf",
}

url = "http://127.0.0.1:8000/api/generate"

print("Відправляю запит на генерацію...")
try:
    response = requests.post(url, json=request_data, timeout=10)
    print(f"Response status: {response.status_code}")
    
    if response.status_code != 200:
        print(f"Помилка: {response.text}")
        sys.exit(1)
    
    resp_data = response.json()
    task_id = resp_data.get("task_id")
    
    if not task_id:
        print("Помилка: не отримано task_id")
        print(f"Response: {resp_data}")
        sys.exit(1)
    
    print(f"Task ID: {task_id}")
    print("Чекаю завершення генерації...")
    print()
    
    max_wait = 600  # 10 хвилин
    waited = 0
    last_progress = -1
    
    while waited < max_wait:
        time.sleep(5)
        waited += 5
        
        try:
            status_response = requests.get(f"http://127.0.0.1:8000/api/status/{task_id}", timeout=5)
            if status_response.status_code != 200:
                print(f"[{waited}s] Помилка отримання статусу: {status_response.status_code}")
                continue
            
            status_data = status_response.json()
            status = status_data.get("status")
            progress = status_data.get("progress", 0)
            message = status_data.get("message", "")
            
            if progress != last_progress:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] [{waited}s] Status: {status}, Progress: {progress}%, Message: {message}")
                last_progress = progress
            
            if status == "completed":
                print()
                print("=" * 80)
                print("ГЕНЕРАЦІЯ ЗАВЕРШЕНА УСПІШНО!")
                print("=" * 80)
                print(f"Task ID: {task_id}")
                print(f"Час виконання: {waited} секунд")
                print()
                
                # Отримуємо детальну інформацію про результат
                if "output_file" in status_data:
                    print(f"Файл згенеровано: {status_data['output_file']}")
                
                print()
                print("Аналіз завершено. Перевірте логи сервера для деталей.")
                break
                
            elif status == "error":
                print()
                print("=" * 80)
                print("ПОМИЛКА ГЕНЕРАЦІЇ!")
                print("=" * 80)
                error_msg = status_data.get("error", "Unknown error")
                print(f"Помилка: {error_msg}")
                sys.exit(1)
                
        except requests.exceptions.RequestException as e:
            print(f"[{waited}s] Помилка запиту статусу: {e}")
            continue
    
    if waited >= max_wait:
        print()
        print("=" * 80)
        print("ТАЙМАУТ: Генерація не завершилась за {max_wait} секунд")
        print("=" * 80)
        sys.exit(1)
        
except requests.exceptions.ConnectionError:
    print("Помилка: не вдалося підключитися до сервера")
    print("Переконайтеся, що сервер запущено на http://127.0.0.1:8000")
    sys.exit(1)
except Exception as e:
    print(f"Помилка: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


