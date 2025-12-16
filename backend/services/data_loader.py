"""
Сервіс для завантаження даних з OpenStreetMap
Використовує osmnx для отримання структурованих даних
"""
import osmnx as ox
import geopandas as gpd
import pandas as pd
import warnings
from typing import Tuple, Optional
import os
from osmnx._errors import InsufficientResponseError

# Придушення deprecation warnings від pandas/geopandas
warnings.filterwarnings('ignore', category=DeprecationWarning, module='pandas')


def fetch_city_data(
    north: float,
    south: float,
    east: float,
    west: float
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, object]:
    """
    Завантажує дані OSM для вказаної області
    
    Args:
        north: Північна межа (широта)
        south: Південна межа (широта)
        east: Східна межа (довгота)
        west: Західна межа (довгота)
    
    Returns:
        Tuple з (buildings_gdf, water_gdf, roads_graph)
    """
    # Optional best-data mode: local Geofabrik PBF extraction by bbox
    source = (os.getenv("OSM_SOURCE") or "overpass").lower()
    if source in ("pbf", "geofabrik", "local"):
        from services.pbf_loader import fetch_city_data_from_pbf
        buildings, water, roads_edges = fetch_city_data_from_pbf(north, south, east, west)
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

        return buildings, water, roads_edges

    bbox = (north, south, east, west)
    
    # Налаштування osmnx для кращої продуктивності
    ox.settings.use_cache = True
    ox.settings.log_console = False
    
    # 1. Будівлі (+ building:part для більшої деталізації)
    print("Завантаження будівель...")
    tags_buildings = {'building': True}
    tags_building_parts = {'building:part': True}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_buildings = ox.features_from_bbox(*bbox, tags=tags_buildings)
        # Додатково тягнемо building:part (не завжди присутні, але дають кращу деталізацію)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                gdf_parts = ox.features_from_bbox(*bbox, tags=tags_building_parts)
        except Exception:
            gdf_parts = gpd.GeoDataFrame()
        # Фільтрація невалідних геометрій
        gdf_buildings = gdf_buildings[gdf_buildings.geometry.notna()]
        if not gdf_parts.empty:
            gdf_parts = gdf_parts[gdf_parts.geometry.notna()]
        # Проекція в метричну систему (UTM автоматично)
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
    
    # 2. Вода (для вирізання з бази)
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
            gdf_water = ox.features_from_bbox(*bbox, tags=tags_water)
        if not gdf_water.empty:
            gdf_water = gdf_water[gdf_water.geometry.notna()]
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
    
    # 3. Дорожня мережа
    print("Завантаження дорожньої мережі...")
    try:
        # 'all' включає всі типи доріг (drive, walk, bike)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            G_roads = ox.graph_from_bbox(*bbox, network_type='all', simplify=True)
        # Проекція графа в метричну систему
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            G_roads = ox.project_graph(G_roads)
    except Exception as e:
        print(f"Помилка завантаження доріг: {e}")
        G_roads = None
    
    print(f"Завантажено: {len(gdf_buildings)} будівель, {len(gdf_water)} водних об'єктів")
    
    return gdf_buildings, gdf_water, G_roads

