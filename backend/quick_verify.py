
import sys
import os

# Add current directory to path so we can import services
sys.path.append(os.getcwd())

import numpy as np
import networkx as nx
from shapely.geometry import LineString, Polygon
import trimesh

try:
    from services import road_processor
    from services import water_processor
    print("[OK] Modules imported successfully")
except ImportError as e:
    print(f"[ERROR] Import failed: {e}")
    sys.exit(1)

# Mock TerrainProvider
class MockTerrainProvider:
    def get_heights_for_points(self, points):
        return np.zeros(len(points))
    
    def get_surface_heights_for_points(self, points):
        return np.zeros(len(points))
        
    def get_bounds(self):
        return (0, 100, 0, 100)

class MockOriginalHeightsProvider:
    def get_heights_for_points(self, points):
        # Return "Water Level" = 5.0
        return np.full(len(points), 5.0)

mock_tp = MockTerrainProvider()
mock_tp.original_heights_provider = MockOriginalHeightsProvider()

def test_road_ramp():
    print("Testing Road Ramp Logic...")
    # Create graph with 2 nodes, different levels
    G = nx.MultiDiGraph()
    G.add_node(1)
    G.add_node(2)
    # Edge connecting them
    G.add_edge(1, 2, key=0, geometry=LineString([(0,0), (100,0)]), layer='0')
    
    # Mock get_node_level to return different levels
    # We can't easily mock the function since it's in the module, 
    # but we can test the function itself if we set up the graph right.
    
    # Add dummy edges to nodes to force levels
    G.add_edge(1, 3, layer='0') # Node 1 is level 0
    G.add_edge(2, 4, layer='1') # Node 2 is level 1
    
    l1 = road_processor.get_node_level(G, 1)
    l2 = road_processor.get_node_level(G, 2)
    
    print(f"Node 1 Level: {l1} (Expected 0)")
    print(f"Node 2 Level: {l2} (Expected 1)")
    
    if l1 != 0 or l2 != 1:
        print("[FAIL] Node level detection failed")
    else:
        print("[PASS] Node level detection")
        
    # Test internal segment creation logic (interpolated ramp)
    # We call _create_road_segment directly
    mesh = road_processor._create_road_segment(
        linestring=LineString([(0,0), (100,0)]),
        width=4.0,
        level_start=0,
        level_end=1,
        is_bridge=False,
        terrain_provider=mock_tp,
        base_height=1.0,
        embed=0.5,
        bridge_clearance=5.0
    )
    
    if mesh is None or len(mesh.vertices) == 0:
        print("[FAIL] Road mesh generation returned None")
    else:
        # Check Z heights
        vs = mesh.vertices
        # We expect Z to rise from ~0 to ~6 (Level 1 is +6m)
        min_z = np.min(vs[:, 2])
        max_z = np.max(vs[:, 2])
        print(f"Ramp Z Range: {min_z:.2f} to {max_z:.2f}")
        
        if max_z > 5.0 and min_z < 2.0:
             print("[PASS] Ramp height interpolation looks correct")
        else:
             print("[WARN] Ramp height range unexpected")

def test_bridge_original_heights():
    print("\nTesting Bridge Height Logic...")
    # Bridge usage of original_heights_provider
    mesh = road_processor._create_road_segment(
        linestring=LineString([(0,0), (100,0)]),
        width=4.0,
        level_start=0,
        level_end=0,
        is_bridge=True,
        terrain_provider=mock_tp,
        base_height=1.0,
        embed=0.0,
        bridge_clearance=6.0
    )
    
    # Mock Original Heights returns 5.0
    # Bridge Ref Z should be 5.0 (original)
    # Target Z = 5.0 + Clearance(6.0) = 11.0
    # Top Z = 11.0 + BaseHeight(1.0) = 12.0
    
    # Note: Logic was `ref_z = np.mean(terrain_z)`.
    # original_provider returns 5.0 everywhere.
    # so ref_z = 5.0
    
    vs = mesh.vertices
    avg_z = np.mean(vs[:, 2])
    print(f"Bridge Average Z: {avg_z:.2f} (Expected ~11.0-12.0)")
    
    if avg_z > 10.0:
        print("[PASS] Bridge uses original heights (correctly elevated)")
    else:
        print(f"[FAIL] Bridge too low (avg {avg_z:.2f}), likely using terrain (0.0)")

def test_water_subdivision():
    print("\nTesting Water Subdivision...")
    try:
        import geopandas as gpd
        poly = Polygon([(0,0), (100,0), (100,100), (0,100)])
        gdf = gpd.GeoDataFrame({'geometry': [poly]})
        
        # We need to catch the print output or just ensure it runs
        mesh = water_processor.process_water_surface(
            gdf_water=gdf,
            thickness_m=2.0,
            depth_meters=1.0,
            terrain_provider=mock_tp,
            texture_enabled=True
        )
        
        if mesh is None:
            print("[FAIL] Water mesh not generated")
            return

        # Check vertex count. 
        # A simple quad has 4 verts.
        # Extruded (top/bottom) -> 8 verts?
        # Subdivided x2 should have meaningful number of vertices.
        # Square: 0 subdiv -> 2 tris. 1 subdiv -> 8 tris. 2 subdiv -> 32 tris.
        # Vertices > 10
        
        n_verts = len(mesh.vertices)
        print(f"Water Mesh Vertices: {n_verts}")
        
        if n_verts > 20: # Arbitrary threshold for "subdivided"
            print("[PASS] Water mesh subdivided")
        else:
            print("[WARN] Water mesh vertex count low, subdivision might have failed or not run")
            
    except Exception as e:
        print(f"[FAIL] Water test verify error: {e}")

if __name__ == "__main__":
    test_road_ramp()
    test_bridge_original_heights()
    test_water_subdivision()
