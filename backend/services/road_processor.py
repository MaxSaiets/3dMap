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
    bridge_buffer_m: float = 12.0,  # buffer around bridge centerline to mark only the bridge area
) -> List[Tuple[object, float]]:
    """
    Визначає мости: дороги, які перетинають воду або мають тег bridge=yes
    
    Args:
        G_roads: OSMnx граф доріг або GeoDataFrame
        water_geometries: Список геометрій водних об'єктів (Polygon/MultiPolygon)
        bridge_tag: Тег для визначення мостів в OSM
        
    Returns:
        Список кортежів (bridge_area_geometry, bridge_height_offset) - геометрія ОБЛАСТІ моста та зміщення висоти
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
                # IMPORTANT: return an AREA geometry for bridge marking.
                # Using raw edge LineString causes almost all buffered road polygons to "intersect a bridge".
                try:
                    bridge_area = geom
                    # If bridge is detected by water intersection, constrain to the water-crossing portion.
                    if water_union is not None:
                        try:
                            inter = geom.intersection(water_union)
                            if inter is not None and not inter.is_empty:
                                bridge_area = inter
                        except Exception:
                            pass
                    # Buffer into an area (polygon) so later intersection is spatially tight.
                    try:
                        bridge_area = bridge_area.buffer(float(bridge_buffer_m), cap_style=2, join_style=2)
                    except Exception:
                        bridge_area = geom.buffer(float(bridge_buffer_m))
                    if bridge_area is not None and not bridge_area.is_empty:
                        bridges.append((bridge_area, bridge_height))
                except Exception:
                    # Fallback to raw geometry if buffering fails
                    bridges.append((geom, bridge_height))
                
        except Exception as e:
            print(f"[WARN] Помилка обробки дороги для визначення моста: {e}")
            continue
    
    print(f"[INFO] Визначено {len(bridges)} мостів")
    return bridges


def build_road_polygons(
    G_roads,
    width_multiplier: float = 1.0,
    min_width_m: Optional[float] = None,
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
        width = width * width_multiplier
        # Ensure minimum printable width (in world meters)
        try:
            if min_width_m is not None:
                width = max(float(width), float(min_width_m))
        except Exception:
            pass
        return width

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
    min_width_m: Optional[float] = None,  # Мінімальна ширина дороги (в метрах у world units)
    clip_polygon: Optional[object] = None,  # Zone polygon in LOCAL coords (for pre-clipping)
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
    
    # Helper: decide if geometry looks like UTM (huge coordinates) and convert to local if needed
    def _looks_like_utm(g) -> bool:
        try:
            b = g.bounds
            return max(abs(float(b[0])), abs(float(b[1])), abs(float(b[2])), abs(float(b[3]))) > 100000.0
        except Exception:
            return False

    def _to_local_geom(g):
        if g is None or global_center is None:
            return g
        try:
            if not _looks_like_utm(g):
                return g
            def to_local_transform(x, y, z=None):
                x_local, y_local = global_center.to_local(x, y)
                if z is not None:
                    return (x_local, y_local, z)
                return (x_local, y_local)
            return transform(to_local_transform, g)
        except Exception:
            return g

    # Build or reuse merged road geometry
    if merged_roads is None:
        print("Створення буферів доріг...")
        merged_roads = build_road_polygons(G_roads, width_multiplier=width_multiplier, min_width_m=min_width_m)
    if merged_roads is None:
        return None
    
    # Ensure merged_roads are in LOCAL coords if we have global_center
    merged_roads = _to_local_geom(merged_roads)
    
    # Pre-clip to zone polygon (LOCAL coords) to prevent roads outside the zone.
    if clip_polygon is not None:
        try:
            clip_poly_local = clip_polygon
            # If clip_polygon came in UTM, convert too
            clip_poly_local = _to_local_geom(clip_poly_local)
            merged_roads = merged_roads.intersection(clip_poly_local)
            if merged_roads is None or merged_roads.is_empty:
                return None
        except Exception:
            pass

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
    if water_geometries is not None:
        try:
            water_geoms_local = [_to_local_geom(g) for g in water_geometries if g is not None and not getattr(g, "is_empty", False)]
        except Exception:
            water_geoms_local = water_geometries

    # Ensure edges are in local coords for bridge detection (otherwise intersects never match)
    try:
        if global_center is not None and gdf_edges is not None and not gdf_edges.empty:
            # If edges look like UTM, convert to local
            sample_geom = gdf_edges.iloc[0].geometry if len(gdf_edges) else None
            if sample_geom is not None and _looks_like_utm(sample_geom):
                def to_local_transform(x, y, z=None):
                    x_local, y_local = global_center.to_local(x, y)
                    if z is not None:
                        return (x_local, y_local, z)
                    return (x_local, y_local)
                gdf_edges = gdf_edges.copy()
                gdf_edges["geometry"] = gdf_edges["geometry"].apply(lambda g: transform(to_local_transform, g) if g is not None and not g.is_empty else g)
                # Also convert G_roads to edges gdf mode for bridge detection
                G_roads = gdf_edges
    except Exception:
        pass
    
    # Визначаємо мости перед обробкою (використовуємо локальні координати)
    # NOTE: detect_bridges returns bridge AREAS (buffered), not raw edge lines.
    bridges = detect_bridges(G_roads, water_geometries=water_geoms_local)
    
    # Precompute bridge union for splitting polygons: generate bridge deck only where needed.
    bridge_areas = [g for g, _h in (bridges or []) if g is not None and not getattr(g, "is_empty", False)]
    bridge_union = None
    if bridge_areas:
        try:
            bridge_union = unary_union(bridge_areas)
            if bridge_union is not None and getattr(bridge_union, "is_empty", False):
                bridge_union = None
        except Exception:
            bridge_union = None

    print(f"Створення 3D мешу доріг з {len(road_geoms)} полігонів (мостів: {len(bridges) if bridges else 0})...")
    road_meshes = []
    
    for poly in road_geoms:
        try:
            # Використовуємо trimesh.creation.extrude_polygon для надійної екструзії
            # Це автоматично обробляє дірки (holes) та правильно тріангулює
            try:
                def _iter_polys(g):
                    if g is None or getattr(g, "is_empty", False):
                        return []
                    gt = getattr(g, "geom_type", "")
                    if gt == "Polygon":
                        return [g]
                    if gt == "MultiPolygon":
                        return list(g.geoms)
                    if gt == "GeometryCollection":
                        return [gg for gg in g.geoms if getattr(gg, "geom_type", "") == "Polygon"]
                    return []

                def _process_one(poly_part: Polygon, is_bridge: bool, bridge_height_offset: float):
                    # embed not > road height
                    rh = max(float(road_height), 0.0001)
                    re = float(road_embed) if road_embed is not None else 0.0
                    re = max(0.0, min(re, rh * 0.8))

                    if poly_part is None or poly_part.is_empty:
                        return

                    # Clean polygon if needed
                    try:
                        if not poly_part.is_valid:
                            poly_part = poly_part.buffer(0)
                        if poly_part.is_empty:
                            return
                        if hasattr(poly_part, "exterior") and len(poly_part.exterior.coords) < 3:
                            return
                    except Exception:
                        return

                    mesh = trimesh.creation.extrude_polygon(poly_part, height=rh)
                    if mesh is None or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
                        return

                    # Project onto terrain
                    if terrain_provider is not None:
                        vertices = mesh.vertices.copy()
                        old_z = vertices[:, 2].copy()
                        ground_z_values = terrain_provider.get_heights_for_points(vertices[:, :2])

                        if is_bridge:
                            water_level_under_bridge = None
                            if hasattr(terrain_provider, 'original_heights_provider') and terrain_provider.original_heights_provider is not None:
                                original_ground_z = terrain_provider.original_heights_provider.get_heights_for_points(vertices[:, :2])
                                water_levels_per_point = original_ground_z - 0.2
                                median_water_level = float(np.median(water_levels_per_point))
                                min_water_level = float(np.min(water_levels_per_point))
                                max_water_level = float(np.max(water_levels_per_point))

                                min_clearance_above_water = max(float(bridge_height_offset), float(rh) * 2.0)
                                bridge_base_z_per_point = water_levels_per_point + min_clearance_above_water
                                bridge_base_z_per_point = np.maximum(
                                    bridge_base_z_per_point,
                                    ground_z_values + float(bridge_height_offset)
                                )
                                bridge_base_z = float(np.median(bridge_base_z_per_point))
                                vertices[:, 2] = bridge_base_z + old_z

                                print(f"  [BRIDGE] Розрахунок: water_level=[{min_water_level:.2f}, {median_water_level:.2f}, {max_water_level:.2f}]м (median), clearance={min_clearance_above_water:.2f}м, bridge_base={bridge_base_z:.2f}м (median)")
                                water_level_under_bridge = median_water_level

                                try:
                                    support_spacing = max(20.0, float(rh) * 20.0)
                                    support_width = max(2.0, float(rh) * 3.0)
                                    min_support_height = max(1.0, float(rh) * 2.0)
                                    bridge_supports = create_bridge_supports(
                                        poly_part,
                                        bridge_base_z,
                                        terrain_provider,
                                        water_level_under_bridge,
                                        support_spacing=float(support_spacing),
                                        support_width=float(support_width),
                                        min_support_height=float(min_support_height),
                                    )
                                    if bridge_supports:
                                        road_meshes.extend(bridge_supports)
                                        print(f"  [BRIDGE] Створено {len(bridge_supports)} опор для моста")
                                except Exception as e:
                                    print(f"  [WARN] Не вдалося створити опори для моста: {e}")
                            else:
                                # Fallback: small lift relative to bridge_height_offset
                                min_ground_z = float(np.min(ground_z_values))
                                vertices[:, 2] = min_ground_z + old_z + float(bridge_height_offset) * 0.5
                        else:
                            min_ground_z = float(np.min(ground_z_values))
                            max_ground_z = float(np.max(ground_z_values))
                            slope = max_ground_z - min_ground_z
                            adaptive_embed = float(re)
                            if slope > float(re) * 2.0:
                                adaptive_embed = float(re) * (1.0 - min(0.5, (slope - float(re) * 2.0) / (slope + 1.0)))
                            min_height_above_ground = float(rh) * 0.05
                            road_z = ground_z_values + old_z - adaptive_embed
                            min_road_z = ground_z_values + min_height_above_ground
                            vertices[:, 2] = np.maximum(road_z, min_road_z)
                        mesh.vertices = vertices
                    else:
                        if float(re) > 0:
                            vertices = mesh.vertices.copy()
                            vertices[:, 2] = vertices[:, 2] - float(re)
                            mesh.vertices = vertices

                    # Cleanup + color
                    try:
                        mesh.fix_normals()
                        mesh.remove_duplicate_faces()
                        mesh.remove_unreferenced_vertices()
                        if not mesh.is_volume:
                            mesh.fill_holes()
                        mesh.merge_vertices(merge_tex=True, merge_norm=True)
                    except Exception:
                        pass

                    if len(mesh.faces) > 0:
                        road_color = np.array([60, 60, 60, 255], dtype=np.uint8) if is_bridge else np.array([40, 40, 40, 255], dtype=np.uint8)
                        face_colors = np.tile(road_color, (len(mesh.faces), 1))
                        mesh.visual = trimesh.visual.ColorVisuals(face_colors=face_colors)

                    road_meshes.append(mesh)

                # Build parts: bridge pieces + remainder
                parts_to_process: List[Tuple[Polygon, bool, float]] = []
                if bridges and bridge_union is not None:
                    # bridge pieces per bridge area
                    for bridge_area, bridge_h in bridges:
                        if bridge_area is None or bridge_area.is_empty:
                            continue
                        try:
                            inter = poly.intersection(bridge_area)
                        except Exception:
                            continue
                        for g in _iter_polys(inter):
                            if g is None or g.is_empty:
                                continue
                            if float(getattr(g, "area", 0.0) or 0.0) < 1.0:
                                continue
                            parts_to_process.append((g, True, float(bridge_h) * float(bridge_height_multiplier)))
                    # normal remainder
                    try:
                        remainder = poly.difference(bridge_union)
                    except Exception:
                        remainder = poly
                    for g in _iter_polys(remainder):
                        if g is None or g.is_empty:
                            continue
                        if float(getattr(g, "area", 0.0) or 0.0) < 1.0:
                            continue
                        parts_to_process.append((g, False, 0.0))
                else:
                    parts_to_process = [(poly, False, 0.0)]
                
                # Process parts
                for part_poly, is_bridge, bridge_height_offset in parts_to_process:
                    _process_one(part_poly, is_bridge, bridge_height_offset)
                
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

