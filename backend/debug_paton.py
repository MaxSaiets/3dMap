import osmnx as ox
import geopandas as gpd
from shapely.geometry import box

# Coordinates for Kyiv, near Paton Bridge
north, south, east, west = 50.44, 50.41, 30.60, 30.56

print(f"Checking area: N={north}, S={south}, E={east}, W={west}")

try:
    # 1. Download specific area to check for the bridge
    print("Downloading OSM data for the area...")
    G = ox.graph_from_bbox(north, south, east, west, network_type='all')
    gdf_edges = ox.graph_to_gdfs(G, nodes=False)
    
    # 2. Search for "Paton" in name
    print(f"Loaded {len(gdf_edges)} edges. Searching for 'Paton'...")
    
    paton_edges = gdf_edges[gdf_edges['name'].astype(str).str.contains("Paton|Патона", case=False, na=False)]
    
    if paton_edges.empty:
        print("❌ Paton bridge NOT found in the downloaded data!")
    else:
        print(f"✅ Found {len(paton_edges)} edges related to Paton Bridge.")
        # Safely print columns
        cols = ['name', 'bridge', 'geometry']
        if 'layer' in paton_edges.columns: cols.append('layer')
        print(paton_edges[cols].head())
        
        # Check coordinates of the first edge
        first_geom = paton_edges.iloc[0].geometry
        bounds = first_geom.bounds
        print(f"Sample geometry bounds (Global Lat/Lon): {bounds}")
        
        # Verify conversion
        from services.global_center import GlobalCenter
        gc = GlobalCenter(50.40, 30.50) # Approx Kyiv center
        local_poly = first_geom
        
        # Simple transform simulation
        import shapely.ops
        def to_local(x, y, z=None):
            return gc.to_local(*gc.to_utm(x, y))
            
        local_geom = shapely.ops.transform(to_local, first_geom)
        print(f"Local Coords Bounds: {local_geom.bounds}")
        
        # Check against Zone Hex_43_39 approx bounds
        # Zone Center approx: 50.427, 30.571
        z_x, z_y = gc.to_local(*gc.to_utm(30.571, 50.427))
        print(f"Zone Center approx: ({z_x}, {z_y})")
        print(f"Bridge is approx {local_geom.centroid.distance(shapely.geometry.Point(z_x, z_y))} meters from zone center")

        
except Exception as e:
    print(f"Error: {e}")
