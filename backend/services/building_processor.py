"""
Сервіс для обробки будівель з екструзією та покращеними дахами
Покращено: додано посадку будівель на рельєф через TerrainProvider
"""
import geopandas as gpd
import trimesh
import numpy as np
from shapely.geometry import Polygon, Point, MultiPolygon
from shapely.ops import transform
from typing import List, Optional
from services.terrain_provider import TerrainProvider
from services.global_center import GlobalCenter
import mapbox_earcut  # Для fallback методу extrude_building
import re
import gc  # For memory cleanup


def process_buildings(
    gdf_buildings: gpd.GeoDataFrame,
    min_height: float = 2.0,
    height_multiplier: float = 1.0,
    terrain_provider: Optional[TerrainProvider] = None,
    foundation_depth: float = 1.0,  # Глибина фундаменту в метрах (до масштабування)
    embed_depth: float = 0.0,       # Наскільки "втиснути" будівлю в землю (м), щоб не було щілин
    max_foundation_depth: Optional[float] = None,  # Запобіжник: максимальна глибина фундаменту (м)
    global_center: Optional[GlobalCenter] = None,  # Глобальний центр для перетворення координат (застарілий, використовується coordinates_already_local)
    coordinates_already_local: bool = False,  # ВИПРАВЛЕННЯ: якщо True, координати вже в локальних, не потрібно перетворювати
) -> List[trimesh.Trimesh]:
    """
    Обробляє будівлі, створюючи 3D меші з екструзією
    
    Args:
        gdf_buildings: GeoDataFrame з будівлями
        min_height: Мінімальна висота будівлі (метри)
        height_multiplier: Множник для висоти
    
    Returns:
        Список Trimesh об'єктів будівель
    """
    if gdf_buildings.empty:
        return []
    
    # ВИПРАВЛЕННЯ: Перетворюємо координати тільки якщо вони ще не в локальних
    # Якщо coordinates_already_local=True, координати вже перетворені в main.py
    if not coordinates_already_local and global_center is not None:
        try:
            print(f"[DEBUG] Перетворюємо gdf_buildings з UTM в локальні координати (fallback)")
            # Створюємо функцію трансформації для Shapely
            def to_local_transform(x, y, z=None):
                """Трансформер: UTM -> локальні координати"""
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)
            
            # Перетворюємо всі геометрії в локальні координати
            gdf_buildings_local = gdf_buildings.copy()
            gdf_buildings_local['geometry'] = gdf_buildings_local['geometry'].apply(
                lambda geom: transform(to_local_transform, geom) if geom is not None and not geom.is_empty else geom
            )
            gdf_buildings = gdf_buildings_local
            print(f"[DEBUG] Перетворено {len(gdf_buildings)} геометрій будівель в локальні координати (fallback)")
        except Exception as e:
            print(f"[WARN] Не вдалося перетворити gdf_buildings в локальні координати: {e}")
            import traceback
            traceback.print_exc()
    elif coordinates_already_local:
        print(f"[DEBUG] Координати будівель вже в локальних, пропускаємо перетворення")
    
    building_meshes = []

    def ground_heights_for_geom(g) -> np.ndarray:
        """
        ПОКРАЩЕНА ВЕРСІЯ: Адаптивний семплінг рельєфу для будівель.
        Використовує більше точок для великих будівель та складного рельєфу.
        ВАЖЛИВО: Рельєф під будівлями вже вирівняний через flatten_heightfield_under_buildings,
        але для точності використовуємо адаптивний семплінг.
        """
        if terrain_provider is None:
            return np.array([0.0], dtype=float)
        try:
            pts = []
            # Polygon або MultiPolygon
            polys = []
            if isinstance(g, Polygon):
                polys = [g]
            elif isinstance(g, MultiPolygon) or hasattr(g, "geoms"):
                try:
                    polys = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]
                except Exception:
                    polys = []

            if not polys:
                # fallback: хоча б центроїд
                c = g.centroid
                pts.append([c.x, c.y])
            else:
                for poly in polys:
                    if poly.exterior is None:
                        continue
                    
                    try:
                        minx, miny, maxx, maxy = poly.bounds
                        dx = float(maxx - minx)
                        dy = float(maxy - miny)
                        area = float(poly.area)
                        
                        # АДАПТИВНИЙ СЕМПЛІНГ: більше точок для великих будівель
                        # Для малих будівель (< 100 м²): мінімальний семплінг
                        # Для середніх (100-1000 м²): середній семплінг
                        # Для великих (> 1000 м²): щільний семплінг
                        
                        if area < 100.0:
                            # Малий: контур + кути + центр
                            coords = np.array(poly.exterior.coords)
                            if len(coords) > 0:
                                step = max(1, len(coords) // 8)
                                pts.extend(coords[::step, :2].tolist())
                        elif area < 1000.0:
                            # Середній: контур + регулярна сітка 3x3
                            coords = np.array(poly.exterior.coords)
                            if len(coords) > 0:
                                step = max(1, len(coords) // 16)
                                pts.extend(coords[::step, :2].tolist())
                            
                            # Регулярна сітка 3x3 всередині
                            for i in range(1, 4):
                                for j in range(1, 4):
                                    x = minx + (dx * i / 4.0)
                                    y = miny + (dy * j / 4.0)
                                    if poly.contains(Point(x, y)):
                                        pts.append([x, y])
                        else:
                            # Великий: контур + щільна сітка 5x5
                            coords = np.array(poly.exterior.coords)
                            if len(coords) > 0:
                                step = max(1, len(coords) // 32)
                                pts.extend(coords[::step, :2].tolist())
                            
                            # Щільна сітка 5x5 всередині
                            for i in range(1, 6):
                                for j in range(1, 6):
                                    x = minx + (dx * i / 6.0)
                                    y = miny + (dy * j / 6.0)
                                    if poly.contains(Point(x, y)):
                                        pts.append([x, y])
                        
                        # Завжди додаємо центроїд та кутові точки
                        c = poly.centroid
                        pts.append([c.x, c.y])
                        
                        corners = [
                            (minx, miny),  # Лівий нижній
                            (maxx, miny),  # Правий нижній
                            (maxx, maxy),  # Правий верхній
                            (minx, maxy),  # Лівий верхній
                        ]
                        for x, y in corners:
                            if poly.contains(Point(x, y)) or poly.touches(Point(x, y)):
                                pts.append([x, y])
                    except Exception:
                        # Fallback: хоча б центроїд
                        pass
                    try:
                        c = poly.centroid
                        pts.append([c.x, c.y])
                    except Exception:
                        pass
                    pass

            if len(pts) == 0:
                return np.array([0.0], dtype=float)

            pts_arr = np.array(pts, dtype=float)
            heights = terrain_provider.get_heights_for_points(pts_arr)
            if heights.size == 0:
                return np.array([0.0], dtype=float)
            heights = np.asarray(heights, dtype=float)
            heights = heights[np.isfinite(heights)]
            if heights.size == 0:
                return np.array([0.0], dtype=float)
            return heights
        except Exception as e:
            mz = float(getattr(terrain_provider, "min_z", 0.0))
            return np.array([mz], dtype=float)
    
    # MEMORY OPTIMIZATION: Process buildings in batches to reduce RAM usage
    # iterrows() is very slow and memory-intensive, so we process by indexing instead
    # Adaptive batch size: smaller batches for large datasets to avoid memory exhaustion
    total_buildings = len(gdf_buildings)
    if total_buildings > 5000:
        batch_size = 50  # Smaller batches for very large datasets
    elif total_buildings > 2000:
        batch_size = 75
    else:
        batch_size = 100  # Default batch size
    
    print(f"[INFO] Processing {total_buildings} buildings in batches of {batch_size}...")
    
    for batch_start in range(0, total_buildings, batch_size):
        batch_end = min(batch_start + batch_size, total_buildings)
        batch_indices = gdf_buildings.index[batch_start:batch_end]
        batch_gdf = gdf_buildings.loc[batch_indices]
        
        print(f"[INFO] Processing buildings batch {batch_start//batch_size + 1}/{(total_buildings + batch_size - 1)//batch_size} ({batch_start+1}-{batch_end}/{total_buildings})...")
        
        # Process this batch
        for idx in batch_indices:
            try:
                row = gdf_buildings.loc[idx]
                geom = row.geometry
                
                # Пропускаємо невалідні геометрії
                if geom is None:
                    continue
                
                # Перевіряємо валідність геометрії
                try:
                    if geom.is_empty:
                        continue
                    if not geom.is_valid:
                        # Спробуємо виправити геометрію
                        geom = geom.buffer(0)
                        if geom.is_empty:
                            continue
                        # Перевіряємо чи після виправлення геометрія має достатньо точок
                        if hasattr(geom, 'exterior') and len(geom.exterior.coords) < 3:
                            continue
                except Exception as e:
                    print(f"  [WARN] Помилка перевірки геометрії будівлі {idx}: {e}")
                    continue
                
                # Отримуємо висоту будівлі
                height = get_building_height(row, min_height) * height_multiplier

                # Розраховуємо foundation_depth_eff заздалегідь (для використання в обох гілках)
                foundation_depth_eff = max(float(foundation_depth), float(embed_depth), 0.1)
                if max_foundation_depth is not None:
                    try:
                        foundation_depth_eff = min(float(foundation_depth_eff), float(max_foundation_depth))
                    except Exception:
                        pass
                foundation_depth_eff = max(float(foundation_depth_eff), 0.05)

                # Якщо рельєфу нема — не "топимо" будівлі фундаментом у нуль,
                # достатньо мінімального embed (щоб не було щілини з плоскою базою).
                if terrain_provider is None:
                    translate_z = -float(embed_depth) if float(embed_depth) > 0 else 0.0
                else:
                    # СПРОЩЕНА ЛОГІКА: Беремо висоти безпосередньо з terrain mesh для координат будівлі
                    # ВАЖЛИВО: geom вже в локальних координатах (після перетворення через global_center)
                    # Рельєф під будівлями вже вирівняний через flatten_heightfield_under_buildings
                    heights = ground_heights_for_geom(geom)
                    
                    if heights.size == 0:
                        # Якщо не вдалося отримати висоти - використовуємо fallback
                        translate_z = -float(embed_depth) if float(embed_depth) > 0 else 0.0
                        print(f"  [WARN] Будівля {idx}: не вдалося отримати висоти рельєфу")
                    else:
                        # Використовуємо мінімальну висоту рельєфу (рельєф вирівняний, тому різниця мінімальна)
                        ground_min = float(np.min(heights))
                        ground_max = float(np.max(heights))
                        ground_mean = float(np.mean(heights))
                        
                        # ВИПРАВЛЕННЯ: Правильна логіка з safety_margin та embed_depth
                        # Мінімальний запас над землею (гарантуємо, що будівля не під землею)
                        safety_margin = 0.1  # 10см запас
                        
                        # base_z - рівень підлоги будівлі
                        # Якщо embed_depth > 0, то "втискаємо" будівлю в землю, але не нижче ground_min
                        # Гарантуємо: base_z >= ground_min (підлога не нижче мінімальної висоти рельєфу)
                        if float(embed_depth) > 0:
                            # Втискаємо в землю, але не нижче ground_min
                            base_z = max(ground_min - float(embed_depth), ground_min - safety_margin)
                        else:
                            # Не втискаємо, додаємо запас
                            base_z = ground_min + safety_margin
                        
                        # translate_z - Z координата нижньої точки будівлі (base_z мінус фундамент)
                        translate_z = float(base_z) - float(foundation_depth_eff)
                        
                        # Додаткова перевірка: translate_z не повинен бути нижче ground_min - foundation_depth_eff
                        # Це гарантує, що навіть з фундаментом будівля не буде під землею
                        min_allowed_z = ground_min - float(foundation_depth_eff) + safety_margin
                        if translate_z < min_allowed_z:
                            translate_z = min_allowed_z
                            base_z = translate_z + float(foundation_depth_eff)
                        
                        # Діагностика для складних випадків
                
                # Simplify geometry to speed up triangulation and reduce vertex count
                try:
                    # Simplify with 0.1m tolerance (preserves shape but removes redundant points)
                    geom = geom.simplify(0.1, preserve_topology=True)
                except Exception:
                    pass

                # Екструзія полігону (використовуємо trimesh.creation.extrude_polygon)
                if isinstance(geom, Polygon):
                    # Перевіряємо чи полігон має достатньо точок
                    if hasattr(geom, 'exterior') and len(geom.exterior.coords) < 3:
                        # print(f"  [SKIP] Будівля {idx}: полігон має менше 3 точок")
                        continue
                    
                    try:
                        # Використовуємо вбудовану функцію trimesh для екструзії
                        mesh = trimesh.creation.extrude_polygon(geom, height=height)
                        
                        if mesh is None or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
                            print(f"  [WARN] Будівля {idx}: extrude_polygon повернув порожній mesh")
                            # Fallback на старий метод
                            mesh = extrude_building(geom, height)
                            if mesh is None or len(mesh.faces) == 0:
                                continue
                        
                        # Садимо від translate_z (нижня точка будівлі)
                        mesh.apply_translation([0, 0, translate_z])
                        
                        if terrain_provider is not None and len(mesh.vertices) > 0:
                            vertices = mesh.vertices.copy()
                            
                            # ВИПРАВЛЕННЯ: Використовуємо нижні 20% висоти будівлі замість фіксованого порогу
                            building_height = float(height)
                            bottom_limit = translate_z + (building_height * 0.2)
                            
                            # Оптимізація: Перевіряємо тільки потенційно проблемні вершини (нижні)
                            # Це значно пришвидшує обробку, уникаючи семплінгу для даху
                            check_mask = vertices[:, 2] <= bottom_limit
                            
                            if np.any(check_mask):
                                check_indices = np.where(check_mask)[0]
                                check_xy = vertices[check_indices, :2]
                                check_z = vertices[check_indices, 2]
                                
                                ground_heights = terrain_provider.get_heights_for_points(check_xy)
                                
                                if len(ground_heights) > 0:
                                    # 5см допуск
                                    below_ground = check_z < (ground_heights - 0.05)
                                    
                                    if np.any(below_ground):
                                        # Знаходимо максимальне заглиблення
                                        buried_indices = np.where(below_ground)[0]
                                        # buried_indices індексують масив check_z/ground_heights (який є subset)
                                        
                                        # Нам потрібно знайти наскільки підняти
                                        # required_z = ground + epsilon
                                        # current = z
                                        # diff = required - current
                                        
                                        # Векторизовано для всіх заглиблених вершин
                                        needed_z = ground_heights[buried_indices] + 0.1
                                        curr_z = check_z[buried_indices]
                                        diffs = needed_z - curr_z
                                        
                                        max_diff = np.max(diffs)
                                        
                                        if max_diff > 0:
                                            # Піднімаємо всю будівлю
                                            vertices[:, 2] += max_diff
                                            translate_z += max_diff
                                            mesh.vertices = vertices
                        
                        # Перевірка на валідність mesh
                        try:
                            if not mesh.is_volume:
                                mesh.fill_holes()
                            mesh.remove_duplicate_faces()
                            mesh.remove_unreferenced_vertices()
                        except Exception as fix_error:
                            print(f"  [WARN] Будівля {idx}: помилка виправлення mesh: {fix_error}")
                        
                        if mesh and len(mesh.faces) > 0 and len(mesh.vertices) > 0:
                            building_meshes.append(mesh)
                        else:
                            print(f"  [SKIP] Будівля {idx}: mesh невалідний після обробки")
                    except Exception as e:
                        print(f"  [WARN] Помилка екструзії будівлі {idx}: {e}")
                        import traceback
                        traceback.print_exc()
                        # Fallback на старий метод
                        try:
                            mesh = extrude_building(geom, height)
                            if mesh:
                                mesh.apply_translation([0, 0, translate_z])
                                if len(mesh.faces) > 0:
                                    building_meshes.append(mesh)
                        except Exception:
                            pass
                # ВИПРАВЛЕННЯ: Якщо MultiPolygon, обробляємо кожен полігон окремо з ОКРЕМИМ translate_z
                elif hasattr(geom, 'geoms') or isinstance(geom, MultiPolygon):
                    geoms_list = geom.geoms if hasattr(geom, 'geoms') else [geom]
                    for poly_idx, poly in enumerate(geoms_list):
                        if not isinstance(poly, Polygon):
                            continue
                        
                        # Перевіряємо валідність полігону
                        try:
                            if poly.is_empty or not poly.is_valid:
                                poly = poly.buffer(0)
                                if poly.is_empty:
                                    continue
                            if hasattr(poly, 'exterior') and len(poly.exterior.coords) < 3:
                                continue
                        except Exception:
                            continue
                        
                        # ВИПРАВЛЕННЯ: Розраховуємо translate_z окремо для кожного полігону
                        poly_translate_z = translate_z  # Початкове значення
                        if terrain_provider is not None:
                            poly_heights = ground_heights_for_geom(poly)
                            if poly_heights.size > 0:
                                poly_ground_min = float(np.min(poly_heights))
                                poly_ground_max = float(np.max(poly_heights))
                                
                                # Така сама логіка як для одиночних полігонів
                                safety_margin = 0.1
                                if float(embed_depth) > 0:
                                    poly_base_z = max(poly_ground_min - float(embed_depth), poly_ground_min - safety_margin)
                                else:
                                    poly_base_z = poly_ground_min + safety_margin
                                
                                poly_translate_z = float(poly_base_z) - float(foundation_depth_eff)
                                
                                # Додаткова перевірка
                                min_allowed_z = poly_ground_min - float(foundation_depth_eff) + safety_margin
                                if poly_translate_z < min_allowed_z:
                                    poly_translate_z = min_allowed_z
                        
                        try:
                            mesh = trimesh.creation.extrude_polygon(poly, height=height)
                            
                            if mesh is None or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
                                # Fallback
                                mesh = extrude_building(poly, height)
                                if mesh is None or len(mesh.faces) == 0:
                                    continue
                            
                            mesh.apply_translation([0, 0, poly_translate_z])
                            
                            # Така сама покращена агресивна перевірка як для одиночних полігонів
                            if terrain_provider is not None and len(mesh.vertices) > 0:
                                vertices = mesh.vertices.copy()
                                
                                # Використовуємо нижні 20% висоти будівлі
                                building_height = float(height)
                                bottom_percentage = 0.2
                                bottom_threshold = poly_translate_z + (building_height * bottom_percentage)
                                is_bottom = vertices[:, 2] <= bottom_threshold
                                
                                if np.any(is_bottom):
                                    bottom_vertices_xy = vertices[is_bottom, :2]
                                    ground_heights = terrain_provider.get_heights_for_points(bottom_vertices_xy)
                                    
                                    # НОВА ЛОГІКА: Садимо на найнижчу точку, але збільшуємо висоту будівлі
                                    # Це гарантує, що фундамент стоїть на землі (не висить),
                                    # а дах не провалюється в схил (бо ми додали різницю висот).
                                    
                                    # 1. Знаходимо найнижчу точку рельєфу під будівлею
                                    min_ground_h = float(np.min(ground_heights))
                                    max_ground_h = float(np.max(ground_heights))
                                    slope_diff = max_ground_h - min_ground_h
                                    
                                    # 2. Визначаємо бажану базу (мінімальна точка + embed)
                                    # embed_depth тут працює як "заглиблення нижче найнижчої точки"
                                    desired_base_z = min_ground_h - max(float(embed_depth), 0.0)
                                    
                                    # 3. Перевіряємо поточну позицію
                                    current_bottom_z = float(np.min(vertices[:, 2]))
                                    
                                    # 4. Переміщаємо будівлю на нову базу
                                    # (зсуваємо вниз, якщо вона була вище)
                                    z_shift = desired_base_z - current_bottom_z
                                    vertices[:, 2] += z_shift
                                    # translate_z теж оновлюємо для подальших перевірок (хоча вони вже не треба)
                                    poly_translate_z += z_shift
                                    
                                    # 5. КРИТИЧНО: "Витягуємо" будівлю вгору, щоб компенсувати схил
                                    # Тобто вершини даху піднімаємо на slope_diff
                                    # Вершини, що були "зверху" (дах), мають z > (current_bottom_z + building_height * 0.9)
                                    # АЛЕ простіше: просто знаходимо верхні вершини
                                    
                                    # Припускаємо, що дах - це все, що вище "низу + 50% висоти"
                                    # (для простих коробок це працює, для складних форм може бути нюанс, 
                                    # але extrude_polygon робить вертикальні стіни, тому ок)
                                    roof_threshold = np.min(vertices[:, 2]) + (building_height * 0.5)
                                    is_roof = vertices[:, 2] > roof_threshold
                                    
                                    if np.any(is_roof):
                                        # Додаємо різницю висот до даху, щоб він був рівним відносно найвищої точки схилу
                                        vertices[is_roof, 2] += slope_diff
                                    
                                    mesh.vertices = vertices
                                    
                                    mesh.vertices = vertices
                                    
                                    try:
                                        mesh.fix_normals()
                                    except:
                                        pass
                            
                            # Перевірка на валідність mesh
                            try:
                                if not mesh.is_volume:
                                    mesh.fill_holes()
                                mesh.remove_duplicate_faces()
                                mesh.remove_unreferenced_vertices()
                            except Exception:
                                pass
                            
                            if mesh and len(mesh.faces) > 0 and len(mesh.vertices) > 0:
                                building_meshes.append(mesh)
                        except Exception as e:
                            # Fallback
                            try:
                                mesh = extrude_building(poly, height)
                                if mesh:
                                    mesh.apply_translation([0, 0, poly_translate_z])
                                    if mesh and len(mesh.faces) > 0:
                                        building_meshes.append(mesh)
                            except Exception:
                                continue
            except Exception as e:
                print(f"Помилка обробки будівлі {idx}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        # MEMORY OPTIMIZATION: Explicitly free batch data after processing
        del batch_gdf
        gc.collect()
        
        print(f"[INFO] Batch {batch_start//batch_size + 1} completed. Total buildings so far: {len(building_meshes)}")
    
    print(f"Створено {len(building_meshes)} будівель")
    return building_meshes


def get_building_height(row, min_height: float) -> float:
    """
    Визначає висоту будівлі з OSM тегів
    """
    # Спробуємо отримати висоту з тегів
    height = None

    def _parse_number(val) -> Optional[float]:
        if val is None:
            return None
        if isinstance(val, (int, float)) and not np.isnan(val):
            return float(val)
        if isinstance(val, str):
            s = val.strip().replace(",", ".")
            m = re.search(r"[-+]?\d+(\.\d+)?", s)
            if not m:
                return None
            try:
                return float(m.group(0))
            except Exception:
                return None
        return None
    
    def _parse_height_m(val) -> Optional[float]:
        """
        Повертає висоту в метрах.
        Підтримка: "20", "20m", "20 m", "65 ft", "65feet".
        """
        if val is None:
            return None
        if isinstance(val, (int, float)) and not np.isnan(val):
            return float(val)
        if isinstance(val, str):
            s = val.strip().lower().replace(",", ".")
            num = _parse_number(s)
            if num is None:
                return None
            # feet -> meters
            if "ft" in s or "feet" in s or "foot" in s:
                return float(num) * 0.3048
            return float(num)
        return None

    def _parse_levels(val) -> Optional[float]:
        """
        Рівні можуть бути "5", "5;6", "5-6". Беремо перше число.
        """
        return _parse_number(val)

    # 1) Явні висоти (height / building:height)
    for key in ["height", "building:height"]:
        if key in row:
            h = _parse_height_m(row.get(key))
            if h is not None and h > 0:
                height = max(float(height or 0.0), float(h))

    # 2) Рівні (levels) -> метри
    levels_m = None
    for key in ["building:levels", "building:levels:aboveground", "levels"]:
        if key in row:
            lv = _parse_levels(row.get(key))
            if lv is not None and lv > 0:
                # Класичне припущення: ~3м на поверх (стабільно і прогнозовано)
                levels_m = float(lv) * 3.0
                break
    if levels_m is not None:
        height = max(float(height or 0.0), float(levels_m))

    # 3) Roof додаємо, якщо є (в OSM часто окремо)
    roof_h = None
    for key in ["roof:height"]:
        if key in row:
            roof_h = _parse_height_m(row.get(key))
            break
    if roof_h is None and "roof:levels" in row:
        rv = _parse_levels(row.get("roof:levels"))
        if rv is not None and rv > 0:
            roof_h = float(rv) * 1.5
    if roof_h is not None and roof_h > 0:
        height = float(height or 0.0) + float(roof_h)

    # Якщо тегів нема — лишаємося на min_height (щоб поведінка була прогнозована)
    
    # Якщо висота не знайдена, використовуємо мінімальну
    if height is None or height < min_height:
        height = min_height
    
    return height


def extrude_building(polygon: Polygon, height: float) -> Optional[trimesh.Trimesh]:
    """
    Екструдує полігон будівлі на вказану висоту
    
    Args:
        polygon: Полігон будівлі
        height: Висота екструзії (метри)
    
    Returns:
        Trimesh об'єкт будівлі
    """
    try:
        # Отримуємо координати зовнішнього контуру
        exterior_coords = np.array(polygon.exterior.coords[:-1])  # Видаляємо дублікат
        
        # Створюємо верхню та нижню поверхні
        vertices_bottom = np.column_stack([
            exterior_coords[:, 0],
            exterior_coords[:, 1],
            np.zeros(len(exterior_coords))
        ])
        
        vertices_top = np.column_stack([
            exterior_coords[:, 0],
            exterior_coords[:, 1],
            np.full(len(exterior_coords), height)
        ])
        
        # Тріангуляція для верхньої та нижньої поверхонь
        try:
            coords_flat = exterior_coords.flatten().tolist()
            triangles_flat = mapbox_earcut.triangulate_float32(coords_flat, [])
            triangles = np.array(triangles_flat).reshape(-1, 3)
        except Exception as e:
            # Fallback: проста тріангуляція через трикутники від першої вершини
            n = len(exterior_coords)
            triangles = np.array([[0, i, (i+1)%n] for i in range(1, n-1)])
        
        # Всі вершини
        all_vertices = np.vstack([vertices_bottom, vertices_top])
        
        # Індекси для нижньої поверхні (обернені для правильного напрямку нормалі)
        bottom_faces = triangles[:, ::-1]
        
        # Індекси для верхньої поверхні (з зсувом)
        top_faces = triangles + len(vertices_bottom)
        
        # Бічні стіни (квадри з двох трикутників)
        n = len(exterior_coords)
        side_faces = []
        for i in range(n):
            next_i = (i + 1) % n
            # Квад складається з двох трикутників
            side_faces.append([i, i + n, next_i])
            side_faces.append([next_i, i + n, next_i + n])
        
        # Об'єднуємо всі грані
        all_faces = np.vstack([
            bottom_faces,
            top_faces,
            np.array(side_faces)
        ])
        
        # Створюємо меш
        mesh = trimesh.Trimesh(vertices=all_vertices, faces=all_faces)
        
        # Перевірка на валідність
        try:
            if not mesh.is_volume:
                # Спроба виправити
                mesh.fill_holes()
                mesh.update_faces(mesh.unique_faces())
                mesh.remove_unreferenced_vertices()
        except Exception as fix_error:
            # Якщо не вдалося виправити, все одно повертаємо меш
            print(f"Попередження при виправленні мешу: {fix_error}")
        
        return mesh
        
    except Exception as e:
        print(f"Помилка екструзії будівлі: {e}")
        import traceback
        traceback.print_exc()
        return None

