"""
Extra layers loader:
- parks/green areas (polygons)
- POIs (benches etc.)

Works in two modes:
- OSM_SOURCE=pbf -> read from local Geofabrik PBF via pyrosm
- otherwise -> fetch from Overpass via OSMnx (best-effort)
"""

from __future__ import annotations

import os
import warnings
from typing import Tuple

import geopandas as gpd
import osmnx as ox


# Кеш ВИМКНЕНО: завжди завантажуємо свіжі дані для кожної зони
# _EXTRAS_CACHE: dict[tuple, tuple[float, gpd.GeoDataFrame, gpd.GeoDataFrame]] = {}  # DISABLED


def _bbox_key(north: float, south: float, east: float, west: float) -> tuple[float, float, float, float]:
    return (round(float(north), 6), round(float(south), 6), round(float(east), 6), round(float(west), 6))


def fetch_extras(
    north: float,
    south: float,
    east: float,
    west: float,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    # Перевіряємо чи є preloaded дані (пріоритет)
    try:
        from services.preloaded_data import is_loaded, get_extras_for_bbox
        if is_loaded():
            print("[extras_loader] Використовую preloaded дані")
            green, pois = get_extras_for_bbox(north, south, east, west)
            return green, pois
    except Exception as e:
        print(f"[WARN] Помилка використання preloaded даних для extras: {e}, використовуємо звичайний режим")
    
    # Використовуємо Overpass API за замовчуванням (завантажує тільки для конкретної зони, без кешу)
    # Для використання PBF встановіть OSM_SOURCE=pbf в .env
    source = (os.getenv("OSM_SOURCE") or "overpass").lower()

    # Кеш ВИМКНЕНО: завжди завантажуємо свіжі дані
    ttl_s = 0.0
    # k = (source, _bbox_key(north, south, east, west))  # DISABLED
    # import time as _time
    # now = _time.time()
    # if ttl_s > 0 and k in _EXTRAS_CACHE:  # DISABLED
    #     ...

    if source in ("pbf", "geofabrik", "local"):
        from services.pbf_loader import fetch_extras_from_pbf

        green, pois = fetch_extras_from_pbf(north, south, east, west)
        # Кеш вимкнено: не зберігаємо результати
        # if ttl_s > 0:
        #     _EXTRAS_CACHE[k] = (now, green, pois)  # DISABLED
        return green, pois

    bbox = (north, south, east, west)

    # Вимкнення кешу OSMnx для меншого використання пам'яті
    ox.settings.use_cache = False
    ox.settings.log_console = False

    # Parks/green polygons
    # Parks/green polygons AND other areas (parking, cemeteries, squares, plazas, railways)
    tags_green = {
        "leisure": ["park", "garden", "playground", "recreation_ground", "pitch", "common", "sports_centre"],
        "landuse": [
            "grass", "meadow", "forest", "village_green", "cemetery", "religious", 
            "recreation_ground", "allotments", "plaza", "commercial", "retail", 
            "railway", "construction", "brownfield", "industrial", "garages",
            "farmland", "farmyard", "orchard", "vineyard"
        ],
        "natural": ["wood", "scrub", "heath", "grassland", "sand", "beach"],
        "amenity": [
            "parking", "grave_yard", "university", "school", "college", "kindergarten", 
            "marketplace", "restaurant", "cafe", "fast_food", "bar", "pub", "food_court",
            "ice_cream", "bicycle_parking", "shelter"
        ],
        "man_made": ["pier", "breakwater", "groyne"],
        "place": ["square"],
        "highway": ["pedestrian"], # pedestrian areas (polygons)
        "railway": ["station", "platform"], # platforms are often polygons
    }
    # POIs (points or small polygons to be treated as detailed objects)
    tags_pois = {
        "amenity": ["bench", "fountain", "statue"],
        "historic": ["monument", "memorial", "archaeological_site", "ruins"],
        "tourism": ["attraction", "artwork", "viewpoint"],
        "man_made": ["tower", "mast", "flagpole"],
    }

    gdf_green = gpd.GeoDataFrame()
    gdf_pois = gpd.GeoDataFrame()

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_green = ox.features_from_bbox(*bbox, tags=tags_green)
        if not gdf_green.empty:
            gdf_green = gdf_green[gdf_green.geometry.notna()]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                gdf_green = ox.project_gdf(gdf_green)
            # Keep polygons only
            gdf_green = gdf_green[gdf_green.geom_type.isin(["Polygon", "MultiPolygon"])]
    except Exception as e:
        print(f"[WARN] Failed to fetch green areas: {e}")
        gdf_green = gpd.GeoDataFrame()

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gdf_pois = ox.features_from_bbox(*bbox, tags=tags_pois)
        if not gdf_pois.empty:
            gdf_pois = gdf_pois[gdf_pois.geometry.notna()]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                gdf_pois = ox.project_gdf(gdf_pois)
            # Keep point-like only
            gdf_pois = gdf_pois[gdf_pois.geom_type.isin(["Point", "MultiPoint"])]
    except Exception:
        gdf_pois = gpd.GeoDataFrame()

    # Кеш вимкнено: не зберігаємо результати
    # if ttl_s > 0:
    #     _EXTRAS_CACHE[k] = (now, gdf_green, gdf_pois)  # DISABLED
    return gdf_green, gdf_pois


