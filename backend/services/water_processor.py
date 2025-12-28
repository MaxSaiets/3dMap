"""
Сервіс для обробки водних об'єктів з булевим відніманням
"""
import geopandas as gpd
import trimesh
import numpy as np
from shapely.geometry import Polygon, box, Point
from shapely.ops import transform
from typing import Optional
from services.terrain_provider import TerrainProvider
from services.global_center import GlobalCenter


def process_water(
    gdf_water: gpd.GeoDataFrame,
    depth_mm: float = 2.0,  # мм (для UI/сумісності)
    depth_meters: Optional[float] = None,  # якщо задано — використовуємо як "метри до масштабування"
    terrain_provider: Optional[TerrainProvider] = None,
    # backward compatibility:
    depth: Optional[float] = None,
) -> Optional[trimesh.Trimesh]:
    """
    Створює меш для води (западини для булевого віднімання)
    
    Args:
        gdf_water: GeoDataFrame з водними об'єктами
        depth: Глибина води (міліметри)
    
    Returns:
        Trimesh об'єкт води або None
    """
    if gdf_water.empty:
        return None
    
    water_meshes = []
    # ВАЖЛИВО:
    # - depth_mm у UI означає ММ НА МОДЕЛІ (після масштабування),
    # - але геометрію ми будуємо в метрах (UTM), і потім масштабуємо до мм.
    # Тому коректний шлях: main.py обчислює depth_meters і передає сюди.
    if depth is not None:
        depth_mm = float(depth)
    if depth_meters is None:
        depth_meters = depth_mm / 1000.0  # fallback (старий режим)

    # Кліп по межах рельєфу (щоб вода "не з'являлась де не треба")
    clip_box = None
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip_box = box(min_x, min_y, max_x, max_y)
        except Exception:
            clip_box = None
    
    for idx, row in gdf_water.iterrows():
        try:
            geom = row.geometry
            
            if not geom:
                continue

            try:
                if not geom.is_valid:
                    geom = geom.buffer(0)
            except Exception:
                continue

            # Кліпимо до bbox (особливо важливо для великих water polygons, які перетинають bbox)
            if clip_box is not None:
                try:
                    geom = geom.intersection(clip_box)
                except Exception:
                    continue
                if geom.is_empty:
                    continue

            # Фільтр по площі (прибирає випадкові артефакти/дуже дрібні плями)
            try:
                if hasattr(geom, "area") and geom.area < 25.0:  # < 25 м²
                    continue
            except Exception:
                pass
            
            # Створюємо западину для води
            # Трохи спрощуємо, щоб прибрати "розпливи" від мікросегментів
            try:
                geom = geom.simplify(0.5, preserve_topology=True)
            except Exception:
                pass

            if isinstance(geom, Polygon):
                mesh = create_water_depression(geom, float(depth_meters), terrain_provider=terrain_provider)
                if mesh:
                    water_meshes.append(mesh)
            elif hasattr(geom, 'geoms'):
                for poly in geom.geoms:
                    if isinstance(poly, Polygon):
                        if hasattr(poly, "area") and poly.area < 25.0:
                            continue
                        try:
                            poly = poly.simplify(0.5, preserve_topology=True)
                        except Exception:
                            pass
                        mesh = create_water_depression(poly, float(depth_meters), terrain_provider=terrain_provider)
                        if mesh:
                            water_meshes.append(mesh)
        except Exception as e:
            print(f"Помилка обробки води {idx}: {e}")
            continue
    
    if not water_meshes:
        return None
    
    # Об'єднуємо всі водні об'єкти
    combined_water = trimesh.util.concatenate(water_meshes)
    return combined_water


def process_water_surface(
    gdf_water: gpd.GeoDataFrame,
    thickness_m: float,
    depth_meters: float,
    terrain_provider: Optional[TerrainProvider] = None,
    global_center: Optional[GlobalCenter] = None,
) -> Optional[trimesh.Trimesh]:
    """
    Creates a thin "water surface" mesh for preview / multi-color printing.
    Assumes the terrain was already depressed by depth_meters, so we place the surface at:
      surface_z = ground_z + depth_meters
    """
    if gdf_water is None or gdf_water.empty:
        return None
    if thickness_m <= 0:
        return None

    # ВАЖЛИВО: Перетворюємо gdf_water в локальні координати, якщо використовується глобальний центр
    # gdf_water приходить з pbf_loader в UTM координатах, але terrain_provider працює з локальними координатами
    if global_center is not None:
        try:
            print(f"[DEBUG] Перетворюємо gdf_water з UTM в локальні координати (глобальний центр)")
            # Створюємо функцію трансформації для Shapely
            def to_local_transform(x, y, z=None):
                """Трансформер: UTM -> локальні координати"""
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)
            
            # Перетворюємо всі геометрії в локальні координати
            gdf_water_local = gdf_water.copy()
            gdf_water_local['geometry'] = gdf_water_local['geometry'].apply(
                lambda geom: transform(to_local_transform, geom) if geom is not None and not geom.is_empty else geom
            )
            gdf_water = gdf_water_local
            print(f"[DEBUG] Перетворено {len(gdf_water)} геометрій води в локальні координати")
        except Exception as e:
            print(f"[WARN] Не вдалося перетворити gdf_water в локальні координати: {e}")
            import traceback
            traceback.print_exc()
    
    meshes = []
    clip_box = None
    # ВАЖЛИВО: terrain_provider.get_bounds() вже повертає локальні координати
    # (X та Y вже центровані в terrain_generator), тому НЕ потрібно перетворювати через global_center
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip_box = box(min_x, min_y, max_x, max_y)
            print(f"[DEBUG] Water clip_box (локальні): x=[{min_x:.2f}, {max_x:.2f}], y=[{min_y:.2f}, {max_y:.2f}]")
        except Exception as e:
            print(f"[WARN] Не вдалося створити clip_box для води: {e}")
            clip_box = None

    processed_count = 0
    skipped_count = 0
    for idx, row in gdf_water.iterrows():
        geom = row.geometry
        if geom is None:
            print(f"[DEBUG] Water geometry {idx} is None, пропускаємо")
            skipped_count += 1
            continue
        
        # Діагностика геометрії
        try:
            geom_bounds = geom.bounds if hasattr(geom, 'bounds') else None
            print(f"[DEBUG] Water geometry {idx} bounds: {geom_bounds}, valid={geom.is_valid if hasattr(geom, 'is_valid') else 'unknown'}")
        except:
            pass
        
        try:
            if not geom.is_valid:
                print(f"[DEBUG] Water geometry {idx} невалідна, виправляємо через buffer(0)")
                geom = geom.buffer(0)
                if geom.is_empty:
                    print(f"[WARN] Water geometry {idx} стала порожньою після buffer(0)")
                    skipped_count += 1
                    continue
        except Exception as e:
            print(f"[WARN] Не вдалося виправити геометрію води {idx}: {e}")
            skipped_count += 1
            continue
        
        if clip_box is not None:
            try:
                # Перевіряємо чи геометрія перетинається з clip_box перед intersection
                if not geom.intersects(clip_box):
                    print(f"[DEBUG] Water geometry {idx} не перетинається з clip_box, пропускаємо")
                    skipped_count += 1
                    continue
                
                geom = geom.intersection(clip_box)
                if geom.is_empty:
                    print(f"[DEBUG] Water geometry {idx} стала порожньою після intersection з clip_box")
                    skipped_count += 1
                    continue
            except Exception as e:
                print(f"[WARN] Не вдалося обрізати геометрію води {idx}: {e}")
                import traceback
                traceback.print_exc()
                skipped_count += 1
                continue

        try:
            geom = geom.simplify(0.5, preserve_topology=True)
        except Exception:
            pass

        polys = [geom] if isinstance(geom, Polygon) else list(getattr(geom, "geoms", []))
        print(f"[DEBUG] Water geometry {idx} розбито на {len(polys)} полігонів")
        
        for poly_idx, poly in enumerate(polys):
            if not isinstance(poly, Polygon) or poly.is_empty:
                print(f"[DEBUG] Water poly {idx}_{poly_idx} не Polygon або порожній, пропускаємо")
                skipped_count += 1
                continue
            
            # Виправляємо полігон перед екструзією
            try:
                if not poly.is_valid:
                    print(f"[DEBUG] Water poly {idx}_{poly_idx} невалідний, виправляємо")
                    poly = poly.buffer(0)
                    if poly.is_empty:
                        print(f"[WARN] Water poly {idx}_{poly_idx} стала порожньою після buffer(0)")
                        skipped_count += 1
                        continue
                
                # Перевіряємо чи полігон має достатньо точок
                if hasattr(poly, 'exterior') and len(poly.exterior.coords) < 3:
                    print(f"[WARN] Water poly {idx}_{poly_idx} має менше 3 точок, пропускаємо")
                    skipped_count += 1
                    continue
            except Exception as e:
                print(f"[WARN] Помилка виправлення water poly {idx}_{poly_idx}: {e}")
                skipped_count += 1
                continue
            
            try:
                mesh = trimesh.creation.extrude_polygon(poly, height=float(thickness_m))
                if mesh is None or len(mesh.vertices) == 0:
                    print(f"[WARN] extrude_polygon повернув порожній mesh для poly {idx}_{poly_idx}")
                    skipped_count += 1
                    continue
                
                # ВИПРАВЛЕННЯ: НЕ використовуємо subdivision для води
                # Subdivision створює нерегулярну сітку, що призводить до "розділених" точок
                # Замість цього використовуємо оригінальний mesh з extrude_polygon
                # Якщо потрібна більша деталізація - краще збільшити кількість точок в полігоні
                # mesh = mesh.subdivide()  # ВИМКНЕНО - створює нерегулярну сітку
                print(f"[DEBUG] Water mesh: {len(mesh.vertices)} вершин (без subdivision для регулярної сітки)")
            except Exception as e:
                print(f"[WARN] Помилка extrude_polygon для poly {idx}_{poly_idx}: {e}")
                import traceback
                traceback.print_exc()
                skipped_count += 1
                continue

            if terrain_provider is not None and len(mesh.vertices) > 0:
                v = mesh.vertices.copy()
                old_z = v[:, 2].copy()  # old_z від 0 до thickness_m (від extrude_polygon)
                
                # ВИПРАВЛЕННЯ: Використовуємо ТІЛЬКИ вершини mesh для семплінгу
                # Це забезпечує регулярну сітку без "розділених" точок
                # Всі точки семплінгу відповідають вершинам mesh, що гарантує узгодженість
                points_array = v[:, :2]  # Використовуємо тільки вершини mesh (регулярна сітка)
                print(f"[DEBUG] Water polygon {idx}_{poly_idx}: використовуємо {len(points_array)} вершин mesh для семплінгу (регулярна сітка)")
                
                # ВАЖЛИВО: для правильного розміщення води потрібно використовувати ОРИГІНАЛЬНІ висоти рельєфу
                # (до вирізання depression), а не дно depression
                # Якщо є original_heights_provider - використовуємо його, інакше - звичайний
                if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider is not None:
                    # РОЗУМНА ЛОГІКА ДЛЯ ВОДИ:
                    # 1. Отримуємо оригінальні висоти рельєфу (до depression)
                    # 2. Отримуємо висоти рельєфу з depression (після вирізання)
                    # 3. Розраховуємо оптимальний рівень води, щоб вона виступала на землю лише на 0.1 мм (після масштабування)
                    # 4. Вода має бути трохи вище дна depression, але не накладатися на землю
                    
                    # Обчислюємо висоти для КОЖНОЇ точки окремо (як з рельєфом)
                    original_ground = terrain_provider.original_heights_provider.get_heights_for_points(points_array)
                    depressed_ground = terrain_provider.get_heights_for_points(points_array)
                    
                    # Аналізуємо різницю між оригінальним рельєфом та depression
                    depression_depth = original_ground - depressed_ground
                    min_depression = float(np.min(depression_depth))
                    max_depression = float(np.max(depression_depth))
                    avg_depression = float(np.mean(depression_depth))
                    
                    # Розраховуємо оптимальний рівень води
                    # Вода має виступати на землю лише на 0.1 мм після масштабування
                    # Для моделі 100мм це означає ~0.0001м в реальних одиницях (дуже мала величина)
                    # Але для кращого вигляду зробимо воду трохи вище дна depression
                    
                    # ВИПРАВЛЕНА ЛОГІКА: Вода має бути на рівні МІНІМАЛЬНОГО оригінального рельєфу (нижня точка русла)
                    # або трохи нижче, щоб не виступати над землею
                    min_original_ground = float(np.min(original_ground))
                    max_original_ground = float(np.max(original_ground))
                    
                    # Вода має бути на рівні найнижчої точки оригінального рельєфу (русло річки)
                    # Мінус невеликий offset для безпеки (щоб не виступала над берегами)
                    water_protrusion_m = 0.01  # Мінімальний виступ: 0.01м (1см) - майже на рівні землі
                    
                    # Використовуємо мінімальну висоту оригінального рельєфу як базовий рівень
                    # Це забезпечить, що вода буде на рівні русла, а не вище берегів
                    base_water_level = min_original_ground - 0.02  # 2см нижче найнижчої точки (безпека)
                    
                    # Розраховуємо рівень поверхні води для кожної точки
                    # Вода має бути на рівні дна depression + мінімальний protrusion
                    # Але не вище оригінального рельєфу в цій точці
                    surface_levels = np.minimum(
                        depressed_ground + water_protrusion_m,  # Дно depression + protrusion
                        original_ground - 0.02  # Але не вище оригінального рельєфу мінус 2см
                    )
                    
                    # Додаткова перевірка: вода не повинна бути вище мінімального рівня
                    surface_levels = np.maximum(surface_levels, base_water_level)
                    
                    # ФІНАЛЬНА ПЕРЕВІРКА: Переконаємося, що вода виступає на землю лише на потрібну величину
                    # Для моделі 100мм, 0.1 мм = 0.0001м в реальних одиницях (дуже мала величина)
                    # Але для кращого вигляду та друку, зробимо воду трохи вище дна depression
                    # Розраховуємо різницю між поверхнею води та дном depression
                    water_above_depression = surface_levels - depressed_ground
                    min_protrusion = float(np.min(water_above_depression))
                    max_protrusion = float(np.max(water_above_depression))
                    
                    # Якщо protrusion занадто великий - зменшуємо
                    if max_protrusion > 0.1:  # Більше 0.1м - занадто багато
                        water_protrusion_m = 0.01  # Зменшуємо до 0.01м (мінімальний виступ)
                        surface_levels = np.minimum(
                            depressed_ground + water_protrusion_m,  # Дно depression + protrusion
                            original_ground - 0.02  # Але не вище оригінального рельєфу мінус 2см
                        )
                        surface_levels = np.maximum(surface_levels, base_water_level)
                        print(f"[DEBUG] Water protrusion занадто великий ({max_protrusion:.3f}м), зменшено до {water_protrusion_m:.3f}м")
                    
                    print(f"[DEBUG] Water analysis: depression=[{min_depression:.3f}, {max_depression:.3f}], avg={avg_depression:.3f}м")
                    print(f"[DEBUG] Water surface levels: range=[{np.min(surface_levels):.3f}, {np.max(surface_levels):.3f}], median={np.median(surface_levels):.3f}")
                    print(f"[DEBUG] Water protrusion над дном depression: min={min_protrusion:.3f}м, max={max_protrusion:.3f}м, target={water_protrusion_m:.3f}м")
                else:
                    # Fallback: використовуємо дно depression + глибина
                    ground = terrain_provider.get_heights_for_points(points_array if len(points_array) > 0 else v[:, :2])
                    # Дно depression + глибина = рівень поверхні води
                    surface_levels = ground + float(depth_meters)
                    print(f"[WARN] Використовується fallback для water surface: ground range=[{np.min(ground):.3f}, {np.max(ground):.3f}], surface range=[{np.min(surface_levels):.3f}, {np.max(surface_levels):.3f}]")
                
                # КРИТИЧНЕ ВИПРАВЛЕННЯ: old_z від extrude_polygon - це товщина води (0 до thickness_m)
                # Для верхньої поверхні води (old_z == thickness_m) використовуємо surface_levels
                # Для нижньої поверхні води (old_z == 0) використовуємо surface_levels - thickness_m
                # Це забезпечить правильну товщину води без "підняття" над землею
                if len(surface_levels) == len(v):
                    # Визначаємо які вершини на верхній поверхні (old_z == thickness_m)
                    # та які на нижній (old_z == 0)
                    thickness = float(thickness_m)
                    is_top_surface = (old_z >= thickness * 0.9)  # Верхня поверхня (90%+ від товщини)
                    is_bottom_surface = (old_z <= thickness * 0.1)  # Нижня поверхня (10% від товщини)
                    
                    # Для верхньої поверхні: використовуємо surface_levels (рівень води)
                    # Для нижньої поверхні: використовуємо surface_levels - thickness (дно води)
                    # Для проміжних вершин: інтерполюємо
                    v[:, 2] = np.where(
                        is_top_surface,
                        surface_levels,  # Верхня поверхня = рівень води
                        np.where(
                            is_bottom_surface,
                            surface_levels - thickness,  # Нижня поверхня = дно води
                            surface_levels - (thickness - old_z)  # Проміжні: інтерполяція
                        )
                    )
                    print(f"[DEBUG] Water mesh: використано {len(surface_levels)} значень для вершин (верх: {np.sum(is_top_surface)}, низ: {np.sum(is_bottom_surface)})")
                elif len(surface_levels) > 0:
                    # Якщо кількість не співпадає (не повинно бути, але на всяк випадок)
                    # Використовуємо медіанне значення для всіх вершин
                    median_level = np.median(surface_levels)
                    v[:, 2] = np.full(len(v), median_level) + old_z
                    print(f"[WARN] Water mesh: кількість не співпадає ({len(surface_levels)} vs {len(v)}), використано медіанне значення")
                else:
                    # Fallback: використовуємо оригінальну логіку
                    v[:, 2] = old_z
                    print(f"[WARN] Water mesh: surface_levels порожній, використано оригінальну висоту")
                
                # Діагностика: перевіряємо, чи вода не накладається на рельєф
                min_water_z = float(np.min(v[:, 2]))
                max_water_z = float(np.max(v[:, 2]))
                
                # Отримуємо висоти рельєфу (з depression) для порівняння
                ground_depressed = terrain_provider.get_heights_for_points(v[:, :2])
                min_ground_depressed = float(np.min(ground_depressed))
                max_ground_depressed = float(np.max(ground_depressed))
                
                # Отримуємо оригінальні висоти для порівняння
                if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider is not None:
                    ground_original = terrain_provider.original_heights_provider.get_heights_for_points(v[:, :2])
                    min_ground_original = float(np.min(ground_original))
                    max_ground_original = float(np.max(ground_original))
                    
                    # Вода має бути на рівні оригінального рельєфу (або трохи нижче для безпеки)
                    # Але не нижче дна depression
                    if min_water_z < min_ground_depressed - 0.01:
                        print(f"[WARN] Water нижче дна depression: water_z={min_water_z:.3f}, depressed_ground={min_ground_depressed:.3f}")
                    elif max_water_z > max_ground_original + 0.01:  # Вода не повинна бути вище оригінального рельєфу
                        print(f"[WARN] Water вище оригінального рельєфу: water_z={max_water_z:.3f}, original_ground={max_ground_original:.3f}")
                    else:
                        print(f"[INFO] Water правильно розміщена: z=[{min_water_z:.3f}, {max_water_z:.3f}], original=[{min_ground_original:.3f}, {max_ground_original:.3f}], depressed=[{min_ground_depressed:.3f}, {max_ground_depressed:.3f}]")
                else:
                    # Fallback: порівнюємо з depressed ground
                    if min_water_z < min_ground_depressed - 0.01:
                        print(f"[WARN] Water нижче дна depression: water_z={min_water_z:.3f}, ground={min_ground_depressed:.3f}")
                    elif max_water_z > max_ground_depressed + depth_meters * 0.95:
                        print(f"[WARN] Water може накладатися: water_z={max_water_z:.3f}, ground={max_ground_depressed:.3f}, depth={depth_meters:.3f}")
                    else:
                        print(f"[INFO] Water розміщена (fallback): z=[{min_water_z:.3f}, {max_water_z:.3f}], ground=[{min_ground_depressed:.3f}, {max_ground_depressed:.3f}]")
                
                mesh.vertices = v
            else:
                # No terrain: just keep near Z=0
                mesh.apply_translation([0, 0, 0.0])

            if len(mesh.faces) > 0 and len(mesh.vertices) > 0:
                meshes.append(mesh)
                processed_count += 1
                print(f"[DEBUG] Water polygon оброблено: {len(mesh.vertices)} вершин, {len(mesh.faces)} граней")
            else:
                print(f"[WARN] Water polygon пропущено: vertices={len(mesh.vertices)}, faces={len(mesh.faces)}")
                skipped_count += 1

    print(f"[INFO] process_water_surface: оброблено {processed_count} полігонів, пропущено {skipped_count}")
    if not meshes:
        print(f"[WARN] process_water_surface: не створено жодного water mesh!")
        return None
    
    print(f"[INFO] process_water_surface: створено {len(meshes)} water meshes")
    try:
        combined = trimesh.util.concatenate(meshes)
        if combined is not None and len(combined.vertices) > 0 and len(combined.faces) > 0:
            print(f"[INFO] Water mesh об'єднано: {len(combined.vertices)} вершин, {len(combined.faces)} граней")
            
            # ВАЖЛИВО: Застосовуємо синій колір до water mesh ПЕРЕД поверненням
            # Це гарантує, що колір буде присутній навіть якщо export_3mf не застосує його
            try:
                # Синій колір для води: RGB(0, 100, 255) = [0, 100, 255, 255]
                water_color = np.array([0, 100, 255, 255], dtype=np.uint8)
                
                # Застосовуємо face colors (найкраща підтримка в 3MF)
                if len(combined.faces) > 0:
                    face_colors = np.tile(water_color, (len(combined.faces), 1))
                    combined.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)
                    print(f"[INFO] Застосовано синій колір до water mesh: {len(combined.faces)} граней")
                
                # Також додаємо vertex colors для fallback
                if len(combined.vertices) > 0:
                    vertex_colors = np.tile(water_color[:3], (len(combined.vertices), 1))
                    # Якщо face colors не працюють, vertex colors будуть використані
                    if not hasattr(combined.visual, 'face_colors') or combined.visual.face_colors is None:
                        combined.visual = trimesh.visual.ColorVisuals(vertex_colors=vertex_colors)
                        print(f"[INFO] Застосовано vertex colors до water mesh: {len(combined.vertices)} вершин")
            except Exception as e:
                print(f"[WARN] Не вдалося застосувати колір до water mesh: {e}")
                import traceback
                traceback.print_exc()
            
            return combined
        else:
            print(f"[WARN] Об'єднаний water mesh порожній, повертаємо перший")
            result = meshes[0] if meshes else None
            # Застосовуємо колір до першого mesh теж
            if result is not None and len(result.faces) > 0:
                try:
                    water_color = np.array([0, 100, 255, 255], dtype=np.uint8)
                    face_colors = np.tile(water_color, (len(result.faces), 1))
                    result.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)
                except Exception:
                    pass
            return result
    except Exception as e:
        print(f"[WARN] Помилка об'єднання water meshes: {e}, повертаємо перший")
        result = meshes[0] if meshes else None
        # Застосовуємо колір до першого mesh теж
        if result is not None and len(result.faces) > 0:
            try:
                water_color = np.array([0, 100, 255, 255], dtype=np.uint8)
                face_colors = np.tile(water_color, (len(result.faces), 1))
                result.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)
            except Exception:
                pass
        return result


def create_water_depression(
    polygon: Polygon,
    depth: float,
    terrain_provider: Optional[TerrainProvider] = None
) -> Optional[trimesh.Trimesh]:
    """
    Створює западину для води (для булевого віднімання з бази)
    
    Args:
        polygon: Полігон води
        depth: Глибина западини (метри)
    
    Returns:
        Trimesh об'єкт западини
    """
    try:
        # Надійний шлях з підтримкою holes: trimesh.creation.extrude_polygon сам тріангулює Shapely polygon (з отворами)
        # Створює volume висотою depth над z=0 → зсуваємо вниз, щоб top був на 0.
        mesh = trimesh.creation.extrude_polygon(polygon, height=float(depth))
        mesh.apply_translation([0, 0, -float(depth)])

        # Драпіруємо на рельєф: new_z = ground_z + old_z
        if terrain_provider is not None and len(mesh.vertices) > 0:
            verts = mesh.vertices.copy()
            old_z = verts[:, 2].copy()
            ground = terrain_provider.get_heights_for_points(verts[:, :2])
            verts[:, 2] = ground + old_z
            mesh.vertices = verts

        return mesh
    except Exception as e:
        print(f"Помилка створення западини води: {e}")
        return None

