import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def test_status_endpoint(task_id):
    print(f"Testing status for {task_id}...")
    try:
        r = requests.get(f"{BASE_URL}/api/status/{task_id}")
        if r.status_code == 200:
            print("Status OK")
            return True
        else:
            print(f"Status Failed: {r.status_code} {r.text}")
            return False
    except Exception as e:
        print(f"Connection error: {e}")
        return False

def test_download_redirect(task_id):
    print(f"Testing download redirect for {task_id}...")
    url = f"{BASE_URL}/api/download/{task_id}?format=stl"
    try:
        # allow_redirects=False to check the 303 explicitly
        r = requests.get(url, allow_redirects=False)
        if r.status_code == 303:
            print(f"Redirect OK (303) -> {r.headers.get('Location')}")
            
            # Now follow it
            location = r.headers.get('Location')
            # If location path is absolute or relative, handle it
            if location.startswith("/"):
                final_url = f"{BASE_URL}{location}"
            else:
                final_url = location
                
            print(f"Following to {final_url}...")
            r2 = requests.get(final_url, stream=True)
            if r2.status_code in [200, 206]:
                print(f"Download OK. Size: {len(r2.content)} bytes")
                return True
            else:
                print(f"Static file fetch failed: {r2.status_code}")
                return False
        elif r.status_code == 200:
             print("Warning: Received 200 OK directly (Redirect didn't happen?)")
             return True # technically success but we wanted redirect
        else:
            print(f"Failed: {r.status_code}")
            return False
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    # Use a known task ID from previous logs
    TASK_ID = "5133f663-c1fd-4d50-a9f6-f7d66ff7aadf" 
    
    if not test_status_endpoint(TASK_ID):
        print("Skipping download test due to status failure")
    else:
        test_download_redirect(TASK_ID)
