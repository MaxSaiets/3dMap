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


def fetch_extras(
    north: float,
    south: float,
    east: float,
    west: float,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    source = (os.getenv("OSM_SOURCE") or "overpass").lower()
    if source in ("pbf", "geofabrik", "local"):
        from services.pbf_loader import fetch_extras_from_pbf

        return fetch_extras_from_pbf(north, south, east, west)

    bbox = (north, south, east, west)

    # Parks/green polygons
    tags_green = {
        "leisure": ["park", "garden", "playground", "recreation_ground", "pitch"],
        "landuse": ["grass", "meadow", "forest", "village_green"],
        "natural": ["wood"],
    }
    # POIs
    tags_pois = {
        "amenity": ["bench", "fountain"],
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
    except Exception:
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

    return gdf_green, gdf_pois


