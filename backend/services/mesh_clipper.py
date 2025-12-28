"""
Утиліта для обрізання мешів по заданому bbox або полігону.
Використовується для видалення додаткової території по боках моделі.
"""
import numpy as np
import trimesh
from typing import Optional, Tuple, List
from shapely.geometry import Polygon, Point
from shapely.prepared import prep


def clip_mesh_to_bbox(
    mesh: trimesh.Trimesh,
    bbox: Tuple[float, float, float, float],
    tolerance: float = 0.001
) -> Optional[trimesh.Trimesh]:
    """
    Обрізає меш по заданому bbox (minx, miny, maxx, maxy).
    
    Args:
        mesh: Меш для обрізання
        bbox: Bounding box (minx, miny, maxx, maxy) в локальних координатах
        tolerance: Допуск для обрізання (в метрах)
    
    Returns:
        Обрізаний меш або None якщо обрізання не вдалося
    """
    if mesh is None or len(mesh.vertices) == 0:
        return mesh
    
    minx, miny, maxx, maxy = bbox
    
    # Розширюємо bbox на tolerance для безпеки
    minx -= tolerance
    miny -= tolerance
    maxx += tolerance
    maxy += tolerance
    
    try:
        # Створюємо обрізаючий box
        clip_box = trimesh.creation.box(
            extents=[maxx - minx, maxy - miny, 1000.0],  # Велика висота для обрізання по XY
            transform=trimesh.transformations.translation_matrix([
                (minx + maxx) / 2.0,
                (miny + maxy) / 2.0,
                0.0
            ])
        )
        
        # Обрізаємо меш по box
        # ВИПРАВЛЕННЯ: intersection може не працювати для складних мешів, використовуємо простий фільтр
        try:
            clipped = mesh.intersection(clip_box)
            if clipped is not None and len(clipped.vertices) > 0:
                return clipped
        except Exception:
            pass  # Fallback до простого фільтру
        
        # Fallback: простий фільтр вершин
        if True:  # Завжди використовуємо простий фільтр для надійності
            # Якщо intersection не спрацював, використовуємо простий фільтр вершин
            vertices = mesh.vertices.copy()
            faces = mesh.faces.copy()
            
            # Фільтруємо вершини, які в межах bbox
            mask = (
                (vertices[:, 0] >= minx) & (vertices[:, 0] <= maxx) &
                (vertices[:, 1] >= miny) & (vertices[:, 1] <= maxy)
            )
            
            if not np.any(mask):
                return None
            
            # Переіндексуємо вершини
            vertex_map = np.full(len(vertices), -1, dtype=np.int32)
            new_vertex_count = 0
            for i in range(len(vertices)):
                if mask[i]:
                    vertex_map[i] = new_vertex_count
                    new_vertex_count += 1
            
            # Фільтруємо грані, які мають всі вершини в межах bbox
            valid_faces = []
            for face in faces:
                if all(vertex_map[v] >= 0 for v in face):
                    valid_faces.append([vertex_map[v] for v in face])
            
            if len(valid_faces) == 0:
                return None
            
            # Створюємо новий меш
            new_vertices = vertices[mask]
            new_faces = np.array(valid_faces, dtype=np.int32)
            
            clipped = trimesh.Trimesh(vertices=new_vertices, faces=new_faces)
            clipped.fix_normals()
        
        return clipped
    
    except Exception as e:
        print(f"[WARN] Не вдалося обрізати меш: {e}")
        # Fallback: повертаємо оригінальний меш
        return mesh


def clip_all_meshes_to_bbox(
    mesh_items: list,
    bbox: Tuple[float, float, float, float],
    tolerance: float = 0.001
) -> list:
    """
    Обрізає всі меші в списку по заданому bbox.
    
    Args:
        mesh_items: Список кортежів (name, mesh)
        bbox: Bounding box (minx, miny, maxx, maxy) в локальних координатах
        tolerance: Допуск для обрізання (в метрах)
    
    Returns:
        Список обрізаних мешів
    """
    clipped_items = []
    for name, mesh in mesh_items:
        if mesh is None:
            continue
        
        clipped = clip_mesh_to_bbox(mesh, bbox, tolerance)
        if clipped is not None and len(clipped.vertices) > 0:
            clipped_items.append((name, clipped))
        else:
            print(f"[WARN] Меш '{name}' став порожнім після обрізання, пропускаємо")
    
    return clipped_items


def clip_mesh_to_polygon(
    mesh: trimesh.Trimesh,
    polygon_coords: List[Tuple[float, float]],
    global_center=None,
    tolerance: float = 0.001
) -> Optional[trimesh.Trimesh]:
    """
    Обрізає меш по заданому полігону (наприклад, шестикутнику).
    
    Args:
        mesh: Меш для обрізання
        polygon_coords: Список координат полігону [(lon, lat), ...] в WGS84
        global_center: GlobalCenter для перетворення координат в локальні
        tolerance: Допуск для обрізання (в метрах)
    
    Returns:
        Обрізаний меш або None якщо обрізання не вдалося
    """
    if mesh is None or len(mesh.vertices) == 0:
        return mesh
    
    if polygon_coords is None or len(polygon_coords) < 3:
        return mesh
    
    try:
        # Перетворюємо координати полігону з WGS84 в локальні координати
        if global_center is not None:
            # Конвертуємо (lon, lat) -> UTM -> локальні
            lons = [coord[0] for coord in polygon_coords]
            lats = [coord[1] for coord in polygon_coords]
            
            # Конвертуємо кожну точку в локальні координати
            local_coords = []
            for lon, lat in zip(lons, lats):
                try:
                    # Конвертуємо lat/lon -> UTM через global_center
                    x_utm, y_utm = global_center.to_utm(lon, lat)
                    # Конвертуємо UTM -> локальні
                    x_local, y_local = global_center.to_local(x_utm, y_utm)
                    local_coords.append((x_local, y_local))
                except Exception as e:
                    print(f"[WARN] Помилка перетворення координат полігону ({lon}, {lat}): {e}")
                    # Пропускаємо цю точку
            if len(local_coords) < 3:
                print(f"[WARN] Недостатньо точок для полігону після перетворення: {len(local_coords)}")
                return mesh
        else:
            # Якщо немає global_center, використовуємо координати як є (припускаємо, що вони вже локальні)
            local_coords = polygon_coords
        
        # Створюємо Shapely полігон
        try:
            polygon = Polygon(local_coords)
            if not polygon.is_valid:
                print(f"[WARN] Полігон невалідний, виправляємо через buffer(0)")
                polygon = polygon.buffer(0)
                if polygon.is_empty:
                    print(f"[WARN] Полігон став порожнім після buffer(0)")
                    return mesh
        except Exception as e:
            print(f"[WARN] Помилка створення полігону: {e}")
            return mesh

        # Трохи розширюємо полігон, щоб не "зрізати" рівно по межі (особливо після simplification)
        tol = float(tolerance or 0.0)
        if tol > 0:
            try:
                polygon = polygon.buffer(tol)
            except Exception:
                pass

        # ВАЖЛИВО: vertex-only фільтр лишає "висячі" трикутники поза зоною.
        # Краще: залишаємо faces, у яких centroid (XY) всередині полігону.
        vertices = np.asarray(mesh.vertices)
        faces = np.asarray(mesh.faces)
        if faces.size == 0:
            return None

        print(f"[DEBUG] Обрізання меша по полігону (face-centroid): {len(vertices)} вершин, {len(faces)} faces, полігон: {len(local_coords)} точок")
        try:
            geom = prep(polygon)
        except Exception:
            geom = polygon

        tri = vertices[faces]  # (F,3,3)
        centroids = tri[:, :, :2].mean(axis=1)  # (F,2)
        # shapely contains is strict; include touches to keep boundary
        keep = np.array([geom.contains(Point(xy[0], xy[1])) or geom.touches(Point(xy[0], xy[1])) for xy in centroids], dtype=bool)

        if not np.any(keep):
            return None

        kept_faces = faces[keep]
        clipped = trimesh.Trimesh(vertices=vertices.copy(), faces=kept_faces.copy(), process=False)
        try:
            clipped.remove_unreferenced_vertices()
        except Exception:
            pass
        try:
            clipped.fix_normals()
        except Exception:
            pass

        if len(clipped.vertices) == 0 or len(clipped.faces) == 0:
            return None
        return clipped
    
    except Exception as e:
        print(f"[WARN] Не вдалося обрізати меш по полігону: {e}")
        import traceback
        traceback.print_exc()
        # Fallback: повертаємо оригінальний меш
        return mesh

