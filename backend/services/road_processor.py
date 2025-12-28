"""
Сервіс для обробки доріг з буферизацією та об'єднанням
Покращена версія з фізичною шириною доріг та підтримкою мостів
Використовує trimesh.creation.extrude_polygon для надійної тріангуляції
"""
import osmnx as ox
import trimesh
import numpy as np
import warnings
from shapely.ops import unary_union, transform
from shapely.geometry import Polygon, MultiPolygon, box, LineString, Point
from typing import Optional, List, Tuple
import geopandas as gpd
from services.terrain_provider import TerrainProvider
from services.global_center import GlobalCenter
from services.mesh_quality import improve_mesh_for_3d_printing, validate_mesh_for_3d_printing

# Придушення deprecation warnings
warnings.filterwarnings('ignore', category=DeprecationWarning, module='pandas')


def create_bridge_supports(
    bridge_polygon: Polygon,
    bridge_height: float,
    terrain_provider: Optional[TerrainProvider],
    water_level: Optional[float],
    support_spacing: float = 20.0,  # Відстань між опорами (метри)
    support_width: float = 2.0,  # Ширина опори (метри)
    min_support_height: float = 1.0,  # Мінімальна висота опори (метри)
) -> List[trimesh.Trimesh]:
    """
    Створює опори для моста, які йдуть від моста до землі/води.
    Це необхідно для стабільності при 3D друку.
    
    Args:
        bridge_polygon: Полігон моста
        bridge_height: Висота моста (Z координата)
        terrain_provider: TerrainProvider для отримання висот землі
        water_level: Рівень води під мостом (опціонально)
        support_spacing: Відстань між опорами (метри)
        support_width: Ширина опори (метри)
        min_support_height: Мінімальна висота опори (метри)
    
    Returns:
        Список Trimesh об'єктів опор
    """
    supports = []
    
    if bridge_polygon is None or terrain_provider is None:
        return supports
    
    try:
        # Отримуємо центральну лінію моста (для розміщення опор)
        # Використовуємо centroid та bounds для визначення напрямку моста
        bounds = bridge_polygon.bounds
        minx, miny, maxx, maxy = bounds
        center_x = (minx + maxx) / 2.0
        center_y = (miny + maxy) / 2.0
        
        # Визначаємо напрямок моста (довша сторона)
        width = maxx - minx
        height = maxy - miny
        
        # ПОКРАЩЕННЯ: Розміщуємо опори по краях моста (для стабільності) + центральні для довгих мостів
        support_positions = []
        
        if width > height:
            # Міст йде вздовж X
            # Опори по краях (лівий і правий) - для стабільності
            edge_y_positions = [miny + support_width, maxy - support_width]
            
            # Центральні опори вздовж X для довгих мостів
            num_center_supports = max(0, int((width - 40) / support_spacing))  # Якщо міст довший за 40м
            if num_center_supports > 0:
                center_x_positions = np.linspace(minx + 20, maxx - 20, num_center_supports)
                # Додаємо опори на обох краях для кожної центральної позиції
                for cx in center_x_positions:
                    for ey in edge_y_positions:
                        support_positions.append((cx, ey))
            
            # Додаємо опори на початку та кінці моста (по краях)
            for ey in edge_y_positions:
                support_positions.append((minx + support_width, ey))
                support_positions.append((maxx - support_width, ey))
            
            # Якщо міст короткий - додаємо опори вздовж центральної лінії
            if width <= 40:
                num_supports = max(2, int(width / support_spacing) + 1)
                support_x_positions = np.linspace(minx + support_width, maxx - support_width, num_supports)
                for sx in support_x_positions:
                    support_positions.append((sx, center_y))
        else:
            # Міст йде вздовж Y
            # Опори по краях (верхній і нижній) - для стабільності
            edge_x_positions = [minx + support_width, maxx - support_width]
            
            # Центральні опори вздовж Y для довгих мостів
            num_center_supports = max(0, int((height - 40) / support_spacing))
            if num_center_supports > 0:
                center_y_positions = np.linspace(miny + 20, maxy - 20, num_center_supports)
                for cy in center_y_positions:
                    for ex in edge_x_positions:
                        support_positions.append((ex, cy))
            
            # Додаємо опори на початку та кінці моста (по краях)
            for ex in edge_x_positions:
                support_positions.append((ex, miny + support_width))
                support_positions.append((ex, maxy - support_width))
            
            # Якщо міст короткий - додаємо опори вздовж центральної лінії
            if height <= 40:
                num_supports = max(2, int(height / support_spacing) + 1)
                support_y_positions = np.linspace(miny + support_width, maxy - support_width, num_supports)
                for sy in support_y_positions:
                    support_positions.append((center_x, sy))
        
        # Видаляємо дублікати (якщо є)
        support_positions = list(set(support_positions))
        
        print(f"  [BRIDGE SUPPORTS] Створено {len(support_positions)} позицій опор (по краях + центральні)")
        
        # Створюємо опори
        for i, (x, y) in enumerate(support_positions):
            try:
                # Перевіряємо, чи точка всередині полігону моста
                pt = Point(x, y)
                if not bridge_polygon.contains(pt) and not bridge_polygon.touches(pt):
                    continue
                
                # ПОКРАЩЕННЯ: Семплінг висоти для площі опори (кілька точок замість однієї)
                # Це забезпечує більш точну висоту для великих опор (2м x 2м)
                support_half = support_width / 2.0
                sample_points = np.array([
                    [x - support_half, y - support_half],  # Лівий нижній кут
                    [x + support_half, y - support_half],  # Правий нижній кут
                    [x - support_half, y + support_half],  # Лівий верхній кут
                    [x + support_half, y + support_half],  # Правий верхній кут
                    [x, y]  # Центр
                ])
                
                # Отримуємо висоти для всіх точок семплінгу
                ground_zs = terrain_provider.get_heights_for_points(sample_points)
                ground_z = float(np.mean(ground_zs))  # Середнє значення для стабільності
                min_ground_z_sample = float(np.min(ground_zs))  # Мінімальне для перевірки води
                
                # Визначаємо висоту опори
                # Якщо є вода - опора йде до рівня води, інакше до землі
                # Використовуємо min_ground_z_sample для перевірки чи опора в воді
                if water_level is not None and min_ground_z_sample < water_level:
                    # Опора в воді - йде до рівня води
                    support_base_z = water_level
                else:
                    # Опора на землі - використовуємо середнє значення
                    support_base_z = ground_z
                
                support_height = bridge_height - support_base_z
                
                # Перевіряємо мінімальну висоту
                if support_height < min_support_height:
                    # Якщо опора занадто низька, все одно створюємо її (для стабільності)
                    # Але з мінімальною висотою
                    support_height = max(min_support_height, 0.5)  # Мінімум 0.5м для видимості
                    print(f"  [BRIDGE SUPPORT] Опора {i}: висота збільшена до мінімуму {support_height:.2f}м")
                
                # Створюємо циліндричну опору
                # Використовуємо box замість cylinder для простішої геометрії (краще для 3D друку)
                support_mesh = trimesh.creation.box(
                    extents=[support_width, support_width, support_height],
                    transform=trimesh.transformations.translation_matrix([x, y, support_base_z + support_height / 2.0])
                )
                
                if support_mesh is not None and len(support_mesh.vertices) > 0:
                    # Застосовуємо сірий колір до опор (бетон/метал)
                    support_color = np.array([120, 120, 120, 255], dtype=np.uint8)  # Сірий колір
                    if len(support_mesh.faces) > 0:
                        face_colors = np.tile(support_color, (len(support_mesh.faces), 1))
                        support_mesh.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)
                    supports.append(support_mesh)
                    
            except Exception as e:
                print(f"  [WARN] Помилка створення опори {i}: {e}")
                continue
        
    except Exception as e:
        print(f"  [WARN] Помилка створення опор для моста: {e}")
        import traceback
        traceback.print_exc()
    
    return supports


def detect_bridges(
    G_roads,
    water_geometries: Optional[List] = None,
    bridge_tag: str = 'bridge',
) -> List[Tuple[object, float]]:
    """
    Визначає мости: дороги, які перетинають воду або мають тег bridge=yes
    
    Args:
        G_roads: OSMnx граф доріг або GeoDataFrame
        water_geometries: Список геометрій водних об'єктів (Polygon/MultiPolygon)
        bridge_tag: Тег для визначення мостів в OSM
        
    Returns:
        Список кортежів (edge_geometry, bridge_height_offset) - геометрія дороги та зміщення висоти для моста
    """
    bridges = []
    
    if G_roads is None:
        return bridges
    
    # Підтримка 2 режимів
    gdf_edges = None
    if isinstance(G_roads, gpd.GeoDataFrame):
        gdf_edges = G_roads
    else:
        if not hasattr(G_roads, "edges") or len(G_roads.edges) == 0:
            return bridges
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)
    
    if gdf_edges is None or gdf_edges.empty:
        return bridges
    
    # Об'єднуємо всі водні об'єкти для перевірки перетину
    water_union = None
    if water_geometries:
        try:
            water_polys = []
            for wg in water_geometries:
                if wg is not None:
                    if isinstance(wg, Polygon):
                        water_polys.append(wg)
                    elif hasattr(wg, 'geoms'):  # MultiPolygon
                        water_polys.extend(wg.geoms)
            if water_polys:
                water_union = unary_union(water_polys)
        except Exception as e:
            print(f"[WARN] Помилка об'єднання водних об'єктів для визначення мостів: {e}")
    
    # Перевіряємо кожну дорогу
    for idx, row in gdf_edges.iterrows():
        try:
            geom = row.geometry
            if geom is None:
                continue
            
            is_bridge = False
            bridge_height = 2.0  # Базова висота моста (метри)
            
            # 1. Перевірка тегу bridge в OSM
            if bridge_tag in row and row[bridge_tag] in ['yes', 'true', '1', True]:
                is_bridge = True
                # Визначаємо висоту моста за типом
                bridge_type = row.get('bridge:type', '')
                if 'suspension' in str(bridge_type).lower():
                    bridge_height = 5.0
                elif 'arch' in str(bridge_type).lower():
                    bridge_height = 4.0
                elif 'beam' in str(bridge_type).lower():
                    bridge_height = 3.0
                else:
                    bridge_height = 2.5
            
            # 2. Перевірка перетину з водою
            if not is_bridge and water_union is not None:
                try:
                    # Перевіряємо чи дорога перетинає воду
                    if geom.intersects(water_union):
                        # Перевіряємо чи це дійсно перетин (не просто дотик)
                        intersection = geom.intersection(water_union)
                        if intersection and hasattr(intersection, 'length') and intersection.length > 1.0:
                            is_bridge = True
                            # Висота моста залежить від ширини води
                            if hasattr(water_union, 'area'):
                                # Знаходимо найближчий водний об'єкт для оцінки ширини
                                min_dist = float('inf')
                                for wg in water_geometries:
                                    if wg is not None:
                                        try:
                                            dist = geom.distance(wg)
                                            if dist < min_dist:
                                                min_dist = dist
                                                if hasattr(wg, 'bounds'):
                                                    # Оцінюємо розмір водного об'єкта
                                                    bounds = wg.bounds
                                                    width = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
                                                    if width > 50:  # Велика річка
                                                        bridge_height = 4.0
                                                    elif width > 20:  # Середня річка
                                                        bridge_height = 3.0
                                                    else:  # Мала річка
                                                        bridge_height = 2.0
                                        except:
                                            pass
                except Exception as e:
                    print(f"[WARN] Помилка перевірки перетину дороги з водою: {e}")
            
            if is_bridge:
                bridges.append((geom, bridge_height))
                
        except Exception as e:
            print(f"[WARN] Помилка обробки дороги для визначення моста: {e}")
            continue
    
    print(f"[INFO] Визначено {len(bridges)} мостів")
    return bridges


def build_road_polygons(
    G_roads,
    width_multiplier: float = 1.0,
) -> Optional[object]:
    """
    Builds merged road polygons (2D) from a roads graph/edges gdf.
    This is useful for terrain-first operations (flattening terrain under roads) and
    also allows reusing the merged geometry for mesh generation.
    """
    if G_roads is None:
        return None

    # Support graph or edges GeoDataFrame
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
        'motorway': 12,
        'motorway_link': 10,
        'trunk': 10,
        'trunk_link': 8,
        'primary': 8,
        'primary_link': 6,
        'secondary': 6,
        'secondary_link': 5,
        'tertiary': 5,
        'tertiary_link': 4,
        'residential': 4,
        'living_street': 3,
        'service': 3,
        'unclassified': 3,
        'footway': 2,
        'path': 1.5,
        'cycleway': 2,
        'pedestrian': 2,
        'steps': 1
    }

    def get_width(row):
        highway = row.get('highway')
        if isinstance(highway, list):
            highway = highway[0] if highway else None
        elif not highway:
            return 3.0
        width = width_map.get(highway, 3.0)
        return width * width_multiplier

    if 'highway' in gdf_edges.columns:
        gdf_edges = gdf_edges.copy()
        gdf_edges['width'] = gdf_edges.apply(get_width, axis=1)
    else:
        gdf_edges = gdf_edges.copy()
        gdf_edges['width'] = 3.0 * width_multiplier

    road_polygons = []
    for _, row in gdf_edges.iterrows():
        try:
            geom = row.geometry
            if geom is None:
                continue
            if not geom.is_valid:
                geom = geom.buffer(0)
            buffer = geom.buffer(row.width / 2, cap_style=2, join_style=2)
            if buffer and hasattr(buffer, 'area') and buffer.area > 0:
                road_polygons.append(buffer)
        except Exception:
            continue

    if not road_polygons:
        return None

    try:
        return unary_union(road_polygons)
    except Exception:
        return road_polygons[0]


def process_roads(
    G_roads,
    width_multiplier: float = 1.0,
    terrain_provider: Optional[TerrainProvider] = None,
    road_height: float = 1.0,  # Висота дороги у "світових" одиницях (звичайно метри в UTM-проєкції)
    road_embed: float = 0.0,   # Наскільки "втиснути" в рельєф (м), щоб гарантовано не висіла
    merged_roads: Optional[object] = None,  # Optional precomputed merged road polygons
    water_geometries: Optional[List] = None,  # Геометрії водних об'єктів для визначення мостів
    bridge_height_multiplier: float = 1.0,  # Множник для висоти мостів
    global_center: Optional[GlobalCenter] = None,  # Глобальний центр для перетворення координат
) -> Optional[trimesh.Trimesh]:
    """
    Обробляє дорожню мережу, створюючи 3D меші з правильною шириною
    
    Args:
        G_roads: OSMnx граф доріг
        width_multiplier: Множник для ширини доріг
    
    Returns:
        Trimesh об'єкт з об'єднаними дорогами
    """
    if G_roads is None:
        return None

    # Підтримка 2 режимів:
    # - OSMnx graph (як було)
    # - GeoDataFrame ребер (pyrosm network edges)
    gdf_edges = None
    if isinstance(G_roads, gpd.GeoDataFrame):
        gdf_edges = G_roads
    else:
        if not hasattr(G_roads, "edges") or len(G_roads.edges) == 0:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)
    
    # Build or reuse merged road geometry
    if merged_roads is None:
        print("Створення буферів доріг...")
        merged_roads = build_road_polygons(G_roads, width_multiplier=width_multiplier)
    if merged_roads is None:
        return None
    
    # ВАЖЛИВО: Перетворюємо merged_roads з UTM в локальні координати, якщо використовується глобальний центр
    # merged_roads приходить з pbf_loader в UTM координатах, але terrain_provider працює з локальними координатами
    if global_center is not None:
        try:
            print(f"[DEBUG] Перетворюємо merged_roads з UTM в локальні координати (глобальний центр)")
            # Створюємо функцію трансформації для Shapely
            def to_local_transform(x, y, z=None):
                """Трансформер: UTM -> локальні координати"""
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)
            
            # Перетворюємо геометрію доріг в локальні координати
            merged_roads = transform(to_local_transform, merged_roads)
            print(f"[DEBUG] Перетворено merged_roads в локальні координати")
        except Exception as e:
            print(f"[WARN] Не вдалося перетворити merged_roads в локальні координати: {e}")
            import traceback
            traceback.print_exc()
    
    # Якщо є рельєф — кліпимо дороги в межі рельєфу (буферизація може виходити за bbox і давати "провали")
    if terrain_provider is not None:
        try:
            min_x, max_x, min_y, max_y = terrain_provider.get_bounds()
            clip = box(min_x, min_y, max_x, max_y)
            merged_roads = merged_roads.intersection(clip)
            # ВИПРАВЛЕННЯ: Перевіряємо чи результат не порожній та валідний
            if merged_roads is None or merged_roads.is_empty:
                print("[WARN] Дороги стали порожніми після обрізання по рельєфу")
                return None
            # Виправляємо геометрію якщо потрібно
            if not merged_roads.is_valid:
                merged_roads = merged_roads.buffer(0)
                if merged_roads.is_empty:
                    print("[WARN] Дороги стали порожніми після виправлення")
                    return None
        except Exception as e:
            print(f"[WARN] Помилка обрізання доріг по рельєфу: {e}")
            pass

    # Конвертація в список полігонів для обробки
    if merged_roads is None or merged_roads.is_empty:
        print("[WARN] merged_roads порожній або None")
        return None
    
    if isinstance(merged_roads, Polygon):
        # Перевіряємо чи полігон має достатньо точок
        if hasattr(merged_roads, 'exterior') and len(merged_roads.exterior.coords) < 3:
            print(f"[WARN] Полігон доріг має менше 3 точок ({len(merged_roads.exterior.coords)}), пропускаємо")
            return None
        road_geoms = [merged_roads]
    elif isinstance(merged_roads, MultiPolygon):
        # Фільтруємо полігони з достатньою кількістю точок
        road_geoms = []
        for geom in merged_roads.geoms:
            if hasattr(geom, 'exterior') and len(geom.exterior.coords) >= 3:
                road_geoms.append(geom)
            else:
                print(f"[WARN] Полігон доріг має менше 3 точок, пропускаємо")
        if len(road_geoms) == 0:
            print("[WARN] Всі полігони доріг мають менше 3 точок")
            return None
    else:
        print(f"[WARN] Невідомий тип геометрії після об'єднання: {type(merged_roads)}")
        return None
    
    # ВАЖЛИВО: Перетворюємо water_geometries в локальні координати для визначення мостів
    water_geoms_local = None
    if water_geometries is not None and global_center is not None:
        try:
            print(f"[DEBUG] Перетворюємо water_geometries для мостів з UTM в локальні координати")
            def to_local_transform(x, y, z=None):
                """Трансформер: UTM -> локальні координати"""
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)
            
            water_geoms_local = []
            for geom in water_geometries:
                if geom is not None and not geom.is_empty:
                    local_geom = transform(to_local_transform, geom)
                    if local_geom is not None and not local_geom.is_empty:
                        water_geoms_local.append(local_geom)
            print(f"[DEBUG] Перетворено {len(water_geoms_local)} water geometries для мостів в локальні координати")
        except Exception as e:
            print(f"[WARN] Не вдалося перетворити water_geometries для мостів: {e}")
            water_geoms_local = water_geometries  # Використовуємо оригінальні як fallback
    else:
        water_geoms_local = water_geometries
    
    # Визначаємо мости перед обробкою (використовуємо локальні координати)
    bridges = detect_bridges(G_roads, water_geometries=water_geoms_local)
    bridge_geoms = {geom: height for geom, height in bridges} if bridges else {}
    
    print(f"Створення 3D мешу доріг з {len(road_geoms)} полігонів (мостів: {len(bridge_geoms)})...")
    road_meshes = []
    
    for poly in road_geoms:
        try:
            # Використовуємо trimesh.creation.extrude_polygon для надійної екструзії
            # Це автоматично обробляє дірки (holes) та правильно тріангулює
            try:
                # Перевіряємо чи це міст
                is_bridge = False
                bridge_height_offset = 0.0
                
                # Перевіряємо чи полігон перетинається з мостами
                for bridge_geom, bridge_h in bridge_geoms.items():
                    try:
                        if poly.intersects(bridge_geom):
                            is_bridge = True
                            bridge_height_offset = bridge_h * bridge_height_multiplier
                            break
                    except:
                        continue
                
                # Екструдуємо полігон на висоту road_height (для мостів додаємо зміщення)
                rh = max(float(road_height), 0.0001)
                if is_bridge:
                    # ВИПРАВЛЕННЯ: Для мостів використовуємо bridge_height_offset як основну висоту
                    # bridge_height_offset вже містить правильну висоту моста (2-5м залежно від типу)
                    # Додаємо базову висоту дороги для товщини
                    total_height = max(rh + bridge_height_offset, bridge_height_offset, 0.5)
                    print(f"  [BRIDGE] Висота моста: {total_height:.2f}м (базова дорога: {rh:.2f}м + висота моста: {bridge_height_offset:.2f}м)")
                else:
                    total_height = rh
                
                # embed не має бути > road_height, інакше вся дорога "піде під землю"
                re = float(road_embed) if road_embed is not None else 0.0
                re = max(0.0, min(re, rh * 0.8))
                
                # ВИПРАВЛЕННЯ: Виправляємо полігон перед екструзією для уникнення помилки ring_end_indices
                try:
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                        if poly.is_empty:
                            print(f"  [SKIP] Полігон дороги став порожнім після виправлення")
                            continue
                    
                    # Перевіряємо структуру полігону
                    if hasattr(poly, 'exterior') and len(poly.exterior.coords) < 3:
                        print(f"  [SKIP] Полігон дороги має менше 3 точок")
                        continue
                    
                    # Перевіряємо внутрішні кільця (holes)
                    if hasattr(poly, 'interiors'):
                        for i, interior in enumerate(poly.interiors):
                            if len(interior.coords) < 3:
                                print(f"  [WARN] Внутрішнє кільце {i} має менше 3 точок, видаляємо")
                                # Створюємо новий полігон без цього кільця
                                # ВАЖЛИВО: Polygon вже імпортовано на початку файлу, не імпортуємо знову
                                new_exterior = poly.exterior
                                new_interiors = [interior for j, interior in enumerate(poly.interiors) if j != i]
                                poly = Polygon(new_exterior, new_interiors)
                except Exception as e:
                    print(f"  [WARN] Помилка виправлення полігону дороги: {e}")
                    continue
                
                mesh = trimesh.creation.extrude_polygon(poly, height=total_height)
                
                # Проектуємо дорогу на рельєф, якщо TerrainProvider доступний
                if terrain_provider is not None:
                    # ВАЖЛИВО: не "вбиваємо" екструзію.
                    # extrude_polygon дає вершини з old_z у [0..total_height].
                    # Потрібно додати рельєф: new_z = ground_z + old_z
                    vertices = mesh.vertices.copy()
                    old_z = vertices[:, 2].copy()
                    ground_z_values = terrain_provider.get_heights_for_points(vertices[:, :2])
                    
                    if is_bridge:
                        # РОЗУМНА ЛОГІКА ДЛЯ МОСТІВ:
                        # 1. Знаходимо висоту води під мостом (якщо є terrain_provider з original_heights_provider)
                        # 2. Розміщуємо міст на достатній висоті над водою (мінімум 3-5 метрів)
                        # 3. Враховуємо оригінальний рельєф та висоту води
                        
                        # Знаходимо мінімальну та максимальну висоту рельєфу під мостом
                        min_ground_z = float(np.min(ground_z_values))
                        max_ground_z = float(np.max(ground_z_values))
                        
                        # ПОКРАЩЕНА ЛОГІКА ДЛЯ МОСТІВ:
                        # 1. Розраховуємо висоту води для кожної точки моста окремо
                        # 2. Використовуємо медіанне значення для стабільності
                        # 3. Адаптуємо висоту моста до нахилу
                        
                        water_level_under_bridge = None
                        if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider is not None:
                            # Отримуємо оригінальні висоти (до depression) для КОЖНОЇ точки моста
                            original_ground_z = terrain_provider.original_heights_provider.get_heights_for_points(vertices[:, :2])
                            
                            # Розраховуємо висоту води для кожної точки окремо
                            # Вода зазвичай на 0.15-0.2м нижче оригінального рельєфу (з water_processor)
                            water_levels_per_point = original_ground_z - 0.2  # Вода для кожної точки
                            
                            # Використовуємо медіанне значення для стабільності (менш чутливе до викидів)
                            # Але також враховуємо мінімальне значення для забезпечення мінімального зазору
                            median_water_level = float(np.median(water_levels_per_point))
                            min_water_level = float(np.min(water_levels_per_point))
                            max_water_level = float(np.max(water_levels_per_point))
                            
                            # Використовуємо медіанне значення як базове, але перевіряємо мінімальне
                            water_level_under_bridge = median_water_level
                            
                            # ВИПРАВЛЕННЯ: Мінімальний зазор над водою має враховувати bridge_height_offset
                            # bridge_height_offset - це висота моста над водою/рельєфом (2-5м)
                            # Використовуємо його як мінімальний clearance
                            min_clearance_above_water = max(3.0, bridge_height_offset)  # Мінімум 3м, або висота моста
                            
                            # Розраховуємо базову висоту моста для кожної точки окремо
                            # Верхня частина моста має бути на bridge_height_offset над водою
                            bridge_base_z_per_point = water_levels_per_point + min_clearance_above_water
                            
                            # Але також враховуємо рельєф (береги) - міст не може бути нижче рельєфу + bridge_height_offset
                            bridge_base_z_per_point = np.maximum(
                                bridge_base_z_per_point,
                                ground_z_values + bridge_height_offset  # Мінімум bridge_height_offset над рельєфом
                            )
                            
                            # Використовуємо медіанне значення для стабільності (але можна залишити per-point для нахилу)
                            # Для стабільності використовуємо медіанне, але можна використати per-point для нахилу
                            use_median = True  # Використовувати медіанне значення для всіх точок
                            
                            if use_median:
                                # Використовуємо медіанне значення для всіх точок (стабільніше для 3D друку)
                                bridge_base_z = float(np.median(bridge_base_z_per_point))
                                vertices[:, 2] = bridge_base_z + old_z
                            else:
                                # Використовуємо per-point значення (враховує нахил, але може бути нестабільним)
                                vertices[:, 2] = bridge_base_z_per_point + old_z
                            
                            print(f"  [BRIDGE] Розрахунок: water_level=[{min_water_level:.2f}, {median_water_level:.2f}, {max_water_level:.2f}]м (median), clearance={min_clearance_above_water:.2f}м, bridge_base={bridge_base_z:.2f}м (median)")
                            
                            # Зберігаємо water_level_under_bridge для опор
                            water_level_under_bridge = median_water_level
                            
                            # КРИТИЧНО ДЛЯ 3D ДРУКУ: Створюємо опори для моста
                            # Мости мають стояти на опорах, які йдуть до землі/води
                            # Це необхідно для стабільності при 3D друку
                            try:
                                bridge_supports = create_bridge_supports(
                                    poly,  # Полігон моста
                                    bridge_base_z,  # Висота моста
                                    terrain_provider,  # Для отримання висот землі
                                    water_level_under_bridge,  # Рівень води
                                    support_spacing=20.0,  # Відстань між опорами (метри)
                                    support_width=2.0,  # Ширина опори (метри)
                                    min_support_height=1.0,  # Мінімальна висота опори (метри)
                                )
                                
                                if bridge_supports is not None and len(bridge_supports) > 0:
                                    # Додаємо опори до списку мешів
                                    road_meshes.extend(bridge_supports)
                                    print(f"  [BRIDGE] Створено {len(bridge_supports)} опор для моста")
                            except Exception as e:
                                print(f"  [WARN] Не вдалося створити опори для моста: {e}")
                                import traceback
                                traceback.print_exc()
                        else:
                            # Fallback: використовуємо стару логіку
                        # Піднімаємо міст над найвищою точкою рельєфу під ним
                        vertices[:, 2] = min_ground_z + old_z + bridge_height_offset * 0.5
                            print(f"  [BRIDGE] Fallback: min_ground={min_ground_z:.2f}м, offset={bridge_height_offset:.2f}м")
                    else:
                        # ПОКРАЩЕНА ЛОГІКА ДЛЯ ЗВИЧАЙНИХ ДОРОГ:
                        # 1. Розраховуємо нахил рельєфу для адаптивного road_embed
                        # 2. Додаємо мінімальну висоту над рельєфом
                        # 3. Захищаємо від "під землею" на крутих схилах
                        
                        # Розраховуємо нахил рельєфу (різниця між мінімальною та максимальною висотою)
                        min_ground_z = float(np.min(ground_z_values))
                        max_ground_z = float(np.max(ground_z_values))
                        slope = max_ground_z - min_ground_z  # Різниця висот
                        
                        # Адаптивний road_embed: на крутих схилах зменшуємо embed
                        # Якщо нахил більший за road_embed * 2, зменшуємо embed
                        adaptive_embed = float(re)
                        if slope > float(re) * 2.0:
                            # На крутих схилах зменшуємо embed пропорційно
                            adaptive_embed = float(re) * (1.0 - min(0.5, (slope - float(re) * 2.0) / (slope + 1.0)))
                            print(f"  [ROAD] Адаптивний embed: slope={slope:.2f}м, original={re:.2f}м, adaptive={adaptive_embed:.2f}м")
                        
                        # Мінімальна висота над рельєфом (для захисту від "під землею")
                        min_height_above_ground = 0.1  # Мінімум 0.1м над рельєфом
                        
                        # Розміщуємо дорогу з адаптивним embed та мінімальною висотою
                        road_z = ground_z_values + old_z - adaptive_embed
                        min_road_z = ground_z_values + min_height_above_ground
                        
                        # Використовуємо максимум між road_z та min_road_z (захист від "під землею")
                        vertices[:, 2] = np.maximum(road_z, min_road_z)
                        
                        # Діагностика
                        final_min_z = float(np.min(vertices[:, 2]))
                        final_max_z = float(np.max(vertices[:, 2]))
                        ground_min_z = float(np.min(ground_z_values))
                        ground_max_z = float(np.max(ground_z_values))
                        
                        if final_min_z < ground_min_z - 0.05:  # Дорога нижче рельєфу більше ніж на 5см
                            print(f"  [WARN] Дорога може бути нижче рельєфу: road_z={final_min_z:.2f}м, ground={ground_min_z:.2f}м")
                        else:
                            print(f"  [ROAD] Дорога розміщена: road_z=[{final_min_z:.2f}, {final_max_z:.2f}]м, ground=[{ground_min_z:.2f}, {ground_max_z:.2f}]м, embed={adaptive_embed:.2f}м")
                    mesh.vertices = vertices
                else:
                    # Без рельєфу: "втиснемо" дороги трохи вниз, щоб не було щілин з плоскою базою
                    if float(re) > 0:
                        vertices = mesh.vertices.copy()
                        vertices[:, 2] = vertices[:, 2] - float(re)
                        mesh.vertices = vertices
                
                # Перевірка на валідність та покращення для 3D принтера
                if len(mesh.faces) > 0 and len(mesh.vertices) > 0:
                    # Виправлення mesh для 3D принтера
                    try:
                        # Виправляємо нормалі
                        mesh.fix_normals()
                        # Видаляємо дегенеровані грані
                        mesh.remove_duplicate_faces()
                        mesh.remove_unreferenced_vertices()
                        # Заповнюємо дірки
                        if not mesh.is_volume:
                            mesh.fill_holes()
                        # Об'єднуємо близькі вершини для чистоти
                        mesh.merge_vertices(merge_tex=True, merge_norm=True)
                    except Exception as fix_error:
                        print(f"  Попередження при виправленні мешу: {fix_error}")
                    
                    # Перевірка мінімальних розмірів для 3D принтера
                    try:
                        bounds = mesh.bounds
                        min_dim = min(bounds[1] - bounds[0])  # Мінімальний розмір
                        if min_dim < 0.001:  # Менше 1мм - занадто тонко
                            print(f"  [WARN] Дорога занадто тонка для друку: {min_dim*1000:.2f}мм")
                    except:
                        pass
                    
                    # Застосовуємо колір до доріг та мостів
                    if is_bridge:
                        # Темно-сірий колір для мостів
                        road_color = np.array([60, 60, 60, 255], dtype=np.uint8)  # Темно-сірий
                    else:
                        # Сіро-чорний колір для доріг (асфальт)
                        road_color = np.array([40, 40, 40, 255], dtype=np.uint8)  # Темно-сірий/чорний
                    
                    if len(mesh.faces) > 0:
                        face_colors = np.tile(road_color, (len(mesh.faces), 1))
                        mesh.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)
                    
                    road_meshes.append(mesh)
                    bridge_label = "[BRIDGE]" if is_bridge else "[ROAD]"
                    print(f"  [OK] {bridge_label} Створено меш: {len(mesh.vertices)} вершин, {len(mesh.faces)} граней, volume={mesh.is_volume}")
                else:
                    print(f"  ❌ Меш дороги невалідний: {len(mesh.faces)} граней, {len(mesh.vertices)} вершин")
                    
            except Exception as extrude_error:
                print(f"  Помилка екструзії полігону: {extrude_error}")
                # Fallback: спробуємо створити простий меш
                continue
                
        except Exception as e:
            print(f"Помилка обробки полігону дороги: {e}")
            continue
    
    if not road_meshes:
        print("Попередження: Не вдалося створити жодного мешу доріг")
        return None
    
    print(f"Створено {len(road_meshes)} мешів доріг")
    
    # Об'єднання всіх мешів доріг
    print("Об'єднання мешів доріг...")
    try:
        combined_roads = trimesh.util.concatenate(road_meshes)
        print(f"Дороги об'єднано: {len(combined_roads.vertices)} вершин, {len(combined_roads.faces)} граней")
        
        # Покращення mesh для 3D принтера
        print("Покращення якості mesh для 3D принтера...")
        combined_roads = improve_mesh_for_3d_printing(combined_roads, aggressive=True)
        
        # Перевірка якості
        is_valid, mesh_warnings = validate_mesh_for_3d_printing(combined_roads)
        if mesh_warnings:
            print(f"[INFO] Попередження щодо якості mesh доріг:")
            for w in mesh_warnings:
                print(f"  - {w}")
        
        return combined_roads
    except Exception as e:
        print(f"Помилка об'єднання доріг: {e}")
        # Повертаємо перший меш якщо не вдалося об'єднати
        if road_meshes:
            return road_meshes[0]
        return None

