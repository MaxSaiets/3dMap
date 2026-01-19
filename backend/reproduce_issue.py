
import networkx as nx
import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon
from backend.services.road_processor import process_roads, detect_bridges, get_node_level

# Mock Terrain Provider
class MockTerrainProvider:
    def get_heights_for_points(self, points):
        # Return 0.0 for ground, -10.0 for water depression
        return np.zeros(len(points))

    class OriginalHeights:
        def get_heights_for_points(self, points):
            return np.full(len(points), 0.0) # Water surface at 0

    original_heights_provider = OriginalHeights()

def test_bridge_detection():
    print("--- Testing Bridge Detection ---")
    G = nx.MultiDiGraph()
    # Edge crossing water
    G.add_edge(1, 2, key=0, geometry=LineString([(0,0), (100,0)]), bridge='yes') # Explicit
    G.add_edge(2, 3, key=0, geometry=LineString([(100,0), (200,0)])) # Implicit check?
    
    water = Polygon([(90, -10), (210, -10), (210, 10), (90, 10)]) # Covers Edge 2-3
    
    bridges = detect_bridges(G, [water])
    print(f"Bridges detected: {bridges}")
    
    # Check if edge (2,3) is detected
    if (2, 3, 0) in bridges:
        print("PASS: Implicit bridge detected.")
    else:
        print("FAIL: Implicit bridge NOT detected.")

def test_road_levels():
    print("\n--- Testing Road Levels ---")
    G = nx.MultiDiGraph()
    # Node 1 (Level 0) -> Node 2 (Level 1)
    G.add_node(1)
    G.add_node(2)
    G.add_edge(1, 2, key=0, geometry=LineString([(0,0), (100,0)]), layer='0')
    
    # We want to see if get_node_level for Node 1 returns 0 or 1?
    # Logic: get_node_level(node) checks connected edges.
    # Edge 1-2 has layer '0'.
    # But wait, does it check incoming or outgoing? G.edges(node) is outgoing.
    # For undirected graph concept, we usually iterate all incident edges.
    
    # Let's add an incoming edge to Node 2 from Node 3 (Level 1)
    G.add_edge(3, 2, key=0, geometry=LineString([(100,100), (100,0)]), layer='1')
    
    lvl_1 = get_node_level(G, 1)
    lvl_2 = get_node_level(G, 2)
    print(f"Node 1 Level: {lvl_1}")
    print(f"Node 2 Level: {lvl_2}")
    
    # Expect Node 2 to be Level 1 because connected edge (3,2) has layer 1.
    if lvl_2 == 1:
        print("PASS: Node level propagation works.")
    else:
        print(f"FAIL: Node 2 should be Level 1, got {lvl_2}")

def test_generation():
    print("\n--- Testing Geometry Flatness ---")
    G = nx.MultiDiGraph()
    # Simple leveled road
    G.add_edge(1, 2, key=0, geometry=LineString([(0,0), (50,0)]), layer='1')
    
    mesh = process_roads(
        G, 
        width_multiplier=1.0, 
        terrain_provider=MockTerrainProvider(),
        road_height=1.0,
        road_embed=0.1,
        merged_roads=None,
        water_geometries=[]
    )
    
    if mesh:
        zs = mesh.vertices[:, 2]
        print(f"Mesh Z Range: {zs.min()} to {zs.max()}")
        # Level 1 should be approx 6.0m + base_height
        if zs.max() > 5.0:
            print("PASS: Road is elevated.")
        else:
            print("FAIL: Road is NOT elevated.")

if __name__ == "__main__":
    test_bridge_detection()
    test_road_levels()
    test_generation()
