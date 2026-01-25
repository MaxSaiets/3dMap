import sys
import os
import asyncio
import warnings
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

# Mock FastAPI/Context stuffs
from services.generation_task import GenerationTask
# CORRECT IMPORT: generate_model_task is in main.py
from main import GenerationRequest, ZoneGenerationRequest, generate_model_task, tasks

# Suppress warnings
warnings.filterwarnings('ignore')

async def main():
    print("=== STARTING PATON BRIDGE GENERATION TEST ===")
    
    # 1. Setup Request for Zone 43, 39 (Paton Area)
    target_north = 50.431122
    target_south = 50.423934
    target_east = 30.576917
    target_west = 30.566988
    
    req = GenerationRequest(
        north=target_north,
        south=target_south,
        east=target_east,
        west=target_west,
        road_width_multiplier=1.0,
        model_size_mm=160.0,
        terrain_enabled=True,
        water_depth=2.0,
        export_format="stl",
        include_parks=True,
        terrain_only=False
    )
    
    task_id = "test_paton_gen"
    print(f"Created request for Paton Zone: {req}")
    
    # Initialize global center if needed
    from services.global_center import set_global_center
    set_global_center(50.40, 30.50) # Approx
    
    # Mock task
    tasks[task_id] = GenerationTask(task_id=task_id, request=req)
    
    print("Running generation task...")
    try:
        # Pass mocked zone info to simulate hexagonal grid context if needed
        # But here we just pass direct coords.
        result = await generate_model_task(
            task_id=task_id, 
            request=req,
            zone_id="hex_43_39",
            # We skip zone_polygon_coords and zone_row/col if optional, 
            # allowing fallback to bbox logic which is fine for this test.
            # But wait, logic in main.py lines 1017+ checks zone_polygon_coords for accurate clipping.
            # If we omit it, it uses bbox, which is OK for a quick test.
        )
        print("Task finished.")
    except Exception as e:
        print(f"Runtime error: {e}")
        import traceback
        traceback.print_exc()

    print("=== GENERATION FINISHED ===")

if __name__ == "__main__":
    asyncio.run(main())
