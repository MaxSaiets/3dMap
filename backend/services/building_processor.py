"""
Сервіс для обробки будівель з екструзією та покращеними дахами
Покращено: додано посадку будівель на рельєф через TerrainProvider
"""
import geopandas as gpd
import trimesh
import numpy as np
from shapely.geometry import Polygon, Point, MultiPolygon
from typing import List, Optional
from services.terrain_provider import TerrainProvider
import mapbox_earcut  # Для fallback методу extrude_building
import re


def process_buildings(
    gdf_buildings: gpd.GeoDataFrame,
    min_height: float = 2.0,
    height_multiplier: float = 1.0,
    terrain_provider: Optional[TerrainProvider] = None,
    foundation_depth: float = 1.0,  # Глибина фундаменту в метрах (до масштабування)
    embed_depth: float = 0.0,       # Наскільки "втиснути" будівлю в землю (м), щоб не було щілин
    max_foundation_depth: Optional[float] = None,  # Запобіжник: максимальна глибина фундаменту (м)
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
    
    building_meshes = []

    def ground_heights_for_geom(g) -> np.ndarray:
        """
        Семплимо рельєф по контуру + кілька внутрішніх точок і повертаємо масив heights.
        Важливо: якщо max недооцінено (мало точок), будівля може частково "впасти" в текстуру на горбах.
        Тому беремо достатньо точок, але без фанатизму (щоб не було повільно).
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
                    coords = np.array(poly.exterior.coords)
                    if len(coords) > 0:
                        step = max(1, len(coords) // 64)
                        pts.extend(coords[::step, :2].tolist())
                    c = poly.centroid
                    pts.append([c.x, c.y])

                    # Додаткові внутрішні точки (для poly), щоб не "косило" на горбах
                    try:
                        minx, miny, maxx, maxy = poly.bounds
                        dx = float(maxx - minx)
                        dy = float(maxy - miny)
                        ox = 0.2 * dx
                        oy = 0.2 * dy
                        candidates = [
                            (c.x + ox, c.y),
                            (c.x - ox, c.y),
                            (c.x, c.y + oy),
                            (c.x, c.y - oy),
                        ]
                        for x, y in candidates:
                            if poly.contains(Point(float(x), float(y))):
                                pts.append([x, y])
                    except Exception:
                        pass

            pts_arr = np.array(pts, dtype=float)
            heights = terrain_provider.get_heights_for_points(pts_arr)
            if heights.size == 0:
                return np.array([0.0], dtype=float)
            heights = np.asarray(heights, dtype=float)
            heights = heights[np.isfinite(heights)]
            if heights.size == 0:
                return np.array([0.0], dtype=float)
            return heights
        except Exception:
            mz = float(getattr(terrain_provider, "min_z", 0.0))
            return np.array([mz], dtype=float)
    
    for idx, row in gdf_buildings.iterrows():
        try:
            geom = row.geometry
            
            # Пропускаємо невалідні геометрії
            if geom is None:
                continue
            
            # Перевіряємо валідність геометрії
            try:
                if not geom.is_valid:
                    # Спробуємо виправити геометрію
                    geom = geom.buffer(0)
            except:
                continue
            
            # Отримуємо висоту будівлі
            height = get_building_height(row, min_height) * height_multiplier

            # Якщо рельєфу нема — не "топимо" будівлі фундаментом у нуль,
            # достатньо мінімального embed (щоб не було щілини з плоскою базою).
            if terrain_provider is None:
                translate_z = -float(embed_depth) if float(embed_depth) > 0 else 0.0
            else:
                # Отримуємо семпли висот під будівлею
                heights = ground_heights_for_geom(geom)
                ground_min = float(np.min(heights))
                ground_max = float(np.max(heights))

                # Ключова зміна:
                # - НЕ робимо фундамент = slope_span, бо це дає “будівлі дуже під землею” (як на скріні).
                # - Беремо "референс" висоту як високий перцентиль, щоб не реагувати на один пік/шум DEM,
                #   але й не “топити” будівлю від ground_max.
                ground_ref = float(np.quantile(heights, 0.90)) if heights.size > 1 else ground_max
                base_z = ground_ref - float(embed_depth)

                foundation_depth_eff = float(foundation_depth)
                foundation_depth_eff = max(foundation_depth_eff, float(embed_depth))

                if max_foundation_depth is not None:
                    try:
                        foundation_depth_eff = min(float(foundation_depth_eff), float(max_foundation_depth))
                    except Exception:
                        pass

                foundation_depth_eff = max(float(foundation_depth_eff), 0.05)
                translate_z = float(base_z) - float(foundation_depth_eff)
            
            # Екструзія полігону (використовуємо trimesh.creation.extrude_polygon)
            if isinstance(geom, Polygon):
                try:
                    # Використовуємо вбудовану функцію trimesh для екструзії
                    mesh = trimesh.creation.extrude_polygon(geom, height=height)
                    
                    # Садимо від base_z і додаємо фундамент вниз (без "закопування" на метри)
                    mesh.apply_translation([0, 0, translate_z])
                    
                    if mesh and len(mesh.faces) > 0:
                        building_meshes.append(mesh)
                except Exception as e:
                    print(f"  Помилка екструзії будівлі {idx}: {e}")
                    # Fallback на старий метод
                    mesh = extrude_building(geom, height)
                    if mesh:
                        mesh.apply_translation([0, 0, translate_z])
                        if len(mesh.faces) > 0:
                            building_meshes.append(mesh)
            # Якщо MultiPolygon, обробляємо кожен полігон окремо
            elif hasattr(geom, 'geoms'):
                for poly in geom.geoms:
                    if isinstance(poly, Polygon):
                        try:
                            mesh = trimesh.creation.extrude_polygon(poly, height=height)
                            mesh.apply_translation([0, 0, translate_z])
                            if mesh and len(mesh.faces) > 0:
                                building_meshes.append(mesh)
                        except Exception as e:
                            # Fallback
                            mesh = extrude_building(poly, height)
                            if mesh:
                                mesh.apply_translation([0, 0, translate_z])
                                if mesh and len(mesh.faces) > 0:
                                    building_meshes.append(mesh)
        except Exception as e:
            print(f"Помилка обробки будівлі {idx}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
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

