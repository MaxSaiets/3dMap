"""
Local OSM PBF loader (Geofabrik) for Ukraine-wide best data.

Goal:
- Avoid Overpass instability/rate limits
- Enable reliable, repeatable results for production

Uses pyrosm to extract features by bbox from a local .osm.pbf.
Optionally auto-downloads the Ukraine PBF from Geofabrik.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple
import os
import warnings

import geopandas as gpd
import osmnx as ox
import pandas as pd
import requests


GEOFABRIK_UKRAINE_PBF_URL = "https://download.geofabrik.de/europe/ukraine-latest.osm.pbf"


def _download_file(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    tmp.replace(dst)


def ensure_ukraine_pbf(pbf_path: Path) -> Path:
    """
    Ensures the PBF exists. If not, optionally auto-downloads from Geofabrik.
    """
    if pbf_path.exists():
        return pbf_path
    auto = (os.getenv("OSM_PBF_AUTO_DOWNLOAD") or "1").lower() in ("1", "true", "yes")
    if not auto:
        raise FileNotFoundError(f"OSM PBF not found: {pbf_path}")
    print(f"[pbf] Downloading Ukraine PBF from Geofabrik to: {pbf_path}")
    _download_file(GEOFABRIK_UKRAINE_PBF_URL, pbf_path)
    return pbf_path


def fetch_city_data_from_pbf(
    north: float,
    south: float,
    east: float,
    west: float,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Returns (buildings_gdf, water_gdf, roads_edges_gdf) in projected CRS (UTM).
    """
    from pyrosm import OSM

    bbox = (north, south, east, west)
    # pyrosm expects [west, south, east, north]
    bb = [west, south, east, north]

    pbf_path = Path(os.getenv("OSM_PBF_PATH") or "cache/osm/ukraine-latest.osm.pbf")
    pbf_path = ensure_ukraine_pbf(pbf_path)

    # Reduce noisy warnings from pyrosm/pandas
    warnings.filterwarnings("ignore", category=UserWarning)

    osm = OSM(str(pbf_path), bounding_box=bb)

    # Buildings
    buildings = osm.get_buildings()
    buildings = buildings if buildings is not None else gpd.GeoDataFrame()

    # building:part (extra detail where available)
    parts = osm.get_data_by_custom_criteria(
        custom_filter={"building:part": True},
        osm_keys_to_keep=[
            "building:part",
            "height",
            "building:height",
            "building:levels",
            "building:levels:aboveground",
            "roof:height",
            "roof:levels",
            "roof:shape",
            "name",
        ],
        filter_type="keep",
    )
    parts = parts if parts is not None else gpd.GeoDataFrame()

    if not parts.empty:
        parts = parts.copy()
        parts["__is_building_part"] = True
        # Keep only parts that carry height/levels/roof info to avoid duplicates
        has_height = None
        for col in [
            "height",
            "building:height",
            "building:levels",
            "building:levels:aboveground",
            "roof:height",
            "roof:levels",
        ]:
            if col in parts.columns:
                s = parts[col].notna()
                has_height = s if has_height is None else (has_height | s)
        if has_height is not None:
            parts = parts[has_height]

    buildings = buildings[buildings.geometry.notna()] if not buildings.empty else buildings
    parts = parts[parts.geometry.notna()] if not parts.empty else parts

    # Water polygons
    water = osm.get_data_by_custom_criteria(
        custom_filter={
            "natural": ["water"],
            "waterway": ["riverbank"],
            "landuse": ["reservoir"],
            "water": True,
        },
        filter_type="keep",
    )
    water = water if water is not None else gpd.GeoDataFrame()
    water = water[water.geometry.notna()] if not water.empty else water

    # Roads as edges GeoDataFrame
    roads = osm.get_network(network_type="all")
    roads = roads if roads is not None else gpd.GeoDataFrame()
    roads = roads[roads.geometry.notna()] if not roads.empty else roads

    # Merge buildings + parts
    if not parts.empty:
        if buildings.empty:
            buildings = parts
        else:
            buildings = gpd.GeoDataFrame(
                pd.concat([buildings, parts], ignore_index=True),
                crs=buildings.crs or parts.crs,
            )

    # Project all to UTM (consistent with current pipeline)
    if not buildings.empty:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            buildings = ox.project_gdf(buildings)
    if not water.empty:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            water = ox.project_gdf(water)
    if not roads.empty:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            roads = ox.project_gdf(roads)

    print(f"[pbf] Loaded: {len(buildings)} buildings, {len(water)} water, {len(roads)} roads edges from PBF")
    return buildings, water, roads


def fetch_extras_from_pbf(
    north: float,
    south: float,
    east: float,
    west: float,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Returns (green_polygons_gdf, poi_points_gdf) in projected CRS (UTM).
    """
    from pyrosm import OSM

    bb = [west, south, east, north]
    pbf_path = Path(os.getenv("OSM_PBF_PATH") or "cache/osm/ukraine-latest.osm.pbf")
    pbf_path = ensure_ukraine_pbf(pbf_path)

    warnings.filterwarnings("ignore", category=UserWarning)
    osm = OSM(str(pbf_path), bounding_box=bb)

    green = osm.get_data_by_custom_criteria(
        custom_filter={
            "leisure": ["park", "garden", "playground", "recreation_ground", "pitch"],
            "landuse": ["grass", "meadow", "forest", "village_green"],
            "natural": ["wood"],
        },
        filter_type="keep",
    )
    green = green if green is not None else gpd.GeoDataFrame()
    green = green[green.geometry.notna()] if not green.empty else green
    if not green.empty:
        green = green[green.geom_type.isin(["Polygon", "MultiPolygon"])]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            green = ox.project_gdf(green)

    pois = osm.get_data_by_custom_criteria(
        custom_filter={"amenity": ["bench", "fountain"]},
        filter_type="keep",
    )
    pois = pois if pois is not None else gpd.GeoDataFrame()
    pois = pois[pois.geometry.notna()] if not pois.empty else pois
    if not pois.empty:
        pois = pois[pois.geom_type.isin(["Point", "MultiPoint"])]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pois = ox.project_gdf(pois)

    print(f"[pbf] Extras: {len(green)} green polygons, {len(pois)} POI points")
    return green, pois


