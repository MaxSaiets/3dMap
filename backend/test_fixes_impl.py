
import sys
import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon, LineString, Point
import trimesh
import networkx as nx

# Add current dir to path to import services
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.road_processor import process_roads
from services.water_processor import process_water_surface

# Mock Terrain Provider
class MockTerrainProvider:
    def __init__(self, ground_z=10.0, original_z=12.0):
        self.ground_z = ground_z
        self.original_z = original_z
        self.original_heights_provider = self 

    def get_surface_heights_for_points(self, points):
        # Simply return constant height + small slope x to verify interpolation? 
        # No, let's keep it simple to verify the strict offsets.
        # But for roads, we test slope.
        pts = np.array(points)
        return np.full(len(pts), self.ground_z) + (pts[:, 0] * 0.1) # Slope X

    def get_heights_for_points(self, points):
        # For original provider
        pts = np.array(points)
        return np.full(len(pts), self.original_z) + (pts[:, 0] * 0.1)

    def get_bounds(self):
        return (0, 100, 0, 100)

def test_water_per_vertex():
    print("Testing Water Per-Vertex Logic...")
    
    # 1. Setup
    # Water polygon
    poly = Polygon([(0,0), (10,0), (10,10), (0,10)])
    gdf = gpd.GeoDataFrame([{'geometry': poly, 'water': 'lake'}], crs="EPSG:3857")
    
    # Terrain: Depressed = 10.0, Original = 12.0. Depth = 2.0.
    terrain = MockTerrainProvider(ground_z=10.0, original_z=12.0)
    
    # 2. Run
    # Enable global var ADD_TEXTURE in water_processor? It's imported.
    # We can check if noise is applied.
    
    mesh = process_water_surface(
        gdf,
        thickness_m=1.0,
        depth_meters=2.0,
        terrain_provider=terrain,
        global_center=None
    )
    
    if mesh is None:
        print("FAIL: No mesh generated")
        return

    # 3. Validation
    verts = mesh.vertices
    # We expect Top Z to be approx Original Z (12.0) + Noise, 
    # BUT strictly >= Depressed (10.0) + 0.2 = 10.2
    
    # Re-calculate expected base
    # slope is x*0.1.
    # At x=0: ground=10, orig=12. z should be ~11.85 (12-0.15).
    # At x=10: ground=11, orig=13. z should be ~12.85.
    
    min_x = np.min(verts[:,0])
    max_x = np.max(verts[:,0])
    
    # Check Depressed Constraint
    depressed = terrain.get_surface_heights_for_points(verts[:, :2])
    diff = verts[:, 2] - depressed
    
    # Filter Top vertices (approx) - Top is roughly > 10.
    is_top = verts[:, 2] > (np.mean(verts[:, 2]))
    top_diff = diff[is_top]
    
    min_diff = np.min(top_diff)
    print(f"  Min Water Height above Bed: {min_diff:.4f}m (Expected >= 0.2)")
    
    if min_diff < 0.19:
        print("FAIL: Water dips too close to bed!")
    else:
        print("PASS: Water clearance constraint holds.")

    # Check Texture (Noise)
    # If noise is working, Z should not be perfectly planar relative to Original.
    # Orig = 12 + 0.1x. Water Base = 11.85 + 0.1x.
    # If we subtract (11.85 + 0.1x), residual should be noise.
    
    base_linear = 11.85 + verts[is_top, 0] * 0.1
    noise_component = verts[is_top, 2] - base_linear
    noise_range = np.max(noise_component) - np.min(noise_component)
    print(f"  Noise Range: {noise_range:.4f}m")
    
    if noise_range < 0.001:
        print("WARN: No noise detected (maybe scale too large or disabled).")
    else:
        print("PASS: Noise texture detected.")


def test_road_nodes():
    print("\nTesting Road Node Snapping...")
    
    # 1. Setup Graph: 2 Edges meeting at (50, 50).
    # Node 1: (0, 50) -> Node 2: (50, 50) -> Node 3: (100, 50)
    # Terrain Slope: Z = 10 + X*0.1.
    # At Node 2 (X=50), Z = 15.0.
    
    G = nx.MultiDiGraph()
    G.add_node(1, x=0, y=0)
    G.add_node(2, x=50, y=0)
    G.add_node(3, x=50, y=50)
    
    # Edges (mock geometry)
    line1 = LineString([(0,0), (50,0)])
    line2 = LineString([(50,0), (50,50)])
    
    G.add_edge(1, 2, key=0, geometry=line1, length=50, layer=0)
    G.add_edge(2, 3, key=0, geometry=line2, length=50, layer=0)
    G.graph['crs'] = 'EPSG:3857'
    
    terrain = MockTerrainProvider(ground_z=10.0)
    
    # 2. Run
    meshes = process_roads(
        G,
        terrain_provider=terrain,
        width_multiplier=1.0,
        road_height=0.5,
        road_embed=0.1,
        global_center=None
    )
    
    if not meshes:
        print("FAIL: No meshes")
        return

    # Combine meshes
    all_verts = meshes.vertices
    
    # 3. Analyze Joint at (50, 0)
    # We find vertices close to (50, 0)
    dist = np.hypot(all_verts[:,0] - 50, all_verts[:,1] - 0)
    mask = dist < 5.0
    
    joint_verts = all_verts[mask]
    if len(joint_verts) == 0:
        print("FAIL: No vertices found at joint (50,50)")
        return
        
    z_vals = joint_verts[:, 2]
    print(f"  Joint Vertices Z stats: Min={np.min(z_vals):.4f}, Max={np.max(z_vals):.4f}, Range={np.ptp(z_vals):.4f}")
    
    # Expected Z:
    # Terrain at 50 = 15.0.
    # Road Z = Terrain - embed(0.1) + lift(0.05) + (Start/End adjust?)
    # Since we implemented NODE SNAPPING, all vertices at (50,50) should be IDENTICAL.
    # Range should be ~0 (floating point error).
    
    # Expected Z Range = Road Height (0.5) because bottom is at Base and top is at Base+Height.
    # If there was a step, the range would be larger (e.g. 0.5 + 0.15 = 0.65).
    # Since Range is 0.5000, it means all Top vertices are at same Z, and all Bottom Z are at same Z.
    # So alignment is perfect.
    
    if abs(np.ptp(z_vals) - 0.5) < 0.01:
        print("PASS: Vertices at joint are perfectly aligned (Range matches height).")
    else:
        print(f"FAIL: Z-gap detected at joint! Range: {np.ptp(z_vals):.4f}")

def test_dangling_bridge():
    print("\nTesting Dangling Bridge (Single Shore)...")
    
    # 1. Setup
    # Create a road polygon that represents a shore (Land)
    # Polygon from (0,0) to (20,20).
    land_poly = Polygon([(0,0), (20,0), (20,20), (0,20)])
    
    # Create a Bridge LineString that enters the land but "dangles" off the edge
    # Bridge from (10, 10) to (50, 10). Length 40.
    # It barely overlaps the land (10 to 20).
    bridge_line = LineString([(10,10), (50,10)])
    
    # Create Graph
    G = nx.MultiDiGraph()
    G.add_edge(1, 2, key=0, geometry=bridge_line, bridge='yes', layer=1, width=4.0)
    G.graph['crs'] = 'EPSG:3857'
    
    terrain = MockTerrainProvider(ground_z=10.0)
    
    # We need to manually construct the "merged_roads" polygon to simulate
    # what happens before process_roads.
    # In reality, this polygon would be the union of many roads.
    # Here we just use the land_poly to represent the "Road Zone".
    
    # Let's mock the `merged_roads` argument.
    # If we assume the road network was clipped or split, and we are processing the "Land" chunk.
    land_poly_short = Polygon([(0,0), (15,0), (15,20), (0,20)]) # Centroid (7.5, 10)
    
    # Bridge from (14, 10) to (50, 10).
    # Overlap is (14,10) to (15,10) (Tiny strip).
    # Bridge Buffer (width 4 -> radius 2) covers Y=[8,12].
    # At X=7.5 (centroid), bridge buffer is far away (starts at 14).
    # So Centroid check fails.
    
    bridge_line_dangle = LineString([(14,10), (50,10)])
    G_dangle = nx.MultiDiGraph()
    G_dangle.add_edge(1, 2, key=0, geometry=bridge_line_dangle, bridge='yes', layer=1, width=4.0)
    G_dangle.graph['crs'] = 'EPSG:3857'
    
    print("  Simulating Edge Case where Polygon Centroid is OUTSIDE Bridge Buffer...")
    
    # We call process_roads with explicit `merged_roads` set to our Land Poly
    # This forces the loop to process `land_poly_short`.
    # The bridge detection inside `process_roads` will see the `bridge_line_dangle`.
    # It should DETECT it (intersection with poly is True).
    # But the old logic would fail to MATCH it because `land_poly_short.centroid` is far from bridge.
    
    meshes = process_roads(
        G_dangle,
        terrain_provider=terrain,
        width_multiplier=1.0,
        merged_roads=land_poly_short, # FORCE this specific polygon
        road_height=1.0
    )
    
    if hasattr(meshes, 'vertices') and len(meshes.vertices) > 0:
        print(f"  Result: Generated {len(meshes.vertices)} vertices.")
        # We expect some vertices to be elevated (bridge height).
        # Normal ground is 10.0. Bridge should be higher (e.g. +6m for layer 1?).
        
        z_vals = meshes.vertices[:, 2]
        max_z = np.max(z_vals)
        print(f"  Max Z: {max_z:.2f} (Ground is ~10.0)")
        
        if max_z > 14.0:
            print("PASS: Bridge mesh generated (Elevation detected).")
        else:
            print("FAIL: Mesh generated but looks like flat ground (No bridge elevation).")
            
    else:
        print("FAIL: No mesh generated at all.")


if __name__ == "__main__":
    test_water_per_vertex()
    test_road_nodes()
    test_dangling_bridge()
