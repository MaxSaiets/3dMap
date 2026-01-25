"""
Green areas (parks/forests/grass) processor.

Creates a thin embossed mesh that is draped onto terrain:
new_z = ground_z + old_z - embed

This makes parks/green areas stand out visually and be printable (has thickness).
"""

from __future__ import annotations

from typing import Optional

import geopandas as gpd
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon, box, Point
from shapely.ops import transform, unary_union

from services.terrain_provider import TerrainProvider
from services.global_center import GlobalCenter


def _iter_polys(geom):
    """Yield Polygon parts from Polygon/MultiPolygon/GeometryCollection-ish inputs."""
    if geom is None:
        return []
    if isinstance(geom, Polygon):
        yield geom
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            if isinstance(g, Polygon):
                yield g
    elif hasattr(geom, "geoms"):
        try:
            for g in geom.geoms:
                if isinstance(g, Polygon):
                    yield g
        except Exception:
            pass


def _create_high_res_mesh(poly: Polygon, height_m: float, target_edge_len_m: float) -> Optional[trimesh.Trimesh]:
    """
    Створює меш з UNIFORM тріангуляцією (remeshed) використовуючи Steiner points.
    Виправляє проблему діагональних смуг, створюючи рівномірні трикутники.
    
    Підхід:
    1. Resample Boundary - додає точки на контур для точного накладання на рельєф
    2. Internal Grid - генерує рівномірну сітку всередині полігону
    3. Delaunay Triangulation - створює рівномірні трикутники
    4. Extrude - витягує в 3D з боковими стінками
    
    Args:
        poly: Вхідний полігон
        height_m: Висота екструзії
        target_edge_len_m: Цільова довжина ребра в метрах (максимальна)
    
    Returns:
        Trimesh об'єкт з високою деталізацією та рівномірною топологією
    """
    try:
        if poly is None or poly.is_empty:
            return None
        
        if target_edge_len_m <= 0:
            target_edge_len_m = 3.0
        
        # 1. RESAMPLE BOUNDARY (Виправляє нерівні краї)
        # Розбиваємо контур на дрібні відрізки для точного накладання на рельєф
        boundary_coords = []
        
        # Обробляємо зовнішній контур
        exterior_pts = np.array(poly.exterior.coords[:-1])  # Без останньої точки (дублікат першої)
        for i in range(len(exterior_pts)):
            p1 = exterior_pts[i]
            p2 = exterior_pts[(i + 1) % len(exterior_pts)]
            dist = np.linalg.norm(p2 - p1)
            
            if dist > target_edge_len_m:
                # Додаємо проміжні точки
                num_segments = int(np.ceil(dist / target_edge_len_m))
                t = np.linspace(0, 1, num_segments + 1)[:-1]  # Без останньої
                for val in t:
                    boundary_coords.append(p1 + (p2 - p1) * val)
            else:
                boundary_coords.append(p1)
        
        # Обробляємо внутрішні отвори (якщо є)
        for interior in poly.interiors:
            interior_pts = np.array(interior.coords[:-1])
            for i in range(len(interior_pts)):
                p1 = interior_pts[i]
                p2 = interior_pts[(i + 1) % len(interior_pts)]
                dist = np.linalg.norm(p2 - p1)
                
                if dist > target_edge_len_m:
                    num_segments = int(np.ceil(dist / target_edge_len_m))
                    t = np.linspace(0, 1, num_segments + 1)[:-1]
                    for val in t:
                        boundary_coords.append(p1 + (p2 - p1) * val)
                else:
                    boundary_coords.append(p1)
        
        # 2. GENERATE INTERNAL GRID (Виправляє діагональні смуги)
        # Створюємо рівномірну сітку всередині полігону
        minx, miny, maxx, maxy = poly.bounds
        
        # Створюємо сітку з кроком target_edge_len_m
        x_range = np.arange(minx, maxx, target_edge_len_m)
        y_range = np.arange(miny, maxy, target_edge_len_m)
        
        if len(x_range) == 0:
            x_range = np.array([minx, maxx])
        if len(y_range) == 0:
            y_range = np.array([miny, maxy])
        
        xx, yy = np.meshgrid(x_range, y_range)
        grid_points = np.vstack([xx.ravel(), yy.ravel()]).T
        
        # Фільтруємо точки всередині полігону (використовуємо prepared geometry для швидкості)
        try:
            from shapely.prepared import prep
            prep_poly = prep(poly)
            valid_points = []
            for pt in grid_points:
                if prep_poly.contains(Point(pt[0], pt[1])):
                    valid_points.append(pt)
        except ImportError:
            # Fallback без prepared geometry
            valid_points = []
            for pt in grid_points:
                if poly.contains(Point(pt[0], pt[1])):
                    valid_points.append(pt)
        
        # Об'єднуємо контурні та внутрішні точки
        if len(boundary_coords) == 0:
            # Fallback до простого extrude, якщо не вдалося створити точки
            return trimesh.creation.extrude_polygon(poly, height=float(height_m))
        
        all_points = np.array(boundary_coords + valid_points)
        
        # Видаляємо дублікати (точки, що дуже близькі одна до одної)
        # Використовуємо простий підхід: групуємо точки за округленими координатами
        tolerance = target_edge_len_m * 0.1  # 10% від цільової довжини
        unique_points = []
        seen = set()
        for pt in all_points:
            key = (round(pt[0] / tolerance), round(pt[1] / tolerance))
            if key not in seen:
                seen.add(key)
                unique_points.append(pt)
        
        if len(unique_points) < 3:
            # Fallback
            return trimesh.creation.extrude_polygon(poly, height=float(height_m))
        
        all_points = np.array(unique_points)
        
        # 3. DELAUNAY TRIANGULATION (Створює рівномірні трикутники)
        try:
            from scipy.spatial import Delaunay
            tri = Delaunay(all_points)
            
            # Delaunay створює Convex Hull, тому треба відкинути трикутники ПОЗА полігоном
            faces = tri.simplices
            vertices = tri.points
            
            # Використовуємо prepared geometry для швидкої перевірки
            try:
                prep_poly = prep(poly)
                contains_check = lambda pt: prep_poly.contains(Point(pt[0], pt[1]))
            except:
                contains_check = lambda pt: poly.contains(Point(pt[0], pt[1]))
            
            final_faces = []
            for face in faces:
                # Рахуємо центр трикутника
                centroid = np.mean(vertices[face], axis=0)
                
                # Check 1: Centroid inside polygon
                if contains_check(centroid):
                    # Check 2: Max edge length (remove long skinny triangles spanning gaps)
                    # This prevents artifacts in concave areas where Delaunay jumps across the gap
                    p1, p2, p3 = vertices[face]
                    max_edge = max(
                        np.linalg.norm(p1 - p2),
                        np.linalg.norm(p2 - p3),
                        np.linalg.norm(p3 - p1)
                    )
                    # Allow slightly larger edges than target, but not huge ones
                    if max_edge < target_edge_len_m * 2.5:
                        final_faces.append(face)
            
            if len(final_faces) == 0:
                # Fallback
                return trimesh.creation.extrude_polygon(poly, height=float(height_m))
            
            # Створюємо плоский 2D меш
            mesh_2d = trimesh.Trimesh(vertices=vertices, faces=np.array(final_faces))
            
        except ImportError:
            # Якщо scipy недоступний, використовуємо простий extrude з subdivision
            print("[WARN] scipy недоступний, використовується простий extrude з subdivision")
            mesh = trimesh.creation.extrude_polygon(poly, height=float(height_m))
            if mesh is None:
                return None
            
            # Адаптивний subdivision як fallback
            minx, miny, maxx, maxy = poly.bounds
            max_dim = max(maxx - minx, maxy - miny)
            needed_subdivisions = max(1, min(int(np.ceil(np.log2(max_dim / target_edge_len_m))), 6))
            
            for _ in range(needed_subdivisions):
                if len(mesh.vertices) > 150000:
                    break
                try:
                    mesh = mesh.subdivide()
                except Exception:
                    break
            return mesh
        
        # 4. EXTRUSION (Витягуємо в 3D з боковими стінками)
        # Створюємо нижні та верхні вершини
        n_verts = len(mesh_2d.vertices)
        v_bottom = np.column_stack((mesh_2d.vertices, np.zeros(n_verts)))
        v_top = np.column_stack((mesh_2d.vertices, np.full(n_verts, float(height_m))))
        
        # Об'єднуємо вершини
        vertices_3d = np.vstack((v_bottom, v_top))
        
        # Створюємо грані: нижня поверхня (flipped), верхня поверхня
        f_bottom = np.fliplr(mesh_2d.faces)  # Перевертаємо для правильної нормалі
        f_top = mesh_2d.faces + n_verts
        
        # Створюємо бокові стінки
        # Знаходимо boundary edges (ребра, що належать тільки одному трикутнику)
        edges = mesh_2d.edges
        edge_count = {}
        for face in mesh_2d.faces:
            for i in range(3):
                edge = tuple(sorted([face[i], face[(i + 1) % 3]]))
                edge_count[edge] = edge_count.get(edge, 0) + 1
        
        # Boundary edges - ті, що зустрічаються тільки один раз
        boundary_edges = [edge for edge, count in edge_count.items() if count == 1]
        
        # Створюємо бокові грані для кожного boundary edge
        side_faces = []
        for edge in boundary_edges:
            v1_bottom, v2_bottom = edge[0], edge[1]
            v1_top = v1_bottom + n_verts
            v2_top = v2_bottom + n_verts
            
            # Два трикутники для бокової грані (квад перетворюємо в 2 трикутники)
            side_faces.append([v1_bottom, v2_bottom, v1_top])
            side_faces.append([v2_bottom, v2_top, v1_top])
        
        # Об'єднуємо всі грані
        all_faces = np.vstack([
            f_bottom,
            f_top,
            np.array(side_faces) if side_faces else np.empty((0, 3), dtype=int)
        ])
        
        # Створюємо 3D меш
        mesh_3d = trimesh.Trimesh(vertices=vertices_3d, faces=all_faces)
        
        return mesh_3d
        
    except Exception as e:
        print(f"[WARN] Помилка створення High-Res мешу з Delaunay: {e}")
        import traceback
        traceback.print_exc()
        # Fallback до простого extrude
        try:
            return trimesh.creation.extrude_polygon(poly, height=float(height_m))
        except Exception:
            return None


def process_green_areas(
    gdf_green: gpd.GeoDataFrame,
    height_m: float,
    embed_m: float,
    terrain_provider: Optional[TerrainProvider] = None,
    global_center: Optional[GlobalCenter] = None,  # UTM -> local
    scale_factor: Optional[float] = None,  # model_mm / world_m
    min_feature_mm: float = 0.8,
    simplify_mm: float = 0.4,
    # --- НОВИЙ АРГУМЕНТ: Полігони доріг для вирізання ---
    road_polygons: Optional[object] = None,  # Shapely Polygon/MultiPolygon об'єднаних доріг (в локальних координатах)
    # --- НОВИЙ АРГУМЕНТ: Полігони води для вирізання ---
    water_polygons: Optional[object] = None,
    # Зниження деталізації для парків
    target_edge_len_m: Optional[float] = None,
) -> Optional[trimesh.Trimesh]:
    if gdf_green is None or gdf_green.empty:
        return None

    # --- Coordinate Transform Block ---
    if global_center is not None:
        try:
            def to_local_transform(x, y, z=None):
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)

            gdf_local = gdf_green.copy()
            gdf_local["geometry"] = gdf_local["geometry"].apply(
                lambda geom: transform(to_local_transform, geom) if geom is not None and not geom.is_empty else geom
            )
            gdf_green = gdf_local
        except Exception:
            pass

    # --- Masks Preparation ---
    # Road Mask
    road_mask = road_polygons
    if road_mask is not None:
        try:
            if getattr(road_mask, "is_empty", False):
                road_mask = None
        except Exception:
            pass
        
        if road_mask is not None and global_center is not None:
            try:
                bounds = road_mask.bounds
                sample_x = bounds[0]
                if abs(sample_x) > 100000:
                    def to_local_transform(x, y, z=None):
                        x_local, y_local = global_center.to_local(x, y)
                        return (x_local, y_local, z) if z is not None else (x_local, y_local)
                    road_mask = transform(to_local_transform, road_mask)
                    if road_mask is None or getattr(road_mask, "is_empty", False):
                        road_mask = None
            except Exception as e:
                print(f"[WARN] Помилка перетворення road_polygons в локальні координати: {e}")
                road_mask = None

    # Water Mask
    water_mask = water_polygons
    if water_mask is not None:
        try:
            if getattr(water_mask, "is_empty", False):
                water_mask = None
        except Exception:
            pass
        # Assuming water_mask passed from main is already local if generated from local geometries
        # (It is, based on main.py logic using water_geometries_local)

    # --- Clipping Block ---
    clip_box = None
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip_box = box(min_x, min_y, max_x, max_y)
        except Exception:
            clip_box = None

    # --- Parameters Calculation ---
    simplify_tol_m = 0.5
    min_width_m = None
    target_edge_len_m = float(target_edge_len_m) if target_edge_len_m is not None else 3.0
    
    if scale_factor is not None and float(scale_factor) > 0:
        try:
            simplify_tol_m = max(0.05, float(simplify_mm) / float(scale_factor))
        except Exception:
            pass
        try:
            min_width_m = max(0.0, float(min_feature_mm) / float(scale_factor))
        except Exception:
            pass

    # Filter & Simplify
    collected_items = [] # (polygon, row)
    for idx, row in gdf_green.iterrows():
        geom = row.geometry
        if geom is None or getattr(geom, "is_empty", False): continue
        
        # 1. Clip to Bbox
        if clip_box is not None:
             try:
                 geom = geom.intersection(clip_box)
             except: continue
        
        # 2. Subtract Roads
        if road_mask is not None:
             try:
                  geom = geom.difference(road_mask)
             except: pass

        # 3. Subtract Water (CRITICAL FIX for water under terrain)
        if water_mask is not None:
             try:
                  geom = geom.difference(water_mask)
             except: pass
        
        if geom is None or getattr(geom, "is_empty", False): continue

        for p in _iter_polys(geom):
             if p and not p.is_empty:
                  try:
                      p = p.simplify(float(simplify_tol_m), preserve_topology=True)
                  except: pass
                  
                  if p and not p.is_empty and p.area > 2.0:
                       collected_items.append((p, row))

    if not collected_items:
        return None

    # --- MESH GENERATION ---
    meshes: list[trimesh.Trimesh] = []
    
    for poly, row in collected_items:
        try:
            is_paved = False
            is_pier = False
            is_cemetery = False
            is_transition = False # construction, brownfield
            
            # Helper to check tags safely
            def check_tag(key, values):
                if key in row and str(row[key]) in values: return True
                return False

            # Paved checks
            if check_tag('amenity', ['parking', 'marketplace', 'university', 'school']): is_paved = True
            if check_tag('place', ['square']): is_paved = True
            if check_tag('landuse', ['plaza', 'commercial', 'retail', 'railway']): is_paved = True
            if check_tag('highway', ['pedestrian']): is_paved = True
            if check_tag('man_made', ['pier', 'breakwater', 'groyne']): is_paved = True; is_pier = True
            if check_tag('railway', ['station', 'platform']): is_paved = True
            
            # Restaurants / Food (Treat as paved/road color as requested)
            if check_tag('amenity', ['restaurant', 'cafe', 'fast_food', 'bar', 'pub', 'food_court', 'ice_cream', 'bicycle_parking', 'shelter']):
                is_paved = True

            # Cemeteries (Distinct "not green" color)
            if check_tag('amenity', ['grave_yard']) or check_tag('landuse', ['cemetery', 'religious']):
                is_cemetery = True
                is_paved = False # Distinct handling

            # Transition / Industrial / Construction / Farmland
            if check_tag('landuse', ['construction', 'brownfield', 'industrial', 'garages', 'farmland', 'farmyard', 'orchard', 'vineyard', 'greenhouse_horticulture']):
                is_transition = True
                is_paved = True # Treat as gray/paved

            # 1. High Res Mesh
            mesh = _create_high_res_mesh(poly, float(height_m), target_edge_len_m)
            if mesh is None or len(mesh.vertices) == 0: continue

            # 2. Draping
            if terrain_provider is not None:
                v = mesh.vertices.copy()
                old_z = v[:, 2].copy()
                ground_heights = terrain_provider.get_surface_heights_for_points(v[:, :2])
                ground_heights = np.nan_to_num(ground_heights, nan=0.0)

                z_min = float(np.min(old_z))
                z_range = float(np.max(old_z)) - z_min
                
                relative_height = np.zeros_like(old_z)
                if z_range > 1e-6:
                    relative_height = (old_z - z_min) / z_range
                
                new_z = ground_heights - float(embed_m) + relative_height * float(height_m)
                
                if is_pier: new_z += 1.0 # Lift piers
                
                if not is_pier:
                    safety_margin = 0.01 
                    min_allowed_z = ground_heights + safety_margin - float(embed_m)
                    new_z = np.maximum(new_z, min_allowed_z)
                    
                    z_fighting_offset = float(height_m) * 0.005
                    top_vertices_mask = relative_height > 0.9
                    if np.any(top_vertices_mask):
                        new_z[top_vertices_mask] = new_z[top_vertices_mask] - z_fighting_offset
                
                v[:, 2] = new_z
                mesh.vertices = v
        
            # 3. Texture / Color
            final_color = np.array([34, 139, 34, 255], dtype=np.uint8) # Default Green
            use_texture = False

            if is_pier:
                 final_color = np.array([160, 140, 100, 255], dtype=np.uint8) # Wood
            elif is_cemetery:
                 final_color = np.array([90, 90, 85, 255], dtype=np.uint8) # Stone/Dark Grey for Cemetery
            elif is_paved or is_transition:
                 final_color = np.array([50, 50, 50, 255], dtype=np.uint8) # Dark Gray (almost Black) for Paved/Restaurants
            else:
                 # Green
                 final_color = np.array([34, 139, 34, 255], dtype=np.uint8)
                 use_texture = True

            if len(mesh.faces) > 0:
                 face_colors = np.tile(final_color, (len(mesh.faces), 1))
                 mesh.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)
            
            if use_texture:
                mesh = _add_strong_faceted_texture(mesh, height_m, scale_factor, original_polygon=poly)

            if len(mesh.faces) > 0:
                meshes.append(mesh)

        except Exception as e:
            continue

    if not meshes:
        return None

    try:
        return trimesh.util.concatenate(meshes)
    except Exception:
        return meshes[0]


def _add_strong_faceted_texture(
    mesh: trimesh.Trimesh, 
    height_m: float, 
    scale_factor: Optional[float] = None,
    original_polygon: Optional[Polygon] = None
) -> trimesh.Trimesh:
    """
    Додає Low Poly текстуру з урахуванням маски країв (Boundary Masking).
    
    Вершини на краях полігону отримують weight = 0 (чисті краї для стикування з дорогами),
    вершини в центрі отримують weight = 1 (повний шум для Low Poly ефекту).
    
    Args:
        mesh: Меш після накладання на рельєф
        height_m: Висота мешу в метрах (для fallback розрахунків)
        scale_factor: Масштаб моделі (model_mm / world_m) для print-aware розрахунків
        original_polygon: Оригінальний полігон для обчислення відстані до краю
    
    Returns:
        Меш з доданою Low Poly текстурою
    """
    try:
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            return mesh

        # Знаходимо "дах" (верхні вершини)
        if mesh.vertex_normals is None or len(mesh.vertex_normals) != len(mesh.vertices):
            mesh.fix_normals()
        
        up_facing = mesh.vertex_normals[:, 2] > 0.5
        
        # Fallback якщо нормалі погані
        if not np.any(up_facing):
            z_values = mesh.vertices[:, 2]
            max_z = float(np.max(z_values))
            min_z = float(np.min(z_values))
            z_range = max_z - min_z
            if z_range < 0.01:
                return mesh
            threshold = min_z + z_range * 0.80
            up_facing = z_values > threshold

        if not np.any(up_facing):
            return mesh

        top_indices = np.where(up_facing)[0]
        top_vertices_xy = mesh.vertices[top_indices, :2]

        # Параметри текстури
        target_noise_mm = 0.5  # Бажана висота шуму на моделі (0.5мм)
        texture_amplitude = min(height_m * 0.4, 3.0)  # default fallback
        fade_distance_m = 2.0  # Дистанція від краю, де починається шум (в метрах)

        if scale_factor is not None and float(scale_factor) > 0:
            texture_amplitude = target_noise_mm / float(scale_factor)
            texture_amplitude = min(texture_amplitude, 3.0)
            # fade distance теж залежить від масштабу
            # ~2-3мм на моделі для плавного переходу
            fade_distance_m = max(2.0, 3.0 / float(scale_factor))

        # --- Boundary Masking (Маска країв) ---
        noise_weights = np.ones(len(top_indices), dtype=float)
        
        if original_polygon is not None and not original_polygon.is_empty:
            try:
                # Shapely boundary для розрахунку відстані
                boundary = original_polygon.boundary
                
                # Для кожної верхньої вершини обчислюємо відстань до краю
                # Це може бути повільно для 50k+ точок, але необхідно для якості
                for i, (x, y) in enumerate(top_vertices_xy):
                    pt = Point(x, y)
                    d = boundary.distance(pt)
                    
                    if d < fade_distance_m:
                        # Плавний перехід (smoothstep)
                        t = d / fade_distance_m  # 0..1
                        weight = t * t * (3.0 - 2.0 * t)  # smoothstep
                        noise_weights[i] = weight
                    # else 1.0 (default - повний шум в центрі)
                    
            except Exception as e:
                print(f"[WARN] Помилка обчислення boundary masking: {e}")
                # Fallback: просто занулюємо самий край, якщо вдасться визначити
                pass

        # Генерація шуму
        np.random.seed(42)
        seed_base = int((np.sum(top_vertices_xy[:, 0]) + np.sum(top_vertices_xy[:, 1])) * 1000) % (2**31)
        np.random.seed(seed_base)
        
        noise = (np.random.random(len(top_indices)) - 0.5) * 2.0 * texture_amplitude
        
        # Застосування маски (шум тільки в центрі, краї залишаються чистими)
        masked_noise = noise * noise_weights
        
        mesh.vertices[top_indices, 2] += masked_noise
        
        # Фінальний штрих - оновити нормалі для Flat Shading
        mesh.fix_normals()

        return mesh
    except Exception as e:
        print(f"[WARN] Помилка застосування Low Poly текстури: {e}")
        import traceback
        traceback.print_exc()
        return mesh
