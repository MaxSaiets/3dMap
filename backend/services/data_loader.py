"""
Сервіс для завантаження даних з OpenStreetMap
Використовує osmnx для отримання структурованих даних
Підтримка кешування для швидкого повторного доступу
"""
import osmnx as ox
import geopandas as gpd
import pandas as pd
import warnings
from typing import Tuple, Optional
import os
import hashlib
from pathlib import Path
from osmnx._errors import InsufficientResponseError
import networkx as nx

# Придушення deprecation warnings від pandas/geopandas
warnings.filterwarnings('ignore', category=DeprecationWarning, module='pandas')

# Налаштування кешування
_CACHE_DIR = Path(os.getenv("OSM_DATA_CACHE_DIR") or "cache/osm/overpass_cache")
_CACHE_VERSION = "v2"  # Версія кешу (збільшити при зміні формату)


def _cache_enabled() -> bool:
    """Перевіряє, чи увімкнено кешування"""
    return (os.getenv("OSM_DATA_CACHE_ENABLED") or "1").lower() in ("1", "true", "yes")


def _cache_key(north: float, south: float, east: float, west: float, padding: float) -> str:
    """Створює ключ кешу на основі bbox та padding"""
    # Round to avoid cache fragmentation due to tiny float diffs
    s = f"{_CACHE_VERSION}|overpass|{round(float(north), 6)}|{round(float(south), 6)}|{round(float(east), 6)}|{round(float(west), 6)}|{round(float(padding), 6)}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _clean_gdf_for_parquet(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Очищує GeoDataFrame від колонок зі складними типами даних для збереження в Parquet"""
    df = gdf.copy()
    
    # 1. Явно видаляємо відомі проблемні колонки (але НЕ u/v!)
    problematic_cols = ['nodes', 'ways', 'relations', 'members', 'restrictions']
    cols_to_drop = [c for c in problematic_cols if c in df.columns]
    if cols_to_drop:
        df = df.drop(columns=cols_to_drop)

    # 2. Конвертуємо всі object-колонки в рядки (КРІМ u, v, geometry)
    protected_cols = ['geometry', 'u', 'v', 'key']  # Захищені колонки для графу
    for col in df.columns:
        if col in protected_cols:
            continue
            
        if df[col].dtype == 'object':
            try:
                # Перевіряємо, чи містить колонка списки/словники
                has_complex = False
                sample = df[col].dropna().head(20)
                for val in sample:
                    if isinstance(val, (list, dict, set, tuple)):
                        has_complex = True
                        break
                
                # Конвертуємо в string для безпечного збереження
                df[col] = df[col].astype(str)
            except Exception:
                # Якщо конвертація не вдалася - видаляємо колонку (але не u/v!)
                if col not in protected_cols and col in df.columns:
                    df = df.drop(columns=[col])
                    
    return df


def _save_to_cache(north: float, south: float, east: float, west: float, padding: float,
                   buildings: gpd.GeoDataFrame, water: gpd.GeoDataFrame, roads_graph) -> None:
    """Зберігає дані в кеш"""
    if not _cache_enabled():
        return
    
    try:
        key = _cache_key(north, south, east, west, padding)
        cache_base = _CACHE_DIR / key
        cache_base.mkdir(parents=True, exist_ok=True)
        
        # Зберігаємо будівлі
        if buildings is not None and not buildings.empty:
            try:
                buildings_clean = _clean_gdf_for_parquet(buildings)
                buildings_clean.to_parquet(cache_base / "buildings.parquet", index=False)
            except Exception as e:
                 print(f"[WARN] Помилка збереження будівель в кеш: {e}")
        
        # Зберігаємо воду
        if water is not None and not water.empty:
            try:
                water_clean = _clean_gdf_for_parquet(water)
                water_clean.to_parquet(cache_base / "water.parquet", index=False)
            except Exception as e:
                print(f"[WARN] Помилка збереження води в кеш: {e}")
        
        # Зберігаємо дороги як GeoDataFrame edges
        if roads_graph is not None:
            try:
                # Перевіряємо, чи граф не порожній
                edges_list = list(roads_graph.edges()) if hasattr(roads_graph, 'edges') else []
                if len(edges_list) > 0:
                    print(f"[CACHE] Конвертація {len(edges_list)} edges в GeoDataFrame...")
                    gdf_edges = ox.graph_to_gdfs(roads_graph, nodes=False)
                    if not gdf_edges.empty:
                        print(f"[CACHE] GeoDataFrame має {len(gdf_edges.columns)} колонок.")
                        
                        gdf_edges = _clean_gdf_for_parquet(gdf_edges)
                        
                        # Перевіряємо наявність 'u' та 'v' (потрібні для відновлення графу)
                        if 'u' not in gdf_edges.columns or 'v' not in gdf_edges.columns:
                            print(f"[WARN] GeoDataFrame не містить 'u' та 'v' колонок після очищення")
                            # Спробуємо відновити з індексів, якщо можливо
                            if hasattr(gdf_edges.index, 'names') and len(gdf_edges.index.names) >= 2:
                                gdf_edges = gdf_edges.reset_index()
                                # Ще раз чистимо, бо reset_index може повернути index-колонки як object
                                gdf_edges = _clean_gdf_for_parquet(gdf_edges)
                        
                        try:
                            gdf_edges.to_parquet(cache_base / "roads_edges.parquet", index=False)
                            
                            # Зберігаємо CRS графу для подальшого відновлення
                            import json
                            graph_metadata = {}
                            if hasattr(roads_graph, 'graph') and 'crs' in roads_graph.graph:
                                graph_metadata['crs'] = str(roads_graph.graph['crs'])
                            # Також зберігаємо CRS з GeoDataFrame, якщо є
                            if hasattr(gdf_edges, 'crs') and gdf_edges.crs is not None:
                                graph_metadata['gdf_crs'] = str(gdf_edges.crs)
                            
                            if graph_metadata:
                                with open(cache_base / "roads_metadata.json", 'w') as f:
                                    json.dump(graph_metadata, f)
                            
                            print(f"[CACHE] ✅ Збережено {len(roads_graph.edges())} доріг в кеш: {cache_base}")
                        except Exception as parquet_error:
                            print(f"[WARN] Помилка збереження доріг в Parquet: {parquet_error}")
                            # Спробуємо зберегти тільки основні колонки
                            try:
                                basic_cols = ['geometry', 'u', 'v'] + [c for c in gdf_edges.columns if c not in ['geometry', 'u', 'v'] and gdf_edges[c].dtype in ['int64', 'float64', 'object']]
                                basic_cols = [c for c in basic_cols if c in gdf_edges.columns]
                                gdf_basic = gdf_edges[basic_cols].copy()
                                # Конвертуємо object колонки в string, якщо можливо
                                for col in gdf_basic.columns:
                                    if gdf_basic[col].dtype == 'object' and col not in ['geometry']:
                                        try:
                                            gdf_basic[col] = gdf_basic[col].astype(str)
                                        except:
                                            gdf_basic = gdf_basic.drop(columns=[col])
                                gdf_basic.to_parquet(cache_base / "roads_edges.parquet", index=False)
                                print(f"[CACHE] ✅ Збережено {len(gdf_basic)} доріг в кеш (спрощена версія): {cache_base}")
                            except Exception as e2:
                                print(f"[ERROR] Не вдалося зберегти дороги навіть у спрощеному форматі: {e2}")
                                import traceback
                                traceback.print_exc()
                    else:
                        print(f"[WARN] Граф доріг має {len(edges_list)} edges, але gdf_edges порожній після конвертації")
                else:
                    print(f"[WARN] Граф доріг порожній ({len(edges_list)} edges), не зберігаємо в кеш")
            except Exception as e:
                print(f"[WARN] Помилка збереження доріг в кеш: {e}")
                import traceback
                print(f"[DEBUG] Traceback для доріг:")
                traceback.print_exc()
        else:
            print(f"[CACHE] roads_graph is None, дороги не зберігаються в кеш")
    except Exception as e:
        print(f"[WARN] Помилка збереження в кеш (загальна): {e}")
        import traceback
        print(f"[DEBUG] Повний traceback:")
        traceback.print_exc()


def _load_from_cache(north: float, south: float, east: float, west: float, padding: float) -> Optional[Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, object]]:
    """Завантажує дані з кешу"""
    if not _cache_enabled():
        return None
    
    try:
        key = _cache_key(north, south, east, west, padding)
        cache_base = _CACHE_DIR / key
        
        bpath = cache_base / "buildings.parquet"
        wpath = cache_base / "water.parquet"
        rpath = cache_base / "roads_edges.parquet"
        
        # Перевіряємо наявність файлів (хоча б один має існувати)
        if not (bpath.exists() or wpath.exists() or rpath.exists()):
            print(f"[CACHE] Кеш не знайдено: {cache_base} (ключ: {key})")
            return None
        
        print(f"[CACHE] Кеш знайдено: {cache_base}")
        
        # Завантажуємо будівлі
        buildings = gpd.GeoDataFrame()
        if bpath.exists():
            buildings = gpd.read_parquet(bpath)
        
        # Завантажуємо воду
        water = gpd.GeoDataFrame()
        if wpath.exists():
            water = gpd.read_parquet(wpath)
        
        # Завантажуємо дороги та перетворюємо в граф
        roads_graph = None
        if rpath.exists():
            try:
                gdf_edges = gpd.read_parquet(rpath)
                if not gdf_edges.empty:
                    print(f"[CACHE] Завантажено {len(gdf_edges)} доріг з кешу")
                    # Перетворюємо GeoDataFrame edges назад в NetworkX граф
                    # ВАЖЛИВО: Зберігаємо всі атрибути, включаючи геометрію, для коректної роботи з road_processor
                    roads_graph = nx.MultiDiGraph()
                    edges_added = 0
                    for idx, row in gdf_edges.iterrows():
                        u = row.get('u')
                        v = row.get('v')
                        if u is not None and v is not None:
                            # Копіюємо всі атрибути з рядка, включаючи геометрію
                            # 'u' та 'v' використовуються тільки для додавання edges
                            attrs = {k: v for k, v in row.items() if k not in ['u', 'v']}
                            roads_graph.add_edge(u, v, **attrs)
                            edges_added += 1
                    if edges_added == 0:
                        print(f"[WARN] Не вдалося додати жодної дороги з кешу (проблема з даними)")
                        roads_graph = None
                    else:
                        # Відновлюємо CRS з метаданих
                        import json
                        metadata_path = cache_base / "roads_metadata.json"
                        if metadata_path.exists():
                            try:
                                with open(metadata_path, 'r') as f:
                                    graph_metadata = json.load(f)
                                    # Відновлюємо CRS у графі (потрібно для osmnx)
                                    if 'crs' in graph_metadata:
                                        roads_graph.graph['crs'] = graph_metadata['crs']
                                    elif 'gdf_crs' in graph_metadata:
                                        roads_graph.graph['crs'] = graph_metadata['gdf_crs']
                            except Exception as e:
                                print(f"[WARN] Не вдалося завантажити метадані графу: {e}")
                        
                        # Якщо CRS не знайдено в метаданих, спробуємо використати CRS з GeoDataFrame
                        if 'crs' not in roads_graph.graph and hasattr(gdf_edges, 'crs') and gdf_edges.crs is not None:
                            roads_graph.graph['crs'] = str(gdf_edges.crs)
                        
                        print(f"[CACHE] Створено граф з {edges_added} edges" + (f" (CRS: {roads_graph.graph.get('crs', 'не встановлено')})" if 'crs' in roads_graph.graph else ""))
                else:
                    print(f"[CACHE] Файл доріг існує, але порожній")
            except Exception as e:
                print(f"[WARN] Помилка завантаження доріг з кешу: {e}")
                import traceback
                traceback.print_exc()
        
        print(f"[CACHE] Дані завантажено з кешу: {cache_base}")
        return buildings, water, roads_graph
    except Exception as e:
        print(f"[WARN] Помилка завантаження з кешу: {e}")
        return None


def fetch_city_data(
    north: float,
    south: float,
    east: float,
    west: float,
    padding: float = 0.002  # Буфер для коректної обробки країв (~200 метрів)
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, object]:
    """
    Завантажує дані OSM для вказаної області з буферизацією для коректної обробки країв
    
    Args:
        north: Північна межа (широта)
        south: Південна межа (широта)
        east: Східна межа (довгота)
        west: Західна межа (довгота)
        padding: Буфер для розширення зони запиту (в градусах, ~200м за замовчуванням)
    
    Returns:
        Tuple з (buildings_gdf, water_gdf, roads_graph) - обрізані до оригінального bbox
    """
    fetch_buildings: bool = True
    fetch_water: bool = True
    fetch_roads: bool = True
    
    # Internal kwargs extraction (backward compatibility hack if needed, or simply explicit args update in next step)
    # Actually, let's just use defaults in function signature if we could, but changing signature requires updating all callers.
    # Since I can update main.py, I will change the signature.
    pass

def fetch_city_data(
    north: float,
    south: float,
    east: float,
    west: float,
    padding: float = 0.002,
    fetch_buildings: bool = True,
    fetch_water: bool = True,
    fetch_roads: bool = True
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, object]:
    """
    Завантажує дані OSM для вказаної області з буферизацією.
    Підтримує вибіркове завантаження шарів.
    """
    target_north, target_south, target_east, target_west = north, south, east, west
    
    # Розширюємо зону запиту (буферизація)
    padded_north = north + padding
    padded_south = south - padding
    padded_east = east + padding
    padded_west = west - padding
    
    # Створюємо target_bbox в WGS84 (для обрізки до проекції)
    from shapely.geometry import box as shapely_box
    target_bbox_wgs84 = shapely_box(target_west, target_south, target_east, target_north)
    
    # Визначаємо джерело даних (потрібно для перевірки кешу та збереження)
    source = (os.getenv("OSM_SOURCE") or "overpass").lower()
    
    # Перевіряємо кеш (для Overpass режиму)
    # PBF режим має власний кеш в pbf_loader
    if source not in ("pbf", "geofabrik", "local"):
        if _cache_enabled():
            print(f"[CACHE] Перевірка кешу для bbox: north={target_north:.6f}, south={target_south:.6f}, east={target_east:.6f}, west={target_west:.6f}, padding={padding}")
            cached_data = _load_from_cache(target_north, target_south, target_east, target_west, padding)
            if cached_data is not None:
                buildings_cached, water_cached, roads_cached = cached_data
                # Перевіряємо, чи дані не порожні
                if (buildings_cached is not None or water_cached is not None or roads_cached is not None):
                    # Підрахунок доріг
                    roads_count = 0
                    if roads_cached is not None:
                        if hasattr(roads_cached, 'edges'):
                            try:
                                roads_count = len(list(roads_cached.edges()))
                            except:
                                roads_count = 0
                        elif hasattr(roads_cached, '__len__'):
                            roads_count = len(roads_cached)
                    
                    print(f"[CACHE] ✅ Використано кешовані дані: {len(buildings_cached) if buildings_cached is not None and not buildings_cached.empty else 0} будівель, "
                          f"{len(water_cached) if water_cached is not None and not water_cached.empty else 0} водних об'єктів, "
                          f"{roads_count} доріг")
                    # Виправлено: використовуємо перевірку is None замість or (GeoDataFrame не можна використовувати в булевих контекстах)
                    return (
                        buildings_cached if buildings_cached is not None and not buildings_cached.empty else gpd.GeoDataFrame(),
                        water_cached if water_cached is not None and not water_cached.empty else gpd.GeoDataFrame(),
                        roads_cached
                    )
                else:
                    print("[CACHE] ⚠️ Кеш знайдено, але дані порожні, завантажую з Overpass...")
            else:
                print("[CACHE] ❌ Кеш не знайдено, завантажую з Overpass API...")
        else:
            print("[CACHE] Кешування вимкнено (OSM_DATA_CACHE_ENABLED=0), завантажую з Overpass API...")
    
    # Optional best-data mode: local Geofabrik PBF extraction by bbox
    if source in ("pbf", "geofabrik", "local"):
        print("[INFO] 📁 ДЖЕРЕЛО ДАНИХ: PBF файл (cache/osm/ukraine-latest.osm.pbf)")
        print(f"[INFO] Буферизація: розширено bbox на {padding} градусів (~{padding * 111000:.0f}м) для коректної обробки країв")
        from services.pbf_loader import fetch_city_data_from_pbf
        # Завантажуємо дані для розширеної зони
        buildings, water, roads_edges = fetch_city_data_from_pbf(padded_north, padded_south, padded_east, padded_west)
        # Optional: replace building outlines with footprints (better detail), while keeping OSM heights where possible.
        try:
            from services.footprints_loader import is_footprints_enabled, load_footprints_bbox, transfer_osm_attributes_to_footprints

            if is_footprints_enabled():
                fp = load_footprints_bbox(north, south, east, west, target_crs=getattr(buildings, "crs", None))
                if fp is not None and not fp.empty:
                    fp = transfer_osm_attributes_to_footprints(fp, buildings)
                    # Keep OSM building parts (extra detail) if present
                    if "__is_building_part" in buildings.columns:
                        parts = buildings[buildings["__is_building_part"].fillna(False)]
                        if not parts.empty:
                            buildings = gpd.GeoDataFrame(
                                pd.concat([fp, parts], ignore_index=True),
                                crs=fp.crs or parts.crs,
                            )
                        else:
                            buildings = fp
                    else:
                        buildings = fp
        except Exception as e:
            print(f"[WARN] Footprints integration skipped: {e}")

        # Обрізаємо дані до оригінального bbox
        from shapely.geometry import box as shapely_box
        target_bbox = shapely_box(target_west, target_south, target_east, target_north)
        
        if buildings is not None and not buildings.empty:
            try:
                buildings = buildings[buildings.geometry.intersects(target_bbox)]
            except Exception:
                pass
        if water is not None and not water.empty:
            try:
                water = water[water.geometry.intersects(target_bbox)]
            except Exception:
                pass
        if roads_edges is not None and not roads_edges.empty:
            try:
                roads_edges = roads_edges[roads_edges.geometry.intersects(target_bbox)]
            except Exception:
                pass
        
        return buildings, water, roads_edges

    # Використовуємо розширені координати для завантаження
    padded_bbox = (padded_north, padded_south, padded_east, padded_west)
    bbox = (target_north, target_south, target_east, target_west)  # Для обрізки
    
    print("[INFO] 🌐 ДЖЕРЕЛО ДАНИХ: Overpass API (онлайн)")
    print(f"[INFO] Буферизація: розширено bbox на {padding} градусів (~{padding * 111000:.0f}м) для коректної обробки країв")
    print(f"[INFO] Завантаження даних для розширеного bbox: north={padded_north}, south={padded_south}, east={padded_east}, west={padded_west}")
    
    # Налаштування osmnx: кеш ВИМКНЕНО для меншого використання пам'яті
    ox.settings.use_cache = False
    ox.settings.log_console = False
    
    gdf_buildings = gpd.GeoDataFrame()
    gdf_parts = gpd.GeoDataFrame()
    
    # 1. Будівлі (+ building:part для більшої деталізації)
    if fetch_buildings:
        print("Завантаження будівель...")
        tags_buildings = {'building': True}
        tags_building_parts = {'building:part': True}
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                # Виправляємо виклик для нової версії osmnx
                try:
                    # Нова версія osmnx використовує bbox як keyword argument
                    gdf_buildings = ox.features_from_bbox(bbox=padded_bbox, tags=tags_buildings)
                except TypeError:
                    # Стара версія osmnx використовує позиційні аргументи
                    gdf_buildings = ox.features_from_bbox(padded_bbox[0], padded_bbox[1], padded_bbox[2], padded_bbox[3], tags=tags_buildings)
            # Додатково тягнемо building:part (не завжди присутні, але дають кращу деталізацію)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    # Виправляємо виклик для нової версії osmnx
                    try:
                        gdf_parts = ox.features_from_bbox(bbox=padded_bbox, tags=tags_building_parts)
                    except TypeError:
                        gdf_parts = ox.features_from_bbox(padded_bbox[0], padded_bbox[1], padded_bbox[2], padded_bbox[3], tags=tags_building_parts)
            except Exception:
                gdf_parts = gpd.GeoDataFrame()
            # Фільтрація невалідних геометрій
            gdf_buildings = gdf_buildings[gdf_buildings.geometry.notna()]
            if not gdf_parts.empty:
                gdf_parts = gdf_parts[gdf_parts.geometry.notna()]
            
            # ОБРІЗКА ДО ПРОЕКЦІЇ (в WGS84 координатах)
            if not gdf_buildings.empty:
                try:
                    gdf_buildings = gdf_buildings[gdf_buildings.geometry.intersects(target_bbox_wgs84)]
                except Exception:
                    pass
            if not gdf_parts.empty:
                try:
                    gdf_parts = gdf_parts[gdf_parts.geometry.intersects(target_bbox_wgs84)]
                except Exception:
                    pass
            
            # Проекція в метричну систему (UTM автоматично) - після обрізки
            if not gdf_buildings.empty:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    gdf_buildings = ox.project_gdf(gdf_buildings)
            if not gdf_parts.empty:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    gdf_parts = ox.project_gdf(gdf_parts)

            # Позначаємо parts і додаємо до buildings тільки ті, що мають висотні теги
            if not gdf_parts.empty:
                gdf_parts = gdf_parts.copy()
                gdf_parts["__is_building_part"] = True
                # Якщо part не має height/levels — часто дублює "корпус" без користі → пропускаємо
                has_height = None
                for col in [
                    "height",
                    "building:height",
                    "building:levels",
                    "building:levels:aboveground",
                    "roof:height",
                    "roof:levels",
                ]:
                    if col in gdf_parts.columns:
                        s = gdf_parts[col].notna()
                        has_height = s if has_height is None else (has_height | s)
                if has_height is not None:
                    gdf_parts = gdf_parts[has_height]
                if not gdf_parts.empty:
                    gdf_buildings = gpd.GeoDataFrame(
                        pd.concat([gdf_buildings, gdf_parts], ignore_index=True),
                        crs=gdf_buildings.crs or gdf_parts.crs,
                    )
        except Exception as e:
            print(f"Помилка завантаження будівель: {e}")
            gdf_buildings = gpd.GeoDataFrame()

        # Optional: footprints replacement in Overpass mode too
        try:
            from services.footprints_loader import is_footprints_enabled, load_footprints_bbox, transfer_osm_attributes_to_footprints

            if is_footprints_enabled() and gdf_buildings is not None and not gdf_buildings.empty:
                fp = load_footprints_bbox(north, south, east, west, target_crs=getattr(gdf_buildings, "crs", None))
                if fp is not None and not fp.empty:
                    fp = transfer_osm_attributes_to_footprints(fp, gdf_buildings)
                    # keep parts if present
                    if "__is_building_part" in gdf_buildings.columns:
                        parts = gdf_buildings[gdf_buildings["__is_building_part"].fillna(False)]
                        if not parts.empty:
                            gdf_buildings = gpd.GeoDataFrame(
                                pd.concat([fp, parts], ignore_index=True),
                                crs=fp.crs or parts.crs,
                            )
                        else:
                            gdf_buildings = fp
                    else:
                        gdf_buildings = fp
        except Exception as e:
            print(f"[WARN] Footprints integration skipped: {e}")
    else:
        print("Пропуск завантаження будівель (fetch_buildings=False)")
    
    # 2. Вода (для вирізання з бази)
    gdf_water = gpd.GeoDataFrame()
    if fetch_water:
        print("Завантаження водних об'єктів...")
        # ВАЖЛИВО: не тягнемо всі waterway (канали/лінії), бо це дає "воду де не треба".
        # Беремо тільки реальні полігональні water-об'єкти.
        tags_water = {
            'natural': 'water',
            'water': True,
            'waterway': 'riverbank',
            'landuse': 'reservoir',
        }
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                # Виправляємо виклик для нової версії osmnx
                try:
                    gdf_water = ox.features_from_bbox(bbox=padded_bbox, tags=tags_water)
                except TypeError:
                    gdf_water = ox.features_from_bbox(padded_bbox[0], padded_bbox[1], padded_bbox[2], padded_bbox[3], tags=tags_water)
            if not gdf_water.empty:
                gdf_water = gdf_water[gdf_water.geometry.notna()]
                # ОБРІЗКА ДО ПРОЕКЦІЇ (в WGS84 координатах)
                try:
                    gdf_water = gdf_water[gdf_water.geometry.intersects(target_bbox_wgs84)]
                except Exception:
                    pass
                # Проекція в метричну систему (UTM автоматично) - після обрізки
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    gdf_water = ox.project_gdf(gdf_water)
        except InsufficientResponseError:
            # Це нормальний кейс: в bbox просто немає води за цими тегами
            gdf_water = gpd.GeoDataFrame()
        except Exception as e:
            # Інші помилки (мережа/Overpass) — залишаємо як warning, але не падаємо
            print(f"[WARN] Завантаження води не вдалося: {e}")
            gdf_water = gpd.GeoDataFrame()
    else:
        print("Пропуск завантаження води (fetch_water=False)")
    
    # 3. Дорожня мережа
    G_roads = None
    if fetch_roads:
        print("Завантаження дорожньої мережі...")
        try:
            # 'all' включає всі типи доріг (drive, walk, bike)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                # Використовуємо custom_filter для завантаження І доріг ("highway"), І залізниці ("railway")
                # network_type='all' часто пропускає залізницю
                rail_filter = '["highway"]|["railway"~"rail|tram|light_rail|subway|monorail|narrow_gauge|preserved"]'
                try:
                    # Нова версія osmnx uses custom_filter arg
                    G_roads = ox.graph_from_bbox(bbox=padded_bbox, custom_filter=rail_filter, simplify=True, retain_all=True)
                except TypeError:
                        # Fallback if bbox arg fails or old version
                    G_roads = ox.graph_from_bbox(padded_bbox[0], padded_bbox[1], padded_bbox[2], padded_bbox[3], custom_filter=rail_filter, simplify=True, retain_all=True)
            
            if G_roads is None:
                print("[WARN] osmnx повернув None для графу доріг")
            elif not hasattr(G_roads, 'edges'):
                print("[WARN] Граф доріг не має атрибуту 'edges'")
                G_roads = None
            else:
                edges_count = len(list(G_roads.edges()))
                if edges_count == 0:
                    print("[WARN] Граф доріг порожній після завантаження (0 edges)")
                    G_roads = None
                else:
                    print(f"[DEBUG] Завантажено {edges_count} доріг (до проекції)")
                    # Проекція графа в метричну систему
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", DeprecationWarning)
                        G_roads = ox.project_graph(G_roads)
                        if G_roads is not None and hasattr(G_roads, 'edges'):
                            edges_after = len(list(G_roads.edges()))
                            print(f"[DEBUG] Після проекції: {edges_after} доріг")
                        else:
                            print("[WARN] Граф доріг став None після проекції")
        except Exception as e:
            print(f"[ERROR] Помилка завантаження доріг: {e}")
            import traceback
            traceback.print_exc()
            G_roads = None
    else:
        print("Пропуск завантаження доріг (fetch_roads=False)")
    
    # Обрізка будівель та води вже виконана ДО проекції (в WGS84)
    num_roads = 0
    if G_roads is not None and hasattr(G_roads, 'edges'):
        num_roads = len(G_roads.edges)
    
    print(f"Завантажено (після обрізки): {len(gdf_buildings)} будівель, {len(gdf_water)} водних об'єктів, {num_roads} доріг")
    
    # ВИПРАВЛЕННЯ: Дороги обрізаються занадто агресивно
    # Краще не обрізати дороги взагалі після буферизації - вони вже обрізані графом osmnx
    # Або обрізати м'яко, зберігаючи більше даних на краях
    # Тимчасово вимикаємо обрізку доріг, оскільки вона видаляє всі дороги
    if G_roads is not None:
        try:
            # Перевіряємо, чи є дороги в графі
            if hasattr(G_roads, 'edges') and len(G_roads.edges) > 0:
                # Поки що залишаємо граф без обрізки - osmnx вже завантажив дані для padded_bbox
                # Краще мати більше доріг, ніж не мати їх взагалі
                # Обрізка буде виконана в road_processor при створенні полігонів
                pass
            else:
                print("[WARN] Граф доріг порожній після завантаження")
                G_roads = None
        except Exception as e:
            print(f"[WARN] Помилка перевірки графу доріг: {e}")
            # Залишаємо граф як є
    
    # Зберігаємо в кеш (для Overpass режиму)
    # PBF режим має власний кеш в pbf_loader
    if source not in ("pbf", "geofabrik", "local"):
        if _cache_enabled():
            print(f"[CACHE] Збереження даних в кеш...")
            _save_to_cache(target_north, target_south, target_east, target_west, padding, gdf_buildings, gdf_water, G_roads)
        else:
            print("[CACHE] Кешування вимкнено, дані не збережено в кеш")
    
    return gdf_buildings, gdf_water, G_roads


def load_city_cache(city_cache_key: str) -> Optional[dict]:
    """
    Load city-wide data (specifically water) using the city cache key.
    This reconstructs the city bbox from the stored metadata and fetches/loads
    the water data for the entire city context.
    
    Args:
        city_cache_key: The hash key identifying the city context
        
    Returns:
        Dict with 'water' key containing GeoDataFrame, or None
    """
    try:
        if not city_cache_key:
            return None
            
        # Path to city cache metadata
        cache_dir = Path("cache/cities")
        cache_file = cache_dir / f"city_{city_cache_key}.json"
        
        if not cache_file.exists():
            return None
            
        import json
        with open(cache_file, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
            
        bbox = metadata.get('bbox')
        if not bbox:
            return None
            
        north = float(bbox['north'])
        south = float(bbox['south'])
        east = float(bbox['east'])
        west = float(bbox['west'])
        
        # Determine padding used for city fetch (usually standard padding)
        # We try to load from standard cache first to avoid re-fetching online
        padding = 0.002
        
        # reuse fetch_city_data logic but restricted to checking cache/fetching
        # We only need water for bridge detection
        
        print(f"[CACHE] Loading global city context for key {city_cache_key}...")
        
        # Check standard cache first
        cached_data = _load_from_cache(north, south, east, west, padding)
        if cached_data:
            _, water, _ = cached_data
            if water is not None and not water.empty:
                return {'water': water}
        
        # If not in cache, we might avoid fetching online to prevent huge downloads during a render task
        # But if the user wants global context, maybe we should? 
        # For now, let's strictly return cached data if available. 
        # If it wasn't pre-fetched, we skip global context to avoid blocking.
        
        return None
        
    except Exception as e:
        print(f"[WARN] Failed to load city cache: {e}")
        return None
