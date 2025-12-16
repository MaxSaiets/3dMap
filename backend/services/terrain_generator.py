"""
Сервіс для генерації рельєфу з DEM даних
Покращення порівняно з Map2Model - додавання рельєфу
"""
import trimesh
import numpy as np
from typing import Tuple, Optional, Iterable
from services.terrain_provider import TerrainProvider
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.base import BaseGeometry


def create_terrain_mesh(
    bbox_meters: Tuple[float, float, float, float],
    z_scale: float = 1.5,
    resolution: int = 100,
    base_thickness: float = 5.0,
    latlon_bbox: Optional[Tuple[float, float, float, float]] = None,
    source_crs: Optional[object] = None,
    terrarium_zoom: Optional[int] = None,
    flatten_buildings: bool = True,
    building_geometries: Optional[Iterable[BaseGeometry]] = None,
    smoothing_sigma: float = 0.0,
    # Water depression (terrain-first). If provided, carves water into the heightfield (no huge walls mesh for preview).
    water_geometries: Optional[Iterable[BaseGeometry]] = None,
    water_depth_m: float = 0.0,
) -> Tuple[Optional[trimesh.Trimesh], Optional[TerrainProvider]]:
    """
    Створює меш рельєфу для вказаної області
    
    Args:
        bbox: Bounding box (north, south, east, west) в градусах (WGS84)
        z_scale: Множник для висоти рельєфу (для візуального ефекту)
        resolution: Роздільна здатність сітки (кількість точок по одній осі)
    
    Returns:
        Trimesh об'єкт рельєфу або плоска база якщо DEM недоступний
    """
    # bbox_meters: (minx, miny, maxx, maxy) в метрах (UTM / метрична CRS)
    minx, miny, maxx, maxy = bbox_meters
    # Створюємо регулярну сітку в метрах (в абсолютних координатах CRS)
    x = np.linspace(minx, maxx, resolution)
    y = np.linspace(miny, maxy, resolution)
    X, Y = np.meshgrid(x, y, indexing='xy')  # indexing='xy' забезпечує правильний порядок
    
    # Отримуємо висоти (спробує API, якщо не вдалося - синтетичний рельєф)
    Z = get_elevation_data(
        X,
        Y,
        latlon_bbox=latlon_bbox,
        z_scale=z_scale,
        source_crs=source_crs,
        terrarium_zoom=terrarium_zoom,
    )

    # Safety: guarantee Z matches X/Y grid shape.
    # Some providers/mocks may return a scalar or a flat array; we always need a (resolution,resolution) grid.
    try:
        Z = np.asarray(Z, dtype=float)
        if Z.shape != X.shape:
            if Z.size == 1:
                Z = np.full(X.shape, float(Z.reshape(-1)[0]), dtype=float)
            else:
                try:
                    Z = Z.reshape(X.shape)
                except Exception:
                    Z = np.resize(Z, X.shape).astype(float, copy=False)
    except Exception:
        # fallback: flat zero terrain
        Z = np.zeros_like(X, dtype=float)

    # Опційне згладжування висот (прибирає "грубі грані" та шум DEM).
    # Виконуємо ДО flatten під будівлями, щоб flatten працював на стабільному heightfield.
    try:
        sigma = float(smoothing_sigma or 0.0)
        if sigma > 0.0:
            from scipy.ndimage import gaussian_filter

            Z = gaussian_filter(Z.astype(float, copy=False), sigma=sigma, mode="nearest")
    except Exception:
        pass

    # КЛЮЧОВО: "Terrain-first" стабілізація.
    # На шумному DEM або крутих схилах будівлі часто стають "криво":
    # частина основи над землею, частина під землею.
    # Вирішення: локально вирівняти (flatten) рельєф під кожною будівлею до стабільної висоти.
    if flatten_buildings and building_geometries is not None:
        try:
            Z = flatten_heightfield_under_buildings(
                X=X, Y=Y, Z=Z, building_geometries=building_geometries
            )
        except Exception as e:
            print(f"[WARN] flatten_buildings failed: {e}")

    # Water depression: carve water directly into the terrain heightfield.
    # This avoids exporting a deep "water box" that visually overlaps everything in preview.
    try:
        if water_geometries is not None and float(water_depth_m or 0.0) > 0.0:
            Z = depress_heightfield_under_polygons(
                X=X,
                Y=Y,
                Z=Z,
                geometries=water_geometries,
                depth=float(water_depth_m),
                quantile=0.50,
            )
    except Exception as e:
        print(f"[WARN] water depression failed: {e}")
    
    # Safety (again): ensure any post-processing didn't break the grid shape.
    try:
        Z = np.asarray(Z, dtype=float)
        if Z.shape != X.shape:
            if Z.size == 1:
                Z = np.full(X.shape, float(Z.reshape(-1)[0]), dtype=float)
            else:
                try:
                    Z = Z.reshape(X.shape)
                except Exception:
                    Z = np.resize(Z, X.shape).astype(float, copy=False)
    except Exception:
        Z = np.zeros_like(X, dtype=float)

    # Final sanitize: no NaN/Inf in heightfield (Trimesh can break badly if vertices contain NaN).
    Z = np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)

    # Створюємо вершини
    # ВАЖЛИВО: порядок координат [X, Y, Z] де:
    # X = схід/захід (easting) - горизонтальна вісь
    # Y = північ/південь (northing) - вертикальна вісь  
    # Z = висота
    vertices = np.column_stack([
        X.flatten(),  # X координата (схід/захід)
        Y.flatten(),  # Y координата (північ/південь)
        Z.flatten()   # Z координата (висота)
    ])
    vertices = np.nan_to_num(vertices, nan=0.0, posinf=0.0, neginf=0.0)
    
    # Створюємо грані для регулярної сітки
    faces = create_grid_faces(resolution, resolution)
    
    # Створюємо TerrainProvider для інтерполяції висот
    terrain_provider = TerrainProvider(X, Y, Z)
    
    # Створюємо верхню поверхню рельєфу
    terrain_top = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    
    # Перевірка на валідність
    if not terrain_top.is_volume:
        terrain_top.fill_holes()
        terrain_top.update_faces(terrain_top.unique_faces())
    
    # Створюємо твердотільний рельєф (з дном та стінами)
    terrain_solid = create_solid_terrain(
        terrain_top, 
        X, Y, Z, 
        base_thickness=base_thickness
    )
    
    return terrain_solid, terrain_provider


def _iter_polygons(geom: BaseGeometry) -> Iterable[Polygon]:
    """Yield Polygon parts from Polygon/MultiPolygon/GeometryCollection-ish inputs."""
    if geom is None:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        try:
            return [g for g in geom.geoms if isinstance(g, Polygon)]
        except Exception:
            return []
    return []


def flatten_heightfield_under_buildings(
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    building_geometries: Iterable[BaseGeometry],
    quantile: float = 0.90,
    all_touched: bool = True,
    min_cells: int = 2,
) -> np.ndarray:
    """
    Вирівнює рельєф під будівлями (heightfield Z) до стабільної висоти.

    Це робить посадку будівель друк-стабільною і прибирає ефект:
    - "частина в землі / частина в повітрі"
    - "дуже глибоко під землею" через slope_span фундамент.
    """
    Z_out = np.array(Z, dtype=float, copy=True)
    if X.ndim != 2 or Y.ndim != 2 or Z_out.ndim != 2:
        return Z_out
    rows, cols = Z_out.shape
    if rows < 2 or cols < 2:
        return Z_out

    # Bounds/transform для rasterize
    minx = float(np.min(X))
    maxx = float(np.max(X))
    miny = float(np.min(Y))
    maxy = float(np.max(Y))

    try:
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds

        transform = from_bounds(minx, miny, maxx, maxy, cols, rows)

        # Ідемо по кожній будівлі окремо: так вирівнювання локальне (а не одним рівнем на всі).
        for g in building_geometries:
            for poly in _iter_polygons(g):
                if poly is None or poly.is_empty:
                    continue
                try:
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                except Exception:
                    continue
                if poly.is_empty:
                    continue

                mask = rasterize(
                    [(poly, 1)],
                    out_shape=(rows, cols),
                    transform=transform,
                    fill=0,
                    dtype="uint8",
                    all_touched=all_touched,
                ).astype(bool)
                if int(mask.sum()) < int(min_cells):
                    continue
                h = Z_out[mask]
                h = h[np.isfinite(h)]
                if h.size < int(min_cells):
                    continue
                ref = float(np.quantile(h, float(quantile)))
                Z_out[mask] = ref
        return Z_out
    except Exception:
        # Fallback (повільніше): без rasterio rasterize. Просто повертаємо як є.
        return Z_out


def depress_heightfield_under_polygons(
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    geometries: Iterable[BaseGeometry],
    depth: float,
    quantile: float = 0.50,
    all_touched: bool = True,
    min_cells: int = 2,
) -> np.ndarray:
    """
    Carves a depression into the heightfield under provided polygons.
    For water we want a *flat* bottom: use a representative surface level (quantile),
    then subtract depth.
    """
    Z_out = np.array(Z, dtype=float, copy=True)
    if X.ndim != 2 or Y.ndim != 2 or Z_out.ndim != 2:
        return Z_out
    rows, cols = Z_out.shape
    if rows < 2 or cols < 2:
        return Z_out

    depth = float(depth)
    if depth <= 0:
        return Z_out

    minx = float(np.min(X))
    maxx = float(np.max(X))
    miny = float(np.min(Y))
    maxy = float(np.max(Y))

    try:
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds

        transform = from_bounds(minx, miny, maxx, maxy, cols, rows)
        for g in geometries:
            for poly in _iter_polygons(g):
                if poly is None or poly.is_empty:
                    continue
                try:
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                except Exception:
                    continue
                if poly.is_empty:
                    continue

                mask = rasterize(
                    [(poly, 1)],
                    out_shape=(rows, cols),
                    transform=transform,
                    fill=0,
                    dtype="uint8",
                    all_touched=all_touched,
                ).astype(bool)
                if int(mask.sum()) < int(min_cells):
                    continue
                h = Z_out[mask]
                h = h[np.isfinite(h)]
                if h.size < int(min_cells):
                    continue
                surface = float(np.quantile(h, float(quantile)))
                Z_out[mask] = surface - depth
        return Z_out
    except Exception:
        return Z_out
    rows, cols = Z_out.shape
    if rows < 2 or cols < 2:
        return Z_out

    # Bounds/transform для rasterize
    minx = float(np.min(X))
    maxx = float(np.max(X))
    miny = float(np.min(Y))
    maxy = float(np.max(Y))

    try:
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds

        transform = from_bounds(minx, miny, maxx, maxy, cols, rows)

        # Ідемо по кожній будівлі окремо: так вирівнювання локальне (а не одним рівнем на всі).
        for g in building_geometries:
            for poly in _iter_polygons(g):
                if poly is None or poly.is_empty:
                    continue
                try:
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                except Exception:
                    continue
                if poly.is_empty:
                    continue

                mask = rasterize(
                    [(poly, 1)],
                    out_shape=(rows, cols),
                    transform=transform,
                    fill=0,
                    dtype="uint8",
                    all_touched=all_touched,
                ).astype(bool)
                if int(mask.sum()) < int(min_cells):
                    continue
                h = Z_out[mask]
                h = h[np.isfinite(h)]
                if h.size < int(min_cells):
                    continue
                ref = float(np.quantile(h, float(quantile)))
                Z_out[mask] = ref
        return Z_out
    except Exception:
        # Fallback (повільніше): без rasterio rasterize. Просто повертаємо як є.
        return Z_out


def get_elevation_data(
    X: np.ndarray,
    Y: np.ndarray,
    latlon_bbox: Optional[Tuple[float, float, float, float]],
    z_scale: float,
    source_crs: Optional[object] = None,
    terrarium_zoom: Optional[int] = None,
) -> np.ndarray:
    """
    Отримує дані висот для сітки координат
    
    Спробує отримати дані з API, якщо не вдалося - використає синтетичний рельєф.
    
    Args:
        X: Масив X координат (2D meshgrid)
        Y: Масив Y координат (2D meshgrid)
        bbox: Bounding box (north, south, east, west) в градусах
        z_scale: Множник висоти
    
    Returns:
        Масив висот Z (2D, такий самий розмір як X та Y)
    """
    from services.elevation_api import get_elevation_data_from_api, get_elevation_simple_terrain
    
    # Спробуємо отримати дані з API (якщо є latlon_bbox)
    Z = None
    if latlon_bbox is not None and source_crs is not None:
        Z = get_elevation_data_from_api(
            latlon_bbox,
            X,
            Y,
            z_scale,
            source_crs=source_crs,
            terrarium_zoom=terrarium_zoom,
        )
    
    if Z is None:
        # Використовуємо синтетичний рельєф для демонстрації
        # bbox тут не потрібен для математики, але параметр збережено для сумісності
        Z = get_elevation_simple_terrain(X, Y, (0, 0, 0, 0), z_scale)
    
    return Z


def create_grid_faces(rows: int, cols: int) -> np.ndarray:
    """
    Створює грані для регулярної сітки
    
    Args:
        rows: Кількість рядків (Y, північ/південь)
        cols: Кількість стовпців (X, схід/захід)
    
    Returns:
        Масив граней (трикутників)
    
    Примітка: Вершини зберігаються в порядку [X, Y, Z], де:
    - X = схід/захід (cols)
    - Y = північ/південь (rows)
    - Z = висота
    """
    faces = []
    
    for i in range(rows - 1):
        for j in range(cols - 1):
            # Індекси вершин для поточного квадрата
            # i = рядок (Y, північ/південь), j = стовпець (X, схід/захід)
            # Вершини зберігаються в порядку: спочатку всі j для i=0, потім для i=1, і т.д.
            top_left = i * cols + j          # (j, i)     - верхній лівий
            top_right = i * cols + (j + 1)  # (j+1, i)   - верхній правий
            bottom_left = (i + 1) * cols + j      # (j, i+1)   - нижній лівий
            bottom_right = (i + 1) * cols + (j + 1)  # (j+1, i+1) - нижній правий
            
            # Квадрат розбивається на два трикутники
            # Трикутник 1: top_left -> bottom_left -> top_right
            # Трикутник 2: top_right -> bottom_left -> bottom_right
            # Важливо: порядок вершин проти годинникової стрілки для правильних нормалей
            faces.append([top_left, bottom_left, top_right])
            faces.append([top_right, bottom_left, bottom_right])
    
    return np.array(faces)


def create_solid_terrain(
    terrain_top: trimesh.Trimesh,
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    base_thickness: float = 5.0
) -> trimesh.Trimesh:
    """
    Створює твердотільний рельєф з дном та стінами
    
    Args:
        terrain_top: Верхня поверхня рельєфу
        X: 2D масив X координат
        Y: 2D масив Y координат
        Z: 2D масив висот
        base_thickness: Товщина основи в метрах
        
    Returns:
        Твердотільний меш рельєфу
    """
    # Знаходимо мінімальну висоту для бази
    min_z = float(np.min(Z)) - base_thickness
    
    # Отримуємо межі рельєфу
    bounds = terrain_top.bounds
    min_x, min_y = float(bounds[0][0]), float(bounds[0][1])
    max_x, max_y = float(bounds[1][0]), float(bounds[1][1])
    
    # Створюємо дно (плоска поверхня на мінімальній висоті)
    bottom_vertices = np.array([
        [min_x, min_y, min_z],
        [max_x, min_y, min_z],
        [max_x, max_y, min_z],
        [min_x, max_y, min_z]
    ], dtype=np.float64)
    bottom_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    bottom_mesh = trimesh.Trimesh(vertices=bottom_vertices, faces=bottom_faces)
    
    # Створюємо стіни по всіх 4 краях (щоб меш був watertight)
    rows, cols = Z.shape
    side_meshes = []
    verts = terrain_top.vertices

    def idx(i: int, j: int) -> int:
        return i * cols + j

    def add_wall(v1_top: np.ndarray, v2_top: np.ndarray):
        v1_bottom = v1_top.copy()
        v2_bottom = v2_top.copy()
        v1_bottom[2] = min_z
        v2_bottom[2] = min_z
        wall_vertices = np.array([v1_top, v2_top, v2_bottom, v1_bottom], dtype=np.float64)
        wall_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        side_meshes.append(trimesh.Trimesh(vertices=wall_vertices, faces=wall_faces))

    # South edge (i=0)
    for j in range(cols - 1):
        add_wall(verts[idx(0, j)], verts[idx(0, j + 1)])
    # North edge (i=rows-1)
    for j in range(cols - 1):
        add_wall(verts[idx(rows - 1, j + 1)], verts[idx(rows - 1, j)])  # reverse for winding
    # West edge (j=0)
    for i in range(rows - 1):
        add_wall(verts[idx(i + 1, 0)], verts[idx(i, 0)])
    # East edge (j=cols-1)
    for i in range(rows - 1):
        add_wall(verts[idx(i, cols - 1)], verts[idx(i + 1, cols - 1)])
    
    # Об'єднуємо всі частини
    all_meshes = [terrain_top, bottom_mesh] + side_meshes
    try:
        solid_terrain = trimesh.util.concatenate(all_meshes)
        
        # Перевірка на валідність
        if solid_terrain and not solid_terrain.is_volume:
            solid_terrain.fill_holes()
            solid_terrain.update_faces(solid_terrain.unique_faces())
        
        return solid_terrain if solid_terrain else terrain_top
    except Exception as e:
        print(f"Попередження: Не вдалося створити твердотільний рельєф: {e}")
        print("Повертаємо верхню поверхню без дна")
        return terrain_top


def load_dem_file(dem_path: str, bbox: Tuple[float, float, float, float]) -> Optional[np.ndarray]:
    """
    Завантажує DEM файл та обрізає його по bbox
    
    Args:
        dem_path: Шлях до DEM файлу (GeoTIFF)
        bbox: Bounding box для обрізання
    
    Returns:
        Масив висот або None
    """
    try:
        import rasterio
        from rasterio.mask import mask
        
        with rasterio.open(dem_path) as src:
            # Створюємо геометрію для обрізання
            from shapely.geometry import box
            bbox_geom = box(bbox[2], bbox[1], bbox[3], bbox[0])  # west, south, east, north
            
            # Обрізаємо растр
            out_image, out_transform = mask(src, [bbox_geom], crop=True)
            
            return out_image[0]  # Перший канал
            
    except ImportError:
        print("rasterio не встановлено, використовується плоский рельєф")
        return None
    except Exception as e:
        print(f"Помилка завантаження DEM: {e}")
        return None

