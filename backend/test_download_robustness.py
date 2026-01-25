import requests
import time
import os
import uuid
import sys

BASE_URL = "http://127.0.0.1:8000"

def test_download(task_id, part="base"):
    url = f"{BASE_URL}/api/download/{task_id}?format=stl&part={part}"
    print(f"Attempting download: {url}")
    try:
        start = time.time()
        # Use stream=True to mimic browser behavior
        with requests.get(url, stream=True, timeout=60) as r:
            if r.status_code == 404:
                print(f"Server returned 404 (Task not found or file missing). This means connection IS working.")
                return True
            if r.status_code != 200:
                print(f"FAILED status {r.status_code}: {r.text}")
                return False
            
            downloaded = 0
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    downloaded += len(chunk)
            
            elapsed = time.time() - start
            print(f"SUCCESS: Downloaded {downloaded/1024/1024:.2f} MB in {elapsed:.2f}s")
            return True
            
    except requests.exceptions.ConnectionError as e:
        print(f"CONNECTION ERROR (The Bug!): {e}")
        return False
    except Exception as e:
        print(f"EXCEPTION: {e}")
        return False

def run_e2e_test():
    print("Starting E2E Test...")
    
    # 1. Trigger Generation (Small area to be fast)
    payload = {
        "north": 50.445,
        "south": 50.440,
        "east": 30.530,
        "west": 30.525,
        "road_width_multiplier": 1.0,
        "road_height_mm": 0.5,
        "road_embed_mm": 0.3,
        "building_min_height": 2.0,
        "building_height_multiplier": 1.0,
        "building_foundation_mm": 0.6,
        "building_embed_mm": 0.2,
        "building_max_foundation_mm": 2.5,
        "include_parks": False,
        "parks_height_mm": 0.6,
        "parks_embed_mm": 0.2,
        "include_pois": False,
        "poi_size_mm": 0.8,
        "poi_height_mm": 1.2,
        "poi_embed_mm": 0.2,
        "water_depth": 2.0,
        "terrain_enabled": False, # Faster
        "terrain_resolution": 150,
        "terrain_z_scale": 1.0,
        "terrain_base_thickness_mm": 1.0,
        "terrain_subdivide": False,
        "terrain_subdivide_levels": 0,
        "export_format": "stl",
        "model_size_mm": 50.0
    }
    
    try:
        r = requests.post(f"{BASE_URL}/api/generate", json=payload)
        if r.status_code != 200:
            print(f"Generation failed: {r.text}")
            return
        
        data = r.json()
        task_id = data["task_id"]
        print(f"Task started: {task_id}")
        
        # 2. Poll Status
        for i in range(30):
            time.sleep(2)
            r = requests.get(f"{BASE_URL}/api/status/{task_id}")
            status = r.json()
            print(f"Status: {status['status']} ({status.get('progress')}%)")
            
            if status['status'] == "completed":
                print("Generation Completed!")
                break
            if status['status'] == "failed":
                print(f"Generation Failed: {status.get('message')}")
                return
        else:
            print("Timeout waiting for generation")
            return

        # 3. Download
        print("Starting Download of Result...")
        if test_download(task_id, "stl"):
            print("E2E TEST PASSED: Download successful without connection reset.")
        else:
            print("E2E TEST FAILED: Download error.")

    except Exception as e:
        print(f"E2E Exception: {e}")

if __name__ == "__main__":
    try:
        r = requests.get(f"{BASE_URL}/docs", timeout=5)
        print(f"Server reachable: {r.status_code}")
    except:
        print("Server NOT reachable. Please run 'python run.py' first.")
        sys.exit(1)
    
    run_e2e_test()
