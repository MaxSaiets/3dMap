"""
Сервіс для обробки доріг з буферизацією та об'єднанням
Покращена версія з фізичною шириною доріг та підтримкою мостів
Використовує trimesh.creation.extrude_polygon для надійної тріангуляції
"""
import osmnx as ox
import trimesh
import numpy as np
import warnings
from shapely.ops import unary_union, transform, snap
from shapely.geometry import Polygon, MultiPolygon, box, LineString, Point
from typing import Optional, List, Tuple
import geopandas as gpd
from services.terrain_provider import TerrainProvider
from services.global_center import GlobalCenter
from services.mesh_quality import improve_mesh_for_3d_printing, validate_mesh_for_3d_printing
from scipy.spatial import cKDTree

# Придушення deprecation warnings
warnings.filterwarnings('ignore', category=DeprecationWarning, module='pandas')

def densify_geometry(geom, max_segment_length=10.0):
    """
    Vectorized densification using Shapely 2.0 segmentize.
    """
    if geom is None or geom.is_empty:
        return geom
    try:
        # Shapely 2.0 optimization (C-level speed)
        return geom.segmentize(max_segment_length)
    except AttributeError:
        # Fallback for older Shapely versions (if any)
        # But realistically we expect Shapely 2.0+
        return geom


def create_bridge_supports(
    bridge_polygon: Polygon,
    bridge_height: float,
    terrain_provider: Optional[TerrainProvider],
    water_level: Optional[float],
    support_spacing: float = 20.0,
    support_width: float = 2.0,
    min_support_height: float = 1.0,
) -> List[trimesh.Trimesh]:
    """
    Створює опори для моста.
    """
    supports = []
    
    if bridge_polygon is None or terrain_provider is None:
        return supports
    
    try:
        bounds = bridge_polygon.bounds
        minx, miny, maxx, maxy = bounds
        center_x = (minx + maxx) / 2.0
        center_y = (miny + maxy) / 2.0
        
        width = maxx - minx
        height = maxy - miny
        
        support_positions = []
        
        if width > height:
            edge_y_positions = [miny + support_width, maxy - support_width]
            num_center_supports = max(0, int((width - 40) / support_spacing))
            if num_center_supports > 0:
                center_x_positions = np.linspace(minx + 20, maxx - 20, num_center_supports)
                for cx in center_x_positions:
                    for ey in edge_y_positions:
                        support_positions.append((cx, ey))
            for ey in edge_y_positions:
                support_positions.append((minx + support_width, ey))
                support_positions.append((maxx - support_width, ey))
            if width <= 40:
                num_supports = max(2, int(width / support_spacing) + 1)
                support_x_positions = np.linspace(minx + support_width, maxx - support_width, num_supports)
                for sx in support_x_positions:
                    support_positions.append((sx, center_y))
        else:
            edge_x_positions = [minx + support_width, maxx - support_width]
            num_center_supports = max(0, int((height - 40) / support_spacing))
            if num_center_supports > 0:
                center_y_positions = np.linspace(miny + 20, maxy - 20, num_center_supports)
                for cy in center_y_positions:
                    for ex in edge_x_positions:
                        support_positions.append((ex, cy))
            for ex in edge_x_positions:
                support_positions.append((ex, miny + support_width))
                support_positions.append((ex, maxy - support_width))
            if height <= 40:
                num_supports = max(2, int(height / support_spacing) + 1)
                support_y_positions = np.linspace(miny + support_width, maxy - support_width, num_supports)
                for sy in support_y_positions:
                    support_positions.append((center_x, sy))
        
        support_positions = list(set(support_positions))
        
        for i, (x, y) in enumerate(support_positions):
            try:
                pt = Point(x, y)
                if not bridge_polygon.contains(pt) and not bridge_polygon.touches(pt):
                    continue
                
                support_half = support_width / 2.0
                sample_points = np.array([
                    [x - support_half, y - support_half],
                    [x + support_half, y - support_half],
                    [x - support_half, y + support_half],
                    [x + support_half, y + support_half],
                    [x, y]
                ])
                
                ground_zs = terrain_provider.get_surface_heights_for_points(sample_points)
                ground_z = float(np.mean(ground_zs))
                min_ground_z_sample = float(np.min(ground_zs))
                
                if water_level is not None and min_ground_z_sample < water_level:
                    support_base_z = water_level
                else:
                    support_base_z = ground_z
                
                support_height = bridge_height - support_base_z
                
                if support_height < 4.0:
                    continue
                
                if support_height < min_support_height:
                    support_height = max(min_support_height, 0.5)
                
                support_mesh = trimesh.creation.box(
                    extents=[support_width, support_width, support_height],
                    transform=trimesh.transformations.translation_matrix([x, y, support_base_z + support_height / 2.0])
                )
                
                if support_mesh is not None and len(support_mesh.vertices) > 0:
                    support_color = np.array([120, 120, 120, 255], dtype=np.uint8)
                    if len(support_mesh.faces) > 0:
                        face_colors = np.tile(support_color, (len(support_mesh.faces), 1))
                        support_mesh.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)
                    supports.append(support_mesh)
            except Exception:
                continue
        
    except Exception as e:
        print(f"  [WARN] Помилка створення опор для моста: {e}")
    
    return supports

def detect_bridges(
    G_roads,
    water_geometries: Optional[List] = None,
    bridge_tag: str = 'bridge',
    bridge_buffer_m: float = 12.0,
    clip_polygon: Optional[object] = None,
) -> List[Tuple[object, object, float, bool, int]]:
    """
    Визначає мости. 
    """
    bridges = []
    
    if G_roads is None:
        return bridges
    
    bridges = []
    # USER REQUEST: Disable all bridge logic. Treat everything as ground roads.
    # The "Pontoon" logic in Ground loop will handle lifting them over water.
    print("[INFO] Bridge detection DISABLED by user request. All roads will be processed as ground/pontoon.")
    return bridges

    # === DISABLED LOGIC BELOW ===
    water_union = None

    if water_geometries:
        try:
            water_polys = []
            for wg in water_geometries:
                if wg is not None and not getattr(wg, "is_empty", False):
                    if isinstance(wg, Polygon): water_polys.append(wg)
                    elif hasattr(wg, 'geoms'): water_polys.extend(wg.geoms)
            if water_polys:
                water_union = unary_union(water_polys)
        except Exception: pass
    
    for idx, row in gdf_edges.iterrows():
        try:
            geom = row.geometry
            if geom is None: continue
            
            # --- FIX: Filter by clip_polygon (REMOVED) ---
            # Видалено перевірку clip_polygon. 
            # Довіряємо вхідним даним (G_roads), які вже завантажені для bbox зони.
            # Це гарантує, що мости на межах не зникнуть.
            
            
            is_bridge = False
            bridge_height = 2.0
            is_over_water = False
            layer_val = 0
            
            def _is_bridge_value(v):
                return str(v).lower() in {"yes", "true", "1", "viaduct", "aqueduct"}
            
            if bridge_tag in row and _is_bridge_value(row.get(bridge_tag)): is_bridge = True
            
            try:
                if float(row.get("layer", 0)) >= 1: is_bridge = True; layer_val = int(row.get("layer"))
            except: pass

            if not is_bridge and water_union is not None:
                if geom.intersects(water_union):
                    if geom.intersection(water_union).length >= 1.0:
                        is_bridge = True; is_over_water = True

            if is_bridge:
                ramp_line = geom
                if water_union is not None:
                    try:
                        inter = geom.intersection(water_union)
                        if inter and not inter.is_empty:
                            ramp_line = inter
                    except: pass
                
                if getattr(ramp_line, "geom_type", "") == "MultiLineString":
                    ramp_line = max(list(ramp_line.geoms), key=lambda g: getattr(g, "length", 0.0), default=geom)

                full_geom = densify_geometry(geom, max_segment_length=10.0)
                
                try:
                    bridge_area = full_geom.buffer(float(bridge_buffer_m), cap_style=2, join_style=2, resolution=4)
                except:
                    bridge_area = full_geom.buffer(float(bridge_buffer_m))
                
                if bridge_area and not bridge_area.is_empty:
                    final_height = max(bridge_height, float(layer_val) * 6.0)
                    if final_height < 4.0 and layer_val >= 1: final_height = 4.0
                    if is_over_water and final_height < 6.0: final_height = 8.0

                    bridge_name = row.get('name', 'Unknown')
                    bridges.append((ramp_line, bridge_area, final_height, is_over_water, layer_val, False, False, bridge_name))
                    
        except Exception:
            continue
            
            print(f"[INFO] Визначено {len(bridges)} мостів.")
    # Log bridge names for debugging
    bridge_names = [b[7] for b in bridges if len(b) > 7]
    if bridge_names:
        print(f"[DEBUG] Bridge names ({len(bridge_names)}): {bridge_names[:10]} ...")

    return bridges

def build_road_polygons(
    G_roads,
    width_multiplier: float = 1.0,
    min_width_m: Optional[float] = None,
    extra_buffer_m: float = 0.5, # Default extra buffer to overlap segments slightly
) -> Optional[object]:
    """
    Builds merged road polygons (2D).
    """
    if G_roads is None:
        return None

    gdf_edges = None
    if isinstance(G_roads, gpd.GeoDataFrame):
        gdf_edges = G_roads
    else:
        if not hasattr(G_roads, "edges") or len(G_roads.edges) == 0:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)

    width_map = {
        'motorway': 14, 'motorway_link': 12, 'trunk': 14, 'trunk_link': 12,
        'primary': 12, 'primary_link': 10, 'secondary': 10, 'secondary_link': 8,
        'tertiary': 8, 'tertiary_link': 6, 'residential': 6, 'living_street': 5,
        'service': 4, 'unclassified': 4, 'footway': 2, 'path': 2,
        'cycleway': 2.5, 'pedestrian': 3, 'steps': 2, 'track': 3, 
        'rail': 3, 'tram': 3, 'light_rail': 3, 'subway': 3, 'monorail': 3, # Railway mappings
        'narrow_gauge': 2, 'preserved': 3
    }

    def get_width(row):
        highway = row.get('highway')
        if isinstance(highway, list):
            highway = highway[0] if highway else None
        elif not highway:
            return 3.0
        try:
            # FORCE BRIDGES TO BE WIDE
            # Check if this row is a bridge using common OSM tags
            is_bridge_segment = False
            # Check 'bridge' column
            if 'bridge' in row:
                val = str(row['bridge']).lower()
                if val in ['yes', 'true', '1', 'viaduct', 'aqueduct']:
                    is_bridge_segment = True
            
            # Also check 'layer' > 0 (often implies bridge/overpass)
            if not is_bridge_segment and 'layer' in row:
                try:
                    if float(row['layer']) >= 1: is_bridge_segment = True
                except: pass

            width = width_map.get(highway, 3.0)
            
            # If it's a bridge, ensure it's at least trunk width (14m)
            if is_bridge_segment:
                width = max(width, 14.0)

            width = width * width_multiplier
            if min_width_m is not None:
                width = max(float(width), float(min_width_m))
        except Exception: pass
        return (width / 2.0) + float(extra_buffer_m)

    if 'highway' in gdf_edges.columns:
        gdf_edges = gdf_edges.copy()
        # Vectorized densification via apply (wrapping C-function)
        # Note: GeoPandas .apply for geometry is slower than direct C calls but segmentize is fast.
        # Ideally: gdf_edges["geometry"] = gdf_edges.geometry.segmentize(15.0) in generic geopandas 0.13+
        try:
             gdf_edges["geometry"] = gdf_edges.geometry.segmentize(15.0)
        except AttributeError:
             gdf_edges["geometry"] = gdf_edges["geometry"].apply(lambda g: densify_geometry(g, max_segment_length=15.0))
             
        widths = gdf_edges.apply(get_width, axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            # Higher resolution (16) for smoother curves and intersections
            gdf_edges["geometry"] = gdf_edges.geometry.buffer(widths, cap_style=1, join_style=1, resolution=16)
    else:
        gdf_edges = gdf_edges.copy()
        try:
             gdf_edges["geometry"] = gdf_edges.geometry.segmentize(15.0)
        except AttributeError:
             gdf_edges["geometry"] = gdf_edges["geometry"].apply(lambda g: densify_geometry(g, max_segment_length=15.0))
        
        width = 3.0 * width_multiplier
        if min_width_m:
            width = max(width, float(min_width_m))
        rad = (width / 2.0) + float(extra_buffer_m)
        gdf_edges["geometry"] = gdf_edges.geometry.buffer(rad, cap_style=1, join_style=1, resolution=16)

    try:
        # Pre-clean geometries before union to avoid "eating" intersections
        # valid_geoms = [g.buffer(0) for g in gdf_edges.geometry.values if g is not None and not g.is_empty]
        # unary_union handles this internally usually, but explicit cleaning helps
        merged = unary_union(gdf_edges.geometry.values)
        # Final cleanup
        if not merged.is_valid:
            merged = merged.buffer(0)
        return merged
    except Exception as e:
        print(f"[WARN] Failed to merge road polygons: {e}")
        return None

def process_roads(
    G_roads,
    width_multiplier: float = 1.0,
    terrain_provider: Optional[TerrainProvider] = None,
    road_height: float = 1.0,
    road_embed: float = 0.0,
    merged_roads: Optional[object] = None,
    water_geometries: Optional[List] = None,
    bridge_height_multiplier: float = 1.0,
    global_center: Optional[GlobalCenter] = None,
    min_width_m: Optional[float] = None,
    clip_polygon: Optional[object] = None,
    city_cache_key: Optional[str] = None,
) -> Optional[trimesh.Trimesh]:
    """
    Обробляє дорожню мережу.
    Strategy: Independent Overlay (Decoupled).
    1. Generate Ground Roads (continuous).
    2. Generate Bridges (layered on top).
    """
    if G_roads is None: return None

    # 1. GeoDataFrame
    gdf_edges = None
    if isinstance(G_roads, gpd.GeoDataFrame): gdf_edges = G_roads.copy()
    else:
        if not hasattr(G_roads, "edges") or len(G_roads.edges) == 0: return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False).copy()

    # 2. Conversion
    if global_center is not None and not gdf_edges.empty:
        try:
            sample = gdf_edges.iloc[0].geometry
            if abs(sample.bounds[0]) < 1000.0:
                print("[INFO] Конвертація доріг в метри...")
                def to_local(x, y, z=None):
                    lx, ly = global_center.to_local(x, y)
                    return (lx, ly, z) if z is not None else (lx, ly)
                gdf_edges["geometry"] = gdf_edges["geometry"].apply(
                    lambda g: transform(to_local, g) if g else g
                )
            else:
                # Це вже UTM (метри), але глобальні. Треба перевести в локальні (відносно центру)
                print("[INFO] Координати вже в UTM. Виконуємо зсув до локального центру...")
                cx, cy = global_center.get_center_utm()
                def shift_to_local(x, y, z=None):
                    return (x - cx, y - cy, z) if z is not None else (x - cx, y - cy)
                
                gdf_edges["geometry"] = gdf_edges["geometry"].apply(
                    lambda g: transform(shift_to_local, g) if g is not None and not g.is_empty else g
                )
        except: pass

    # 3. Merged Roads (Ground)
    if merged_roads is None:
        merged_roads = build_road_polygons(gdf_edges, width_multiplier, min_width_m)
    if merged_roads is None: return None

    # 4. Clip Zone (DISABLED FOR DEBUGGING)
    # if clip_polygon:
    #     merged_roads = merged_roads.intersection(clip_polygon)
    
    # 5. Clip Terrain (DISABLED FOR DEBUGGING)
    # if terrain_provider:
        # min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
        # merged_roads = merged_roads.intersection(box(min_x-200, min_y-200, max_x+200, max_y+200))

    # Force print bounds
    if merged_roads is not None:
        print(f"[DEBUG] Merged Roads Bounds: {merged_roads.bounds}")

    # Polygons list
    road_geoms = []
    if getattr(merged_roads, "geom_type", "") == "Polygon": road_geoms = [merged_roads]
    elif getattr(merged_roads, "geom_type", "") == "MultiPolygon": road_geoms = list(merged_roads.geoms)
    elif getattr(merged_roads, "geom_type", "") == "GeometryCollection":
        road_geoms = [g for g in merged_roads.geoms if g.geom_type == "Polygon"]

    if not road_geoms: return None

    # 6. Detect Bridges
    bridges = detect_bridges(gdf_edges, water_geometries=water_geometries, clip_polygon=clip_polygon)
    
    print(f"Генерація 3D (Полігонів доріг: {len(road_geoms)}, Знайдено мостів: {len(bridges)})...")
    print("  -> Стратегія: Незалежна генерація (Overlay). Мости накладаються поверх доріг.")
    
    road_meshes = []
    stats = {'bridge': 0, 'ground': 0, 'anti_drown': 0}

    # === 1. ГЕНЕРАЦІЯ ЗЕМЛІ (GROUND ROADS) ===
    # === 1. ГЕНЕРАЦІЯ ЗЕМЛІ (GROUND ROADS) ===
    print(f"  [1/2] Обробка наземних доріг ({len(road_geoms)} полігонів)...")

    # Helper to flatten and cleanup geometry BEFORE processing
    def _clean_and_flatten(g):
        if g is None or g.is_empty: return []
        if g.geom_type == 'Polygon':
            if not g.is_valid:
                return _clean_and_flatten(g.buffer(0))
            return [g]
        elif g.geom_type in ['MultiPolygon', 'GeometryCollection']:
            out = []
            for sub in g.geoms:
                out.extend(_clean_and_flatten(sub))
            return out
        return []

    # Flatten inputs first
    valid_polys = []
    for raw_poly in road_geoms:
        if raw_poly.area < 0.1: continue
        valid_polys.extend(_clean_and_flatten(raw_poly))

    print(f"    -> Flattened to {len(valid_polys)} simple valid polygons.")

    for poly in valid_polys:
        if poly.area < 0.1: continue
        try:
            # Densify (segmentize) - safe effectively because poly is valid
            p_poly = densify_geometry(poly, max_segment_length=10.0)
            
            # Final check - unlikely to fail but safety first
            # If segmentize broke validity (rare), buffer(0) might split it again
            polys_to_extrude = [p_poly]
            if not p_poly.is_valid:
                fixed = p_poly.buffer(0)
                if fixed.geom_type == 'Polygon': polys_to_extrude = [fixed]
                elif fixed.geom_type == 'MultiPolygon': polys_to_extrude = list(fixed.geoms)

            for final_p in polys_to_extrude:
                if final_p.area < 0.1: continue

                rh = max(float(road_height), 0.1)
                # trimesh expects a single Polygon
                mesh = trimesh.creation.extrude_polygon(final_p, height=rh)
                
                if mesh is None or len(mesh.vertices) == 0: continue

                if terrain_provider is not None:
                    vertices = mesh.vertices.copy()
                    old_z = vertices[:, 2].copy()
                    
                    ground_z = terrain_provider.get_surface_heights_for_points(vertices[:, :2])
                    if np.any(np.isnan(ground_z)):
                        valid_mask = ~np.isnan(ground_z)
                        fill = np.nanmedian(ground_z[valid_mask]) if np.any(valid_mask) else 0.0
                        ground_z = np.nan_to_num(ground_z, nan=fill)
                    
                    vertices[:, 2] = ground_z + old_z
                    stats['ground'] += 1
                    
                    # Anti-drown (Pontoon) - ROBUST VERSION
                    if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider:
                        orig_z = terrain_provider.original_heights_provider.get_heights_for_points(vertices[:, :2])
                        orig_z = np.nan_to_num(orig_z, nan=0.0)
                        
                        # Calculate depth (original water surface - carved riverbed)
                        depth = orig_z - ground_z
                        drown_mask = depth > 0.5 # If water is deeper than 0.5m
                        
                        if np.any(drown_mask):
                            # Lift exactly to water surface + 1.0m (Pontoon bridge style)
                            vertices[drown_mask, 2] = orig_z[drown_mask] + old_z[drown_mask] + 1.0
                            stats['anti_drown'] += np.sum(drown_mask)

                    mesh.vertices = vertices

                color = [40, 40, 40, 255]
                if len(mesh.faces) > 0:
                    mesh.visual = trimesh.visual.ColorVisuals(face_colors=np.tile(color, (len(mesh.faces), 1)))
                
                road_meshes.append(mesh)

        except Exception as e:
            # print(f"[WARN] Failed to process ground road poly: {e}")
            continue

    # === 2. ГЕНЕРАЦІЯ МОСТІВ (BRIDGES) ===
    print(f"  [2/2] Обробка мостів ({len(bridges)} об'єктів)...")
    
    for i, b in enumerate(bridges):
        try:
            # b = (ramp_line, bridge_area, final_height, is_over_water, layer_val, ...)
            bridge_area = b[1]
            if bridge_area is None or bridge_area.is_empty: continue
            
            p_poly = densify_geometry(bridge_area, max_segment_length=10.0)
            if not p_poly.is_valid: p_poly = p_poly.buffer(0)
            
            h_off = float(b[2]) * bridge_height_multiplier
            rh = max(float(road_height), 0.1)
            
            mesh = trimesh.creation.extrude_polygon(p_poly, height=rh)
            if mesh is None or len(mesh.vertices) == 0: continue
            
            try: mesh.fix_normals() 
            except: pass
            
            if terrain_provider:
                vertices = mesh.vertices.copy()
                old_z = vertices[:, 2].copy()
                
                ground_z = terrain_provider.get_surface_heights_for_points(vertices[:, :2])
                if np.any(np.isnan(ground_z)):
                    valid_mask = ~np.isnan(ground_z)
                    fill = np.nanmedian(ground_z[valid_mask]) if np.any(valid_mask) else 0.0
                    ground_z = np.nan_to_num(ground_z, nan=fill)

                base_z = np.median(ground_z) + max(h_off, 6.0)
                is_over_water = b[3]
                water_level = 0.0
                
                if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider:
                    orig_z = terrain_provider.original_heights_provider.get_heights_for_points(vertices[:, :2])
                    orig_z = np.nan_to_num(orig_z, nan=0.0)
                    water_level = np.median(orig_z)

                if is_over_water:
                    min_height_over_water = max(h_off, 6.0) 
                    target_z = water_level + min_height_over_water
                    if base_z < target_z:
                        print(f"    [Bridge #{i}] Deep water fix: {base_z:.1f}m -> {target_z:.1f}m (bed @ {np.median(ground_z):.1f}m)")
                        base_z = target_z
                elif water_level > base_z - 3.0:
                    base_z = max(base_z, water_level + 5.0)
                
                vertices[:, 2] = base_z + old_z
                mesh.vertices = vertices
                
            color = [60, 60, 60, 255]
            if len(mesh.faces) > 0:
                mesh.visual = trimesh.visual.ColorVisuals(face_colors=np.tile(color, (len(mesh.faces), 1)))
            
            road_meshes.append(mesh)
            stats['bridge'] += 1
            
            if terrain_provider and base_z - np.min(ground_z) > 3.0:
                try:
                    supps = create_bridge_supports(p_poly, base_z, terrain_provider, None, 35.0, 2.0, 2.0)
                    if supps: road_meshes.extend(supps)
                except: pass

        except Exception as e:
            print(f"[WARN] Failed to generate bridge {i}: {e}")

    if not road_meshes: return None
    
    print(f"Фіналізація: об'єднання {len(road_meshes)} елементів...")
    print(f"Stats: Bridges={stats['bridge']}, Ground={stats['ground']}, Pontoon Fixes={stats['anti_drown']}")
    
    try: return trimesh.util.concatenate(road_meshes)
    except: return road_meshes[0] if road_meshes else None
