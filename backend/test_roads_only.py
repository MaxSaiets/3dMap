import os
import sys
import numpy as np
import trimesh
import osmnx as ox
import pandas as pd
import warnings
from shapely.geometry import Polygon
from shapely.ops import transform

# Suppress warnings
warnings.filterwarnings('ignore')

# Add backend directory to path
sys.path.append(os.getcwd())

from services.global_center import GlobalCenter
from services.road_processor import build_road_polygons, densify_geometry

# Configuration
OUTPUT_FILE = r"h:\3dMap\backend\output\test_roads_only.stl"
CENTER_LAT = 50.400000
CENTER_LON = 30.500000

# Paton Zone
NORTH = 50.431122
SOUTH = 50.423934
EAST = 30.576917
WEST = 30.566988

# Use 1km padding just like in main.py
ROAD_PADDING = 0.01 

print("=== TEST ROADS ONLY GENERATION ===")
print(f"Zone: N={NORTH}, S={SOUTH}, E={EAST}, W={WEST}")
print(f"Padding: {ROAD_PADDING}")

try:
    # 1. Init Global Center
    gc = GlobalCenter(CENTER_LAT, CENTER_LON)
    
    # 2. Build Zone Polygon (local coords)
    poly_coords_latlon = [
        (WEST, NORTH), (EAST, NORTH), (EAST, SOUTH), (WEST, SOUTH), (WEST, NORTH)
    ]
    # Simple rectangle for test (user has hex but bbox is fine for clipping test)
    # Actually let's use the bbox converted to local rect
    minx, miny = gc.to_local(*gc.to_utm(WEST, SOUTH))
    maxx, maxy = gc.to_local(*gc.to_utm(EAST, NORTH))
    zone_polygon_local = Polygon([
        (minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)
    ])
    
    print(f"Zone Polygon Local Bounds: {zone_polygon_local.bounds}")

    # 3. Fetch Roads (Large Padding)
    padded_north = NORTH + ROAD_PADDING
    padded_south = SOUTH - ROAD_PADDING
    padded_east = EAST + ROAD_PADDING
    padded_west = WEST - ROAD_PADDING
    
    print("Fetching roads...")
    ox.settings.use_cache = False # FORCE LIVE
    G = ox.graph_from_bbox(
        padded_north, padded_south, padded_east, padded_west,
        network_type='all', 
        simplify=True, 
        retain_all=True
    )
    
    if G is None:
        print("Error: No roads found.")
        exit(1)
        
    print(f"Downloaded {len(G.edges)} edges.")
    
    # 4. Project Graph
    G_proj = ox.project_graph(G) # projects to UTM
    
    # 5. Build Polygons (Unclipped)
    print("Building road polygons...")
    # Use road_processor's function which handles densification
    merged_roads_utm = build_road_polygons(G_proj, width_multiplier=1.0)
    
    # 6. Transform to Local
    print("Transforming to local...")
    def to_local_r(x, y, z=None):
        return gc.to_local(x, y)
    
    merged_roads_local = transform(to_local_r, merged_roads_utm)
    
    # 7. Clip to Zone
    print("Clipping to zone...")
    clipped_roads = merged_roads_local.intersection(zone_polygon_local)
    
    if clipped_roads.is_empty:
        print("Error: Clipped roads are empty!")
        exit(1)
        
    # 8. Extrude
    print("Extruding...")
    # Flatten multipolygons
    polys = []
    if clipped_roads.geom_type == 'Polygon':
        polys = [clipped_roads]
    elif clipped_roads.geom_type in ['MultiPolygon', 'GeometryCollection']:
        for g in clipped_roads.geoms:
            if g.geom_type == 'Polygon': polys.append(g)
            
    print(f"Processing {len(polys)} polygons...")
    
    meshes = []
    for p in polys:
        if p.area < 0.1: continue
        try:
            # Densify
            p_dense = densify_geometry(p, max_segment_length=10.0)
            if not p_dense.is_valid: p_dense = p_dense.buffer(0)
            
            # Handle potential split from buffer(0)
            sub_polys = [p_dense]
            if p_dense.geom_type == 'MultiPolygon':
                sub_polys = list(p_dense.geoms)
                
            for sp in sub_polys:
                if sp.area < 0.1: continue
                m = trimesh.creation.extrude_polygon(sp, height=2.0)
                meshes.append(m)
        except Exception as e:
            print(f"Warn: {e}")
            
    if meshes:
        print(f"Saving {len(meshes)} meshes to {OUTPUT_FILE}")
        combined = trimesh.util.concatenate(meshes)
        combined.export(OUTPUT_FILE)
        print("Done.")
    else:
        print("No meshes generated.")

except Exception as e:
    print(f"Fatal: {e}")
    import traceback
    traceback.print_exc()
