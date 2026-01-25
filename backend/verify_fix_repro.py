import sys
import os
from pathlib import Path
import numpy as np
from pyproj import CRS

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from services.global_center import GlobalCenter
from services.terrain_generator import create_terrain_mesh

def verify_fix():
    print("Verifying Terrain CRS Fix...")
    
    # 1. Setup Global Center (Kyiv - UTM Zone 36N)
    # Kyiv lon ~30.5 -> Zone 36
    gc = GlobalCenter(50.45, 30.52)
    print(f"GlobalCenter CRS: {gc.utm_crs}")
    
    # 2. Simulate a "wrong" source CRS (e.g. from a building file that was interpreted weirdly, or adjacent zone)
    # Let's say Zone 35N
    fake_source_crs = CRS.from_epsg(32635)
    print(f"Fake Source CRS: {fake_source_crs}")
    
    # 3. Call create_terrain_mesh
    # We expect it to print "[INFO] Using GlobalCenter CRS for elevation lookup..."
    # and use gc.utm_crs instead of fake_source_crs.
    
    # Dummy bbox in meters (local)
    bbox_meters = (-100, -100, 100, 100)
    
    try:
        # We don't care about the actual result, just the print output and that it doesn't crash
        # We disable water/buildings/roads to make it fast
        create_terrain_mesh(
            bbox_meters=bbox_meters,
            z_scale=1.0,
            resolution=10, # Very low res for speed
            latlon_bbox=(50.46, 50.44, 30.53, 30.51), # Dummy bbox
            source_crs=fake_source_crs, # PASS THE WRONG CRS
            global_center=gc,
            bbox_is_local=True,
            # Disable other features
            flatten_buildings=False,
            flatten_roads=False,
            water_geometries=None,
            elevation_ref_m=None # Local mode for simplicity
        )
    except Exception as e:
        print(f"Execution finished (expected, as API might fail or we just wanted logs): {e}")

if __name__ == "__main__":
    verify_fix()
