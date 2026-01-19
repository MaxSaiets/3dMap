
import os
import glob

path = r"h:\3dMap\backend\services"
print(f"Listing {path}:")
try:
    files = os.listdir(path)
    for f in files:
        print(f)
except Exception as e:
    print(f"Error: {e}")

print("\nSpecific check:")
target = os.path.join(path, "road_processor.py")
print(f"Exists {target}? {os.path.exists(target)}")
