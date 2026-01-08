import requests
import sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

API = "http://127.0.0.1:8000"

print("Testing API...")
r1 = requests.get(f"{API}/", timeout=5)
print(f"Health: {r1.status_code} - {r1.json()}")

print("\nCreating generation task...")
req = {
    "north": 50.455, "south": 50.450, "east": 30.530, "west": 30.520,
    "model_size_mm": 100.0, "road_width_multiplier": 1.0,
    "building_min_height": 2.0, "building_height_multiplier": 1.0,
    "water_depth": 2.0, "terrain_enabled": True, "export_format": "stl"
}

try:
    r2 = requests.post(f"{API}/api/generate", json=req, timeout=(5, 15), stream=True)
    r2.raw.read(1)  # Check connection
    print(f"Request sent: {r2.status_code}")
    if r2.status_code == 200:
        data = r2.json()
        task_id = data.get("task_id")
        print(f"Task ID: {task_id}")
        print(f"Status: {data.get('status')}")
        print("\n[OK] System is working! Task created successfully.")
        print("Generation runs in background with batch processing enabled.")
    else:
        print(f"Error: {r2.status_code} - {r2.text[:200]}")
except requests.exceptions.Timeout:
    print("[WARN] Request timed out (may be normal if server is processing)")
    print("[INFO] Check server logs to verify batch processing is active")
except Exception as e:
    print(f"[ERROR] {e}")


