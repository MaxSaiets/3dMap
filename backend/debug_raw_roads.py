import osmnx as ox
import geopandas as gpd
import trimesh
import numpy as np
from shapely.geometry import LineString, MultiLineString
from shapely.ops import transform
import pandas as pd
import warnings

# Suppress warnings
warnings.filterwarnings('ignore')

# Configuration
OUTPUT_FILE = r"h:\3dMap\backend\output\debug_5km_all.stl"
CENTER_LAT = 50.400000
CENTER_LON = 30.500000

# Target Zone BBox (Paton)
NORTH = 50.431122
SOUTH = 50.423934
EAST = 30.576917
WEST = 30.566988
PADDING = 0.03 # ~5.5km padding per user request

print("=== DEBUG 5KM ALL ROADS ===")
print(f"Fetching live data... Padding={PADDING}")

try:
    ox.settings.use_cache = False
    
    padded_north = NORTH + PADDING
    padded_south = SOUTH - PADDING
    padded_east = EAST + PADDING
    padded_west = WEST - PADDING
    
    print("Downloading graph (simplify=True)...")
    G = ox.graph_from_bbox(
        padded_north, padded_south, padded_east, padded_west,
        network_type='all', 
        simplify=True, 
        retain_all=True
    )
    
    if G is None or len(G.edges) == 0:
        print("Error: No edges found!")
        exit(1)
        
    print(f"Downloaded {len(G.edges)} edges.")
    
    # Analyze Bridge Tags
    bridge_edges = []
    paton_edges = []
    
    print("Analyzing tags...")
    for u, v, k, data in G.edges(keys=True, data=True):
        # Check bridge tag
        is_bridge = False
        if 'bridge' in data and data['bridge'] not in ['no', 'nan', None]:
            is_bridge = True
        
        name = str(data.get('name', ''))
        
        if is_bridge:
            bridge_edges.append(data)
            if "Paton" in name or "Патона" in name:
                print(f"[FOUND] Bridge AND Name Match! ID: {data.get('osmid')}")
        
        if "Paton" in name or "Патона" in name:
            paton_edges.append(data)

    print(f"Total edges with 'bridge' tag: {len(bridge_edges)}")
    print(f"Total edges with 'Paton' name: {len(paton_edges)}")
    
    if len(bridge_edges) > 0:
        print("Sample bridge names:", [e.get('name', 'unnamed') for e in bridge_edges[:10]])

    # Convert to GDF
    print("Converting to GDF...")
    gdf = ox.graph_to_gdfs(G, nodes=False)
    
    # Filter only bridges for STL
    # Actually, let's dump ALL 'Paton' edges + ALL 'Bridge' edges to be sure
    
    # Setup Global Center
    import sys
    import os
    sys.path.append(os.getcwd())
    from services.global_center import GlobalCenter
    
    gc = GlobalCenter(CENTER_LAT, CENTER_LON)
    
    meshes = []
    
    print("Projecting and meshing ALL roads...")
    count = 0
    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty: continue
        
        # Project LatLon -> Local
        try:
            local_geom = transform(
                lambda x, y, z=None: gc.to_local(*gc.to_utm(x, y)), 
                geom
            )
            
            # Create mesh
            # Standard width for everything
            poly = local_geom.buffer(3.0)
            if not poly.is_empty:
                # Name check just for highlight (optional)
                name = str(row.get('name', ''))
                is_paton = "Paton" in name or "Патона" in name
                height = 50.0 if is_paton else 2.0
                
                mesh = trimesh.creation.extrude_polygon(poly, height=height)
                meshes.append(mesh)
                count += 1
                
        except Exception as e:
            pass

    print(f"Generated {count} total road meshes.")
    
    if meshes:
        combined = trimesh.util.concatenate(meshes)
        combined.export(OUTPUT_FILE)
        print(f"Saved to {OUTPUT_FILE}")
    else:
        print("No matches to save.")

except Exception as e:
    print(f"Fatal: {e}")
    import traceback
    traceback.print_exc()
