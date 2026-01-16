"""
Сервіс для обробки доріг.
Версія: FINAL FIXED (Layer separation logic).
Виправляє: зникнення доріг під естакадами, артефакти на розв'язках, обрізання країв.
"""
import osmnx as ox
import trimesh
import numpy as np
import warnings
import geopandas as gpd
from typing import Optional, List, Tuple

from shapely.ops import unary_union, transform, snap
from shapely.geometry import Polygon, MultiPolygon, box, LineString, Point

from services.terrain_provider import TerrainProvider
from services.global_center import GlobalCenter
from services.mesh_quality import improve_mesh_for_3d_printing, validate_mesh_for_3d_printing

warnings.filterwarnings('ignore', category=DeprecationWarning, module='pandas')
warnings.filterwarnings('ignore', category=UserWarning, module='shapely')


def create_bridge_supports(
    bridge_polygon: Polygon,
    bridge_deck_z: float,
    terrain_provider: Optional[TerrainProvider],
    water_level: Optional[float],
    support_spacing: float = 25.0,
    support_width: float = 2.5,
    min_support_height: float = 2.0,
) -> List[trimesh.Trimesh]:
    """Генерує опори моста."""
    supports = []
    if bridge_polygon is None or terrain_provider is None:
        return supports

    try:
        bounds = bridge_polygon.bounds
        minx, miny, maxx, maxy = bounds
        width_geo = maxx - minx
        height_geo = maxy - miny
        
        support_positions = []
        if width_geo > height_geo:
            rows = [miny + support_width, maxy - support_width]
            steps = max(2, int(width_geo / support_spacing))
            cols = np.linspace(minx + support_width, maxx - support_width, steps)
            for x in cols:
                for y in rows: support_positions.append((x, y))
        else:
            cols = [minx + support_width, maxx - support_width]
            steps = max(2, int(height_geo / support_spacing))
            rows = np.linspace(miny + support_width, maxy - support_width, steps)
            for y in rows:
                for x in cols: support_positions.append((x, y))

        for x, y in support_positions:
            try:
                pt = Point(x, y)
                if not bridge_polygon.buffer(1.0).contains(pt): continue

                offset = support_width / 2.0
                samples = np.array([[x, y], [x+offset, y], [x-offset, y], [x, y+offset], [x, y-offset]])
                ground_zs = terrain_provider.get_surface_heights_for_points(samples)
                min_ground = float(np.min(ground_zs))

                if water_level is not None and min_ground < water_level + 0.5:
                    base_z = water_level
                    is_in_water = True
                else:
                    base_z = float(np.mean(ground_zs))
                    is_in_water = False

                height = bridge_deck_z - base_z
                
                # ФІЛЬТР: Не ставимо опори для низьких естакад, якщо це не вода
                if not is_in_water and height < 5.0: continue
                if height < min_support_height: continue

                supp = trimesh.creation.box(
                    extents=[support_width, support_width, height],
                    transform=trimesh.transformations.translation_matrix([x, y, base_z + height/2.0])
                )
                
                color = np.array([100, 100, 100, 255], dtype=np.uint8)
                supp.visual = trimesh.visual.ColorVisuals(face_colors=np.tile(color, (len(supp.faces), 1)))
                supports.append(supp)
            except: continue
    except: pass
    return supports


def detect_bridges(
    gdf_edges: gpd.GeoDataFrame,
    water_geometries: Optional[List] = None,
    bridge_tag: str = 'bridge',
    bridge_buffer_m: float = 12.0,
) -> List[Tuple[object, object, float, bool, int]]:
    """
    Повертає список: (line, area, height, is_water, layer_int)
    """
    bridges = []
    if gdf_edges is None or gdf_edges.empty: return bridges

    water_union = None
    if water_geometries:
        try:
            valid = [g for g in water_geometries if g is not None and not g.is_empty]
            if valid: water_union = unary_union(valid)
        except: pass

    for _, row in gdf_edges.iterrows():
        try:
            geom = row.geometry
            if geom is None or geom.is_empty: continue

            is_bridge = False
            is_over_water = False
            bridge_height = 4.0 
            layer_val = 0

            def check(val):
                s = str(val).lower() if val is not None else ""
                return s in {'yes', 'true', '1', 'viaduct', 'aqueduct'} or s.startswith('viaduct')
            
            def get_f(val):
                try: return float(val)
                except: return None

            # Layer detection
            l_raw = get_f(row.get('layer'))
            if l_raw is not None: layer_val = int(l_raw)

            # 1. Tags
            if bridge_tag in row and check(row.get(bridge_tag)):
                is_bridge = True
                btype = str(row.get('bridge:type', '')).lower()
                if 'suspension' in btype: bridge_height = 12.0
                elif 'arch' in btype: bridge_height = 8.0
                else: bridge_height = 6.0
            
            if not is_bridge and (check(row.get('bridge:structure')) or check(row.get('man_made'))):
                is_bridge = True

            # 2. Layer Logic (Layer >= 1 is a bridge)
            if not is_bridge and layer_val >= 1:
                is_bridge = True
                bridge_height = max(5.0, layer_val * 6.0)

            # 3. Water Logic
            if water_union is not None and geom.intersects(water_union):
                is_over_water = True
                if not is_bridge:
                    is_bridge = True
                    bridge_height = max(bridge_height, 6.0)

            if is_bridge:
                b_line = geom
                if is_over_water and water_union:
                    try:
                        inter = geom.intersection(water_union)
                        if not inter.is_empty: b_line = inter
                    except: pass
                
                if hasattr(b_line, 'geoms'):
                    b_line = max(b_line.geoms, key=lambda g: g.length, default=geom)

                try:
                    b_area = b_line.buffer(bridge_buffer_m, cap_style=2, join_style=2)
                    if not b_area.is_empty:
                        bridges.append((b_line, b_area, bridge_height, is_over_water, layer_val))
                except: pass

        except: continue

    return bridges


def build_road_polygons(G_roads, width_multiplier=1.0, min_width_m=None, extra_buffer_m=0.0):
    if G_roads is None: return None
    gdf = G_roads if isinstance(G_roads, gpd.GeoDataFrame) else ox.graph_to_gdfs(G_roads, nodes=False)
    
    width_map = {'motorway': 12, 'motorway_link': 10, 'trunk': 10, 'trunk_link': 8, 'primary': 8, 'secondary': 7, 'tertiary': 6, 'residential': 5, 'service': 3.5, 'footway': 2.5}
    polys = []
    
    for _, row in gdf.iterrows():
        try:
            geom = row.geometry
            if not geom or geom.is_empty: continue
            
            hw = row.get('highway')
            if isinstance(hw, list): hw = hw[0]
            base_w = width_map.get(hw, 4.0) * width_multiplier
            if min_width_m: base_w = max(base_w, min_width_m)
            
            radius = (base_w / 2.0) + float(extra_buffer_m)
            p = geom.buffer(radius, cap_style=2, join_style=2)
            if not p.is_empty: polys.append(p)
        except: continue

    if not polys: return None
    try: return unary_union(polys)
    except: return polys[0]


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
) -> Optional[trimesh.Trimesh]:
    
    if G_roads is None: return None

    # 1. Transform Coords (FORCE LOCAL)
    gdf_edges = G_roads if isinstance(G_roads, gpd.GeoDataFrame) else ox.graph_to_gdfs(G_roads, nodes=False)
    if global_center:
        try:
            def to_local(x, y, z=None):
                xl, yl = global_center.to_local(x, y)
                return (xl, yl, z) if z else (xl, yl)
            
            gdf_edges = gdf_edges.copy()
            gdf_edges['geometry'] = gdf_edges['geometry'].apply(
                lambda g: transform(to_local, g) if g and not g.is_empty else g
            )
            # Update inputs
            G_roads = gdf_edges
            if water_geometries:
                water_geometries = [transform(to_local, g) for g in water_geometries if g]
        except: pass

    # 2. Detect Bridges
    # bridges = [(line, area, height, is_water, layer), ...]
    bridges = detect_bridges(gdf_edges, water_geometries)
    
    # 3. Build Polygons (Base Geometry)
    if merged_roads is None:
        merged_roads = build_road_polygons(gdf_edges, width_multiplier, min_width_m)

    if merged_roads is None or merged_roads.is_empty: return None

    # 4. Safe Clipping (fix edges issue)
    if terrain_provider:
        minx, maxx, miny, maxy = terrain_provider.get_bounds()
        # Великий запас (500м), щоб точно не обрізати дороги, що входять в карту
        clip_box = box(minx - 500, miny - 500, maxx + 500, maxy + 500)
        try:
            if not merged_roads.is_valid: merged_roads = merged_roads.buffer(0)
            merged_roads = merged_roads.intersection(clip_box)
        except: pass

    if merged_roads is None or merged_roads.is_empty: return None

    # --- STRATEGY: LAYER SEPARATION ---
    # Ми генеруємо меші в 3 проходи, щоб уникнути конфліктів.
    
    # Групи мостів
    # Low bridges (Layer 1 + Water): Вони замінюють землю.
    bridges_low = [b for b in bridges if b[4] == 1 or b[3]] 
    # High bridges (Layer 2+): Вони летять над землею.
    bridges_high = [b for b in bridges if b[4] >= 2]

    # Маски
    mask_low = unary_union([b[1] for b in bridges_low]) if bridges_low else None
    mask_high = unary_union([b[1] for b in bridges_high]) if bridges_high else None

    road_meshes = []

    # Helper to mesh a single part
    def _create_mesh(poly, mode="ground", bridge_meta=None):
        rh = max(float(road_height), 0.1)
        re = max(0.0, float(road_embed))
        
        try: mesh = trimesh.creation.extrude_polygon(poly, height=rh)
        except: return
        if not len(mesh.vertices): return
        
        if terrain_provider:
            v = mesh.vertices.copy()
            old_z = v[:, 2].copy()
            ground = terrain_provider.get_surface_heights_for_points(v[:, :2])
            
            GLOBAL_BIAS = 0.05
            
            if mode == "ground":
                # Земля: слідуємо за рельєфом
                v[:, 2] = ground - re + GLOBAL_BIAS + old_z
                col = [40, 40, 40, 255]
            
            elif mode == "bridge":
                b_line, _, b_h, b_water, b_layer = bridge_meta
                
                # Визначаємо висоту деки
                # Для Layer 1 (Low) - це може бути рампа, але для простоти робимо плоскою
                # Для Layer 2+ (High) - точно плоска високо над землею
                
                max_ground = np.max(ground)
                # Базова висота моста
                deck_z = max_ground + (b_h * bridge_height_multiplier)
                
                # Гарантуємо, що міст не нижче за дорогу, яка до нього підходить
                safe_min = np.max(ground) + GLOBAL_BIAS + 0.5
                if deck_z < safe_min: deck_z = safe_min

                # Верхні вершини
                top_mask = old_z > (rh * 0.5)
                v[top_mask, 2] = deck_z
                # Нижні вершини (робимо товсту плиту)
                v[~top_mask, 2] = deck_z - rh

                # Опори
                clearance = deck_z - np.mean(ground)
                # Для високих мостів або води
                if b_water or clearance > 5.0:
                    w_level = None
                    if b_water and hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider:
                        og = terrain_provider.original_heights_provider.get_heights_for_points(v[:,:2])
                        w_level = np.median(og) - 0.5
                    
                    road_meshes.extend(create_bridge_supports(poly, deck_z - rh, terrain_provider, w_level))
                
                col = [90, 90, 90, 255]

            mesh.vertices = v
            mesh.visual = trimesh.visual.ColorVisuals(face_colors=np.tile(col, (len(mesh.faces), 1)))
            road_meshes.append(mesh)

    # --- EXECUTION LOOP ---
    
    # Розбиваємо дороги на прості полігони
    if isinstance(merged_roads, Polygon): all_polys = [merged_roads]
    elif hasattr(merged_roads, 'geoms'): all_polys = list(merged_roads.geoms)
    else: all_polys = []

    for poly in all_polys:
        if poly.area < 0.001: continue

        # 1. GENERATE HIGH BRIDGES (Overlay)
        # Ми не вирізаємо їх з полігону, просто малюємо поверх.
        if mask_high and poly.intersects(mask_high):
            try:
                high_parts = poly.intersection(mask_high)
                geoms = high_parts.geoms if hasattr(high_parts, 'geoms') else [high_parts]
                for p in geoms:
                    if p.area < 0.1: continue
                    # Знаходимо метадані
                    match = next((b for b in bridges_high if b[1].intersects(p)), None)
                    if match: _create_mesh(p, "bridge", match)
            except: pass

        # 2. GENERATE LOW BRIDGES (Replacement)
        # Ми будемо їх малювати, а потім вирізати з землі
        if mask_low and poly.intersects(mask_low):
            try:
                low_parts = poly.intersection(mask_low)
                geoms = low_parts.geoms if hasattr(low_parts, 'geoms') else [low_parts]
                for p in geoms:
                    if p.area < 0.1: continue
                    match = next((b for b in bridges_low if b[1].intersects(p)), None)
                    if match: _create_mesh(p, "bridge", match)
            except: pass

        # 3. GENERATE GROUND ROADS (Remaining)
        # Земля = Полігон МІНУС (Низькі мости). Високі мости не чіпаємо!
        # Це вирішує проблему обриву дороги під естакадою.
        ground_poly = poly
        if mask_low:
            try:
                ground_poly = poly.difference(mask_low)
            except: pass # Якщо помилка, малюємо все як землю (краще ніж нічого)
        
        geoms = ground_poly.geoms if hasattr(ground_poly, 'geoms') else [ground_poly]
        for p in geoms:
            if p.area < 0.001: continue
            _create_mesh(p, "ground")

    if not road_meshes: return None
    try:
        combined = trimesh.util.concatenate(road_meshes)
        return improve_mesh_for_3d_printing(combined, aggressive=True)
    except: return road_meshes[0]

