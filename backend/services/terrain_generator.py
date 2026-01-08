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
from services.crs_utils import bbox_latlon_to_utm, transform_geometry_to_utm
from services.global_center import GlobalCenter, get_or_create_global_center


def create_terrain_mesh(
    bbox_meters: Tuple[float, float, float, float],
    z_scale: float = 1.5,
    resolution: int = 100,
    base_thickness: float = 5.0,
    latlon_bbox: Optional[Tuple[float, float, float, float]] = None,
    source_crs: Optional[object] = None,
    terrarium_zoom: Optional[int] = None,
    # Baseline logic:
    # - elevation_ref_m: global reference elevation (abs meters above sea level) for city+surroundings
    # - baseline_offset_m: shifts terrain so global reference maps to baseline_mm on the final model
    elevation_ref_m: Optional[float] = None,
    baseline_offset_m: float = 0.0,
    flatten_buildings: bool = True,
    building_geometries: Optional[Iterable[BaseGeometry]] = None,
    # Terrain-first стабілізація для доріг: вирівнюємо heightfield під road polygons,
    # щоб дороги були гладкішими та краще накладались на рельєф.
    flatten_roads: bool = False,
    road_geometries: Optional[Iterable[BaseGeometry]] = None,
    smoothing_sigma: float = 0.0,
    # Water depression (terrain-first). If provided, carves water into the heightfield (no huge walls mesh for preview).
    water_geometries: Optional[Iterable[BaseGeometry]] = None,
    water_depth_m: float = 0.0,
    # Subdivision для плавнішого mesh (збільшує кількість трикутників)
    subdivide: bool = False,
    subdivide_levels: int = 1,
    # Глобальний центр для синхронізації квадратів карти
    global_center: Optional[GlobalCenter] = None,
    # ВАЖЛИВО: якщо True, bbox_meters вже в локальних координатах (не потрібно перетворювати)
    bbox_is_local: bool = False,
    # Полігон зони для форми base та стінок (якщо None - використовується квадратний bbox)
    zone_polygon: Optional[BaseGeometry] = None,
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
    # Перевірка на валідність bbox
    if maxx <= minx or maxy <= miny:
        raise ValueError(f"Invalid bbox: minx={minx}, maxx={maxx}, miny={miny}, maxy={maxy}")
    
    # ВИПРАВЛЕННЯ 3: Розрахунок resolution з урахуванням aspect ratio для збереження пропорцій
    # Це запобігає спотворенню рельєфу (Mercator vs Linear проекція)
    width_m = maxx - minx
    height_m = maxy - miny
    aspect_ratio = width_m / height_m if height_m > 0 else 1.0
    
    # resolution - це кількість точок по довшій стороні
    if width_m > height_m:
        res_x = resolution
        res_y = int(resolution / aspect_ratio)
    else:
        res_y = resolution
        res_x = int(resolution * aspect_ratio)
    
    # Мінімальна роздільна здатність - 10 точок
    res_x = max(10, res_x)
    res_y = max(10, res_y)
    
    # КРИТИЧНЕ ВИПРАВЛЕННЯ: Використання глобального центру для синхронізації квадратів
    # Якщо global_center задано - використовуємо його (єдина точка відліку для всієї карти)
    # Якщо ні - використовуємо локальний центр квадрата (для сумісності)
    if global_center is not None:
        # Використовуємо глобальний центр - всі квадрати мають спільну точку відліку (0,0)
        center_x_utm, center_y_utm = global_center.get_center_utm()
        # ВАЖЛИВО: якщо bbox_meters вже в локальних координатах, не перетворюємо
        if bbox_is_local:
            minx_local, miny_local, maxx_local, maxy_local = minx, miny, maxx, maxy
            print(f"[DEBUG] Використовується ГЛОБАЛЬНИЙ центр: center_utm=({center_x_utm:.2f}, {center_y_utm:.2f})")
            print(f"[DEBUG] BBox вже в локальних координатах: ({minx_local:.2f}, {miny_local:.2f}, {maxx_local:.2f}, {maxy_local:.2f})")
            # ВАЖЛИВО: для локальних координат центр bbox - це середнє значення (для центрування сітки)
            # Але координати вже відносні до глобального центру, тому не потрібно додатково центрувати
            center_x = (minx_local + maxx_local) / 2.0
            center_y = (miny_local + maxy_local) / 2.0
        else:
            # bbox_meters в UTM, конвертуємо в локальні координати відносно глобального центру
            minx_local, miny_local = global_center.to_local(minx, miny)
            maxx_local, maxy_local = global_center.to_local(maxx, maxy)
            print(f"[DEBUG] Використовується ГЛОБАЛЬНИЙ центр: center_utm=({center_x_utm:.2f}, {center_y_utm:.2f})")
            print(f"[DEBUG] BBox перетворено з UTM в локальні: ({minx_local:.2f}, {miny_local:.2f}, {maxx_local:.2f}, {maxy_local:.2f})")
            center_x = (minx_local + maxx_local) / 2.0
            center_y = (miny_local + maxy_local) / 2.0
        to_utm = global_center.to_utm
        to_wgs84 = global_center.to_wgs84
    else:
        # Локальний центр квадрата (для сумісності зі старим кодом)
        center_x = (minx + maxx) / 2.0
        center_y = (miny + maxy) / 2.0
        to_utm = None
        to_wgs84 = None
        if latlon_bbox is not None:
            try:
                north, south, east, west = latlon_bbox
                _, _, _, _, _, to_utm, to_wgs84 = bbox_latlon_to_utm(north, south, east, west)
                print(f"[DEBUG] UTM transformers created for terrain mesh")
            except Exception as e:
                print(f"[WARN] Failed to create UTM transformers: {e}, using provided bbox_meters as-is")
        print(f"[DEBUG] Використовується ЛОКАЛЬНИЙ центр квадрата: center=({center_x:.2f}, {center_y:.2f})")
    
    print(f"[DEBUG] Terrain grid resolution: X={res_x}, Y={res_y}, aspect_ratio={aspect_ratio:.3f}, width={width_m:.1f}m, height={height_m:.1f}m")
    print(f"[DEBUG] Centering coordinates: center=({center_x:.2f}, {center_y:.2f}), offset=({-center_x:.2f}, {-center_y:.2f})")
    
    # Створюємо регулярну сітку в ЛОКАЛЬНИХ координатах (центровано)
    # Якщо використовується глобальний центр - координати відносні до нього
    # Якщо локальний центр - координати відносні до центру квадрата
    if global_center is not None:
        # Використовуємо локальні координати відносно глобального центру
        x_local = np.linspace(minx_local, maxx_local, res_x)
        y_local = np.linspace(miny_local, maxy_local, res_y)
        X_local, Y_local = np.meshgrid(x_local, y_local, indexing='xy')
        # Для отримання висот конвертуємо локальні координати в UTM
        X_utm_flat = X_local.flatten()
        Y_utm_flat = Y_local.flatten()
        X_utm = np.zeros_like(X_utm_flat)
        Y_utm = np.zeros_like(Y_utm_flat)
        for i in range(len(X_utm_flat)):
            x_utm_val, y_utm_val = global_center.from_local(X_utm_flat[i], Y_utm_flat[i])
            X_utm[i] = x_utm_val
            Y_utm[i] = y_utm_val
        X_utm = X_utm.reshape(X_local.shape)
        Y_utm = Y_utm.reshape(Y_local.shape)
    else:
        # Локальний центр квадрата (старий підхід)
        x_local = np.linspace(-width_m/2, width_m/2, res_x)
        y_local = np.linspace(-height_m/2, height_m/2, res_y)
        X_local, Y_local = np.meshgrid(x_local, y_local, indexing='xy')
        # Для отримання висот додаємо центр назад
        X_utm = X_local + center_x
        Y_utm = Y_local + center_y
    
    # Отримуємо висоти (спробує API, якщо не вдалося - синтетичний рельєф)
    # ВАЖЛИВО: Передаємо UTM координати (X_utm, Y_utm) для семплінгу висот
    # get_elevation_data перетворить їх в Lat/Lon для API
    Z = get_elevation_data(
        X_utm,  # Використовуємо UTM координати для семплінгу
        Y_utm,
        latlon_bbox=latlon_bbox,
        z_scale=z_scale,
        source_crs=source_crs,
        terrarium_zoom=terrarium_zoom,
        elevation_ref_m=elevation_ref_m,
        baseline_offset_m=baseline_offset_m,
    )
    
    # Після отримання висот, використовуємо локальні координати для створення mesh
    # X, Y тепер в локальних координатах (центровано)
    X = X_local
    Y = Y_local

    # Stitching mode: protect seam values from any later per-tile operations (flatten/water/etc).
    stitching_mode = bool(elevation_ref_m is not None and np.isfinite(float(elevation_ref_m)))
    Z_seam_ref = None
    if stitching_mode:
        try:
            Z_seam_ref = np.asarray(Z, dtype=float).copy()
        except Exception:
            Z_seam_ref = None

    # ВИПРАВЛЕННЯ 1: Строга перевірка розмірів замість небезпечного reshape
    # Це запобігає "діагональному зсуву" рельєфу (shearing)
    try:
        Z = np.asarray(Z, dtype=float)
        
        # Діагностика: виводимо розміри для відстеження проблем
        print(f"[DEBUG] Elevation data shape check: Z={Z.shape}, Grid X={X.shape}, Grid Y={Y.shape}")
        
        if Z.shape != X.shape:
            # Скаляр - дозволяємо (заповнюємо всю сітку)
            if Z.size == 1:
                print(f"[INFO] Scalar elevation data, filling grid with value={float(Z.reshape(-1)[0])}")
                Z = np.full(X.shape, float(Z.reshape(-1)[0]), dtype=float)
            # Плоский масив точно такого ж розміру - дозволяємо reshape
            elif Z.ndim == 1 and Z.size == X.size:
                print(f"[INFO] Flat array matches grid size, reshaping: {Z.shape} -> {X.shape}")
                Z = Z.reshape(X.shape)
            # 2D масив іншого розміру - інтерполюємо (безпечно)
            elif Z.ndim == 2:
                print(f"[WARN] Z shape {Z.shape} != Grid shape {X.shape}, interpolating...")
                try:
                    from scipy.interpolate import griddata
                    # Створюємо координати для інтерполяції
                    z_rows, z_cols = Z.shape
                    # Використовуємо ті самі межі, що й для основної сітки
                    z_x = np.linspace(float(minx), float(maxx), z_cols)
                    z_y = np.linspace(float(miny), float(maxy), z_rows)
                    z_X, z_Y = np.meshgrid(z_x, z_y, indexing='xy')
                    # Інтерполюємо на нову сітку
                    points = np.column_stack([z_X.flatten(), z_Y.flatten()])
                    values = Z.flatten()
                    # Видаляємо NaN перед інтерполяцією
                    valid = np.isfinite(values)
                    if np.any(valid):
                        Z = griddata(
                            points[valid], 
                            values[valid], 
                            (X, Y), 
                            method='linear', 
                            fill_value=np.nanmean(values[valid]) if np.any(valid) else 0.0
                        )
                        print(f"[INFO] Interpolation successful: {Z.shape}")
                    else:
                        print(f"[WARN] No valid elevation data, using zeros")
                        Z = np.zeros_like(X, dtype=float)
                except ImportError:
                    print(f"[ERROR] scipy not available for interpolation, using zeros")
                    Z = np.zeros_like(X, dtype=float)
            else:
                # КРИТИЧНА ПОМИЛКА: небезпечний reshape призведе до зсуву рельєфу
                # Замість "м'якого порятунку" викидаємо помилку
                raise ValueError(
                    f"CRITICAL MISMATCH: Z shape {Z.shape} does not match Grid shape {X.shape}. "
                    f"Z size={Z.size}, Grid size={X.size}. "
                    f"This would cause terrain shearing (diagonal shift). "
                    f"Check your elevation API provider or zoom levels. "
                    f"Z.ndim={Z.ndim}, Z.size={Z.size}, X.size={X.size}"
                )
        
        # Фінальна перевірка після всіх операцій
        if Z.shape != X.shape:
            raise ValueError(f"FINAL CHECK FAILED: Z shape {Z.shape} != Grid shape {X.shape} after processing")
        
        print(f"[OK] Elevation data matches grid: Z={Z.shape}, Grid={X.shape}")
        
    except ValueError as ve:
        # Перекидаємо ValueError далі (це критична помилка)
        raise ve
    except Exception as e:
        print(f"[ERROR] Failed to process elevation data: {e}")
        import traceback
        traceback.print_exc()
        # Fallback: flat zero terrain (краще ніж спотворений рельєф)
        print(f"[WARN] Using flat zero terrain as fallback")
        Z = np.zeros_like(X, dtype=float)

    # Опційне згладжування висот (прибирає "грубі грані" та шум DEM).
    # IMPORTANT (stitching): any per-tile filter with reflect padding creates different seam values.
    if elevation_ref_m is None or not np.isfinite(float(elevation_ref_m)):
        try:
            sigma = float(smoothing_sigma or 0.0)
            if sigma > 0.0:
                from scipy.ndimage import gaussian_filter
                # Використовуємо 'reflect' замість 'nearest' для кращої обробки країв
                Z = gaussian_filter(Z.astype(float, copy=False), sigma=sigma, mode="reflect")
                # Додаткове легке згладжування для ще плавнішого рельєфу
                Z = gaussian_filter(Z.astype(float, copy=False), sigma=sigma * 0.5, mode="reflect")
            
            # Додаткове згладжування для уникнення фасетованого вигляду
            # Якщо resolution низький, додаємо легке згладжування навіть без явного sigma
            if sigma == 0.0 and resolution < 250:
                # Автоматичне легке згладжування для низької деталізації
                from scipy.ndimage import gaussian_filter
                auto_sigma = max(0.8, 2.0 / (resolution / 100.0))  # Збільшено адаптивний sigma
                Z = gaussian_filter(Z.astype(float, copy=False), sigma=auto_sigma, mode="reflect")
                # Додаткове згладжування
                Z = gaussian_filter(Z.astype(float, copy=False), sigma=auto_sigma * 0.6, mode="reflect")
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

    # Terrain-first стабілізація під дорогами (flatten).
    # Мета: прибрати дрібний "шум" DEM під road polygons, щоб дороги не "пливли" і не ламались.
    if flatten_roads and road_geometries is not None:
        try:
            Z = flatten_heightfield_under_polygons(
                X=X, Y=Y, Z=Z, geometries=road_geometries, quantile=0.50
            )
        except Exception as e:
            print(f"[WARN] flatten_roads failed: {e}")

    # Restore a thin border ring so seams stay identical between adjacent tiles,
    # regardless of what features got clipped differently on each side (roads/buildings/water).
    if stitching_mode and Z_seam_ref is not None:
        try:
            k = 2  # cells
            Z = np.asarray(Z, dtype=float)
            Z_seam_ref = np.asarray(Z_seam_ref, dtype=float)
            Z[:, :k] = Z_seam_ref[:, :k]
            Z[:, -k:] = Z_seam_ref[:, -k:]
            Z[:k, :] = Z_seam_ref[:k, :]
            Z[-k:, :] = Z_seam_ref[-k:, :]
        except Exception:
            pass

    # CRITICAL (stitching): in global elevation sync mode, all tiles must share the same Z floor.
    # Clamp the heightfield to >= 0 BEFORE carving water, so min_floor is identical across zones.
    try:
        if elevation_ref_m is not None and np.isfinite(float(elevation_ref_m)):
            Z = np.maximum(np.asarray(Z, dtype=float), 0.0)
    except Exception:
        pass

    # Water depression: carve water directly into the terrain heightfield.
    # This avoids exporting a deep "water box" that visually overlaps everything in preview.
    # ВАЖЛИВО: зберігаємо оригінальні висоти ПЕРЕД вирізанням для правильного розміщення поверхні води
    # ВИПРАВЛЕННЯ: Перетворюємо water_geometries в локальні координати (відносно глобального або локального центру)
    water_geometries_local = None
    if water_geometries is not None:
        try:
            water_geometries_local = []
            for geom in water_geometries:
                if geom is not None and not geom.is_empty:
                    # Крок 1: Перетворюємо в UTM (якщо потрібно)
                    if to_utm is not None:
                        utm_geom = transform_geometry_to_utm(geom, to_utm)
                    else:
                        # Геометрія вже в UTM (з pbf_loader)
                        utm_geom = geom
                    
                    if utm_geom is not None and not utm_geom.is_empty:
                        # Крок 2: Центруємо - конвертуємо в локальні координати
                        # Якщо використовується глобальний центр - конвертуємо через нього
                        # Якщо локальний - віднімаємо локальний центр
                        def center_transform(x, y, z=None):
                            """Трансформер для центрування координат"""
                            if global_center is not None:
                                # Використовуємо глобальний центр
                                x_local, y_local = global_center.to_local(x, y)
                            else:
                                # Використовуємо локальний центр квадрата
                                x_local = x - center_x
                                y_local = y - center_y
                            if z is not None:
                                return (x_local, y_local, z)
                            return (x_local, y_local)
                        
                        from shapely.ops import transform
                        local_geom = transform(center_transform, utm_geom)
                        
                        if local_geom is not None and not local_geom.is_empty:
                            water_geometries_local.append(local_geom)
            
            if len(water_geometries_local) > 0:
                center_type = "глобальному" if global_center is not None else "локальному"
                print(f"[DEBUG] Transformed and centered {len(water_geometries_local)} water geometries to {center_type} coordinates")
            else:
                water_geometries_local = None
        except Exception as e:
            print(f"[WARN] Failed to transform/center water geometries: {e}, using as-is")
            import traceback
            traceback.print_exc()
            water_geometries_local = water_geometries
    
    Z_original_before_water = Z.copy() if water_geometries_local is not None and float(water_depth_m or 0.0) > 0.0 else None
    try:
        if water_geometries_local is not None and float(water_depth_m or 0.0) > 0.0:
            # ВАЖЛИВО: Використовуємо локальні координати (X, Y) та центровані геометрії
            # Це гарантує ідеальне накладання без floating point precision помилок
            # CRITICAL (stitching): do not allow water carve to reduce the tile's minimum height.
            # Otherwise the solid base becomes a tall "column" and tiles shift differently in export.
            pre_water_floor = float(np.nanmin(Z))
            Z = depress_heightfield_under_polygons(
                X=X,  # Локальні координати (центровані)
                Y=Y,  # Локальні координати (центровані)
                Z=Z,
                geometries=water_geometries_local,  # Центровані геометрії води
                depth=float(water_depth_m),
                min_floor=pre_water_floor,
                quantile=0.1,  # Використовуємо нижчий quantile для глибшого depression
            )
            print(f"[INFO] Water depression вирізано в рельєфі: depth={water_depth_m:.3f}м, quantile=0.1")
    except Exception as e:
        print(f"[WARN] water depression failed: {e}")
        Z_original_before_water = None

    # Restore border ring again after water carve (same reasoning as above).
    if stitching_mode and Z_seam_ref is not None:
        try:
            k = 2
            Z = np.asarray(Z, dtype=float)
            Z_seam_ref = np.asarray(Z_seam_ref, dtype=float)
            Z[:, :k] = Z_seam_ref[:, :k]
            Z[:, -k:] = Z_seam_ref[:, -k:]
            Z[:k, :] = Z_seam_ref[:k, :]
            Z[-k:, :] = Z_seam_ref[-k:, :]
        except Exception:
            pass
    
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
    # CRITICAL: do NOT convert NaN -> 0.0 (it creates huge negative spikes after global normalization).
    try:
        finite = np.isfinite(Z)
        fill = float(np.nanmin(Z[finite])) if np.any(finite) else 0.0
        Z = np.where(finite, Z, fill)
    except Exception:
        Z = np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)
    
    # Додаткова перевірка: якщо всі значення однакові або дуже малі, додаємо мінімальну варіацію
    # IMPORTANT (stitching): never inject noise/contrast in global mode (breaks seams).
    z_range = float(np.max(Z) - np.min(Z))
    if elevation_ref_m is None or not np.isfinite(float(elevation_ref_m)):
        if z_range < 1e-6:
            rng = np.random.RandomState(42)
            noise_amplitude = max(0.1, z_scale * 0.1)
            noise = rng.uniform(-noise_amplitude, noise_amplitude, Z.shape)
            Z = Z + noise
            Z = np.clip(Z, -1e6, 1e6)
        elif z_range < 0.5:
            z_mean = float(np.mean(Z))
            z_contrast = 2.0
            Z = (Z - z_mean) * z_contrast + z_mean

    # Фінальне згладжування для уникнення фасетованого вигляду
    # IMPORTANT (stitching): disable per-tile reflect smoothing in global mode.
    if elevation_ref_m is None or not np.isfinite(float(elevation_ref_m)):
        try:
            from scipy.ndimage import gaussian_filter, uniform_filter
            final_smooth = max(1.0, min(2.0, 250.0 / max(resolution, 100.0)))
            Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth, mode="reflect")
            Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.7, mode="reflect")
            Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.4, mode="reflect")
            try:
                Z_smooth = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.3, mode="reflect")
                Z = Z * 0.85 + Z_smooth * 0.15
            except Exception:
                pass
            try:
                Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.2, mode="reflect")
            except Exception:
                pass
        except Exception:
            pass
    
    # Підсилення контрасту рельєфу для кращої видимості
    # IMPORTANT (stitching): skip in global mode (breaks seams).
    if elevation_ref_m is None or not np.isfinite(float(elevation_ref_m)):
        try:
            z_range = float(np.max(Z) - np.min(Z))
            if z_range > 0.01:
                z_mean = float(np.mean(Z))
                contrast_factor = 1.5
                Z = (Z - z_mean) * contrast_factor + z_mean
                try:
                    from scipy.ndimage import gaussian_gradient_magnitude
                    gradient = gaussian_gradient_magnitude(Z, sigma=1.0)
                    gradient_norm = gradient / (np.max(gradient) + 1e-10)
                    adaptive_boost = 1.0 + gradient_norm * 0.3
                    Z = (Z - z_mean) * adaptive_boost + z_mean
                except Exception:
                    pass
        except Exception:
            pass

    # CRITICAL (stitching): keep a shared global Z floor across zones even after contrast/smoothing.
    try:
        if elevation_ref_m is not None and np.isfinite(float(elevation_ref_m)):
            Z = np.maximum(np.asarray(Z, dtype=float), 0.0)
    except Exception:
        pass
    
    # Створюємо вершини
    # ВАЖЛИВО: порядок координат [X, Y, Z] де:
    # X = схід/захід (easting) - горизонтальна вісь - в ЛОКАЛЬНИХ координатах
    # Y = північ/південь (northing) - вертикальна вісь - в ЛОКАЛЬНИХ координатах
    # Z = висота
    # КРИТИЧНО: X та Y мають бути в локальних координатах (центровані), а не в UTM!
    # Перевіряємо, чи X та Y в локальних координатах
    if global_center is not None:
        # X та Y вже в локальних координатах (X_local, Y_local)
        print(f"[DEBUG] Використовуються локальні координати для вершин: X range=[{np.min(X):.2f}, {np.max(X):.2f}], Y range=[{np.min(Y):.2f}, {np.max(Y):.2f}]")
    else:
        # X та Y в локальних координатах відносно центру квадрата
        print(f"[DEBUG] Використовуються локальні координати (квадрат) для вершин: X range=[{np.min(X):.2f}, {np.max(X):.2f}], Y range=[{np.min(Y):.2f}, {np.max(Y):.2f}]")
    
    vertices = np.column_stack([
        X.flatten(),  # X координата (схід/захід) - в ЛОКАЛЬНИХ координатах
        Y.flatten(),  # Y координата (північ/південь) - в ЛОКАЛЬНИХ координатах
        Z.flatten()   # Z координата (висота)
    ])
    
    # Перевірка на валідність вершин перед обробкою
    if vertices.shape[0] == 0:
        raise ValueError("Vertices array is empty")
    if vertices.shape[1] != 3:
        raise ValueError(f"Invalid vertices shape: {vertices.shape}, expected (N, 3)")
    
    # Очищення від NaN/Inf
    # CRITICAL: do not push NaNs to 0.0 (can create spikes at (0,0,0)).
    try:
        finite = np.isfinite(vertices)
        # fill Z with min finite Z, X/Y with their own min finite values
        v = vertices.copy()
        for c in range(3):
            col = v[:, c]
            m = np.isfinite(col)
            fill = float(np.min(col[m])) if np.any(m) else 0.0
            v[:, c] = np.where(m, col, fill)
        vertices = v
    except Exception:
        vertices = np.nan_to_num(vertices, nan=0.0, posinf=0.0, neginf=0.0)
    
    # Перевірка на дублікати вершин (якщо є багато однакових, це може спричинити проблеми)
    # Але для регулярної сітки це нормально, тому просто перевіряємо на екстремальні значення
    if np.any(np.abs(vertices) > 1e10):
        print("[WARN] Extremely large vertex coordinates detected, clamping")
        vertices = np.clip(vertices, -1e10, 1e10)
    
    # Створюємо грані для регулярної сітки
    # ВИПРАВЛЕННЯ: використовуємо res_y, res_x (рядки, стовпці) для правильного створення граней
    faces = create_grid_faces(res_y, res_x)
    
    # Перевірка граней перед створенням mesh
    if len(faces) == 0:
        raise ValueError("No faces created for terrain mesh")
    
    # Перевірка індексів граней
    max_face_idx = np.max(faces) if len(faces) > 0 else -1
    if max_face_idx >= len(vertices):
        raise ValueError(f"Face index out of bounds: max_index={max_face_idx}, vertices={len(vertices)}")
    
    # Створюємо TerrainProvider для інтерполяції висот
    # ВАЖЛИВО: TerrainProvider містить рельєф з depression (Z після вирізання)
    terrain_provider = TerrainProvider(X, Y, Z)
    
    # Зберігаємо оригінальні висоти ПЕРЕД вирізанням depression для правильного розміщення води
    # Це потрібно для визначення рівня поверхні води (оригінальний рельєф, а не дно depression)
    if Z_original_before_water is not None:
        # Створюємо додатковий TerrainProvider з оригінальними висотами
        terrain_provider_original = TerrainProvider(X, Y, Z_original_before_water)
        # Зберігаємо в атрибуті для доступу з water_processor
        terrain_provider.original_heights_provider = terrain_provider_original
    else:
        terrain_provider.original_heights_provider = None
    
    # Створюємо верхню поверхню рельєфу
    # CRITICAL: if zone_polygon is provided, build terrain_top directly inside the polygon
    # to avoid "stair-stepped" / spiky edges from triangle-level mesh clipping.
    try:
        terrain_top = None
        if zone_polygon is not None and terrain_provider is not None:
            from shapely.geometry import Point
            from shapely.prepared import prep
            from scipy.spatial import Delaunay

            poly = zone_polygon
            if hasattr(poly, "buffer") and (not getattr(poly, "is_valid", True)):
                poly = poly.buffer(0)

            stitching_mode = bool(elevation_ref_m is not None and np.isfinite(float(elevation_ref_m)))

            # Stitching mode must be deterministic and polygon-driven.
            # If buffer(0) ever produces MultiPolygon (rare, but can happen with invalid rings),
            # boundary extraction would fail and the border snaps inward by ~one grid step.
            try:
                from shapely.geometry import Polygon as _Polygon, MultiPolygon as _MultiPolygon
                if isinstance(poly, _MultiPolygon):
                    parts = [p for p in getattr(poly, "geoms", []) if isinstance(p, _Polygon) and (not p.is_empty)]
                    if parts:
                        poly = max(parts, key=lambda p: float(abs(p.area)))
                if stitching_mode and not isinstance(poly, _Polygon):
                    raise ValueError(f"stitching_mode: zone_polygon must be Polygon, got {type(poly).__name__}")
            except Exception:
                # if shapely types aren't available, continue best-effort
                pass
            prep_poly = prep(poly)

            # NOTE: In stitching mode we must keep polygon boundary vertices.
            # Shapely `contains()` is STRICT (returns False on boundary), which would
            # silently drop boundary triangles and produce a jagged/inset outline (crooked hex sides).
            def _in_poly_for_tris(pt: "Point") -> bool:
                if stitching_mode:
                    # Prefer covers() (includes boundary); fallback to contains||touches if not available.
                    try:
                        return bool(getattr(prep_poly, "covers", None)(pt))  # type: ignore[misc]
                    except Exception:
                        try:
                            return bool(poly.covers(pt))  # type: ignore[attr-defined]
                        except Exception:
                            return bool(prep_poly.contains(pt) or prep_poly.touches(pt))
                # legacy: allow boundary too
                return bool(prep_poly.contains(pt) or prep_poly.touches(pt))

            # 1) interior points: take grid points that are inside the polygon.
            # NOTE: in stitching_mode we do NOT include points that merely "touch" the boundary,
            # because those grid-touch points differ per-tile and create jagged borders / spikes.
            xy_grid = np.column_stack([X.flatten(), Y.flatten()])
            inside_mask = np.array(
                [
                    (prep_poly.contains(Point(float(x), float(y))) if stitching_mode else (prep_poly.contains(Point(float(x), float(y))) or prep_poly.touches(Point(float(x), float(y)))))
                    for x, y in xy_grid
                ],
                dtype=bool,
            )
            pts_xy = [tuple(map(float, p)) for p in xy_grid[inside_mask]]

            # 2) boundary points:
            # - stitching_mode: deterministic samples along edges (neighbors generate identical points)
            # - non-stitching: densify along edges for nicer looking borders in single-tile preview
            try:
                coords = list(poly.exterior.coords)
            except Exception:
                coords = []

            boundary_pts = []
            if coords:
                if stitching_mode:
                    if len(coords) >= 2 and coords[0] == coords[-1]:
                        coords = coords[:-1]
                    # Use a spacing tied to the terrain grid step.
                    # If spacing is too large, unconstrained Delaunay can "skip" extreme boundary vertices,
                    # and after face filtering the mesh border snaps inward by ~one grid step (seam gap).
                    try:
                        dx = float(width_m) / max(1.0, float(res_x - 1))
                        dy = float(height_m) / max(1.0, float(res_y - 1))
                        grid_step = max(1e-6, float(min(dx, dy)))
                        edge_spacing = max(1.0, grid_step * 0.9)
                    except Exception:
                        edge_spacing = 5.0

                    ring_xy = [(float(x), float(y)) for x, y, *_ in coords]
                    ring_xy = ring_xy + [ring_xy[0]]
                    for i in range(len(ring_xy) - 1):
                        x1, y1 = ring_xy[i]
                        x2, y2 = ring_xy[i + 1]
                        seg_len = float(np.hypot(x2 - x1, y2 - y1))
                        if seg_len < 1e-6:
                            continue
                        n = max(2, int(seg_len / edge_spacing) + 1)
                        for t in np.linspace(0.0, 1.0, n, dtype=float):
                            p = (float(x1 + (x2 - x1) * t), float(y1 + (y2 - y1) * t))
                            pts_xy.append(p)
                            boundary_pts.append(p)
                else:
                    try:
                        b = poly.bounds
                        zone_size = max(float(b[2] - b[0]), float(b[3] - b[1]))
                        spacing = max(2.0, min(10.0, zone_size / 200.0))  # 2..10m
                    except Exception:
                        spacing = 5.0
                    for i in range(max(0, len(coords) - 1)):
                        x1, y1 = coords[i][0], coords[i][1]
                        x2, y2 = coords[i + 1][0], coords[i + 1][1]
                        seg_len = float(np.hypot(x2 - x1, y2 - y1))
                        n = max(2, int(seg_len / spacing) + 1)
                        for t in np.linspace(0.0, 1.0, n, dtype=float):
                            p = (float(x1 + (x2 - x1) * t), float(y1 + (y2 - y1) * t))
                            pts_xy.append(p)
                            boundary_pts.append(p)

            # De-dupe points.
            # IMPORTANT (stitching): do NOT coarse-round polygon boundary points.
            # Rounding to 0.001m can move a boundary point slightly OUTSIDE the polygon,
            # which then causes all boundary triangles to be filtered out and the tile border
            # snaps inward by ~one grid step (visible seam/gap + "triangular walls").
            round_decimals = 6 if stitching_mode else 3
            pts_xy_arr = np.unique(np.round(np.asarray(pts_xy, dtype=float), round_decimals), axis=0)

            # Z from terrain_provider (already accounts for water depression etc.)
            zs = terrain_provider.get_heights_for_points(pts_xy_arr)

            # Force boundary heights to come from absolute DEM sampling (perfect stitching).
            try:
                if boundary_pts and latlon_bbox is not None:
                    boundary_set = {tuple(np.round(np.asarray(p, dtype=float), round_decimals)) for p in boundary_pts}
                    rounded_all = np.round(pts_xy_arr, round_decimals)
                    boundary_mask = np.array([tuple(p) in boundary_set for p in rounded_all], dtype=bool)
                    if np.any(boundary_mask):
                        bxy = pts_xy_arr[boundary_mask]
                        if global_center is not None:
                            xb = np.zeros((len(bxy), 1), dtype=float)
                            yb = np.zeros((len(bxy), 1), dtype=float)
                            for i in range(len(bxy)):
                                x_utm_val, y_utm_val = global_center.from_local(float(bxy[i, 0]), float(bxy[i, 1]))
                                xb[i, 0] = x_utm_val
                                yb[i, 0] = y_utm_val
                        else:
                            xb = (bxy[:, 0].reshape(-1, 1) + float(center_x))
                            yb = (bxy[:, 1].reshape(-1, 1) + float(center_y))

                        zb = get_elevation_data(
                            xb,
                            yb,
                            latlon_bbox=latlon_bbox,
                            z_scale=z_scale,
                            source_crs=source_crs,
                            terrarium_zoom=terrarium_zoom,
                            elevation_ref_m=elevation_ref_m,
                            baseline_offset_m=baseline_offset_m,
                        )
                        zb = np.asarray(zb, dtype=float).reshape(-1)
                        zs = np.asarray(zs, dtype=float)
                        zs[boundary_mask] = zb
            except Exception:
                pass

            pts3 = np.column_stack([pts_xy_arr[:, 0], pts_xy_arr[:, 1], zs.astype(float)])

            # Triangulate and keep triangles inside/touching polygon.
            tri_faces = None
            tri_vertices = None
            if stitching_mode:
                # Use constrained triangulation so the boundary is EXACTLY the polygon (no inward "grid-step" snap).
                # This eliminates seam gaps and "triangular walls" when other parts (roads/water) reach the true border.
                import triangle as _tri

                # Build a deterministic boundary ring (ordered) from the polygon exterior.
                ring_xy = [(float(x), float(y)) for x, y, *_ in coords]
                if len(ring_xy) >= 2 and ring_xy[0] == ring_xy[-1]:
                    ring_xy = ring_xy[:-1]
                if len(ring_xy) < 3:
                    raise ValueError("stitching_mode: polygon exterior has too few vertices")

                # De-dupe ring consecutive duplicates
                ring_clean = []
                for p in ring_xy:
                    if not ring_clean or (abs(ring_clean[-1][0] - p[0]) > 1e-9 or abs(ring_clean[-1][1] - p[1]) > 1e-9):
                        ring_clean.append(p)
                ring_xy = ring_clean

                # Build vertex list: all points (boundary + interior), de-duped at round_decimals.
                pts_all = np.asarray(pts_xy_arr, dtype=float)
                key = np.round(pts_all, round_decimals)
                # mapping from rounded->index
                mapping = {}
                vertices = []
                for i in range(len(pts_all)):
                    k = (float(key[i, 0]), float(key[i, 1]))
                    if k in mapping:
                        continue
                    mapping[k] = len(vertices)
                    vertices.append([float(pts_all[i, 0]), float(pts_all[i, 1])])
                vertices = np.asarray(vertices, dtype=float)

                # Boundary vertex indices (must exist in mapping)
                ring_idx = []
                for x, y in ring_xy:
                    k = (float(round(x, round_decimals)), float(round(y, round_decimals)))
                    if k not in mapping:
                        # Shouldn't happen, but be strict in stitching mode
                        raise ValueError("stitching_mode: boundary vertex missing from point set")
                    ring_idx.append(int(mapping[k]))

                segments = []
                for i in range(len(ring_idx)):
                    a = ring_idx[i]
                    b = ring_idx[(i + 1) % len(ring_idx)]
                    if a == b:
                        continue
                    segments.append([a, b])
                if len(segments) < 3:
                    raise ValueError("stitching_mode: not enough boundary segments for constrained triangulation")

                tri_in = {"vertices": vertices, "segments": np.asarray(segments, dtype=np.int32)}
                tri_out = _tri.triangulate(tri_in, "pQ")
                tri_vertices = np.asarray(tri_out.get("vertices", []), dtype=float)
                tri_faces = np.asarray(tri_out.get("triangles", []), dtype=np.int32)
                if tri_vertices.size == 0 or tri_faces.size == 0:
                    raise ValueError("stitching_mode: constrained triangulation returned empty mesh")

                # Heights for triangulated vertices
                zs2 = terrain_provider.get_heights_for_points(tri_vertices)

                # Boundary markers: prefer triangle's vertex_markers if present, else fallback to ring membership
                boundary_mask2 = None
                vm = tri_out.get("vertex_markers")
                if vm is not None:
                    vm = np.asarray(vm).reshape(-1)
                    boundary_mask2 = vm != 0
                else:
                    # fallback: points that match any boundary ring coordinate (rounded)
                    bset = {tuple(np.round(np.asarray([x, y], dtype=float), round_decimals)) for x, y in ring_xy}
                    boundary_mask2 = np.array([tuple(np.round(v, round_decimals)) in bset for v in tri_vertices], dtype=bool)

                # Re-sample DEM for boundary vertices (perfect stitching)
                try:
                    if boundary_mask2 is not None and np.any(boundary_mask2) and latlon_bbox is not None:
                        bxy = tri_vertices[boundary_mask2]
                        if global_center is not None:
                            xb = np.zeros((len(bxy), 1), dtype=float)
                            yb = np.zeros((len(bxy), 1), dtype=float)
                            for i in range(len(bxy)):
                                x_utm_val, y_utm_val = global_center.from_local(float(bxy[i, 0]), float(bxy[i, 1]))
                                xb[i, 0] = x_utm_val
                                yb[i, 0] = y_utm_val
                        else:
                            xb = (bxy[:, 0].reshape(-1, 1) + float(center_x))
                            yb = (bxy[:, 1].reshape(-1, 1) + float(center_y))
                        zb = get_elevation_data(
                            xb,
                            yb,
                            latlon_bbox=latlon_bbox,
                            z_scale=z_scale,
                            source_crs=source_crs,
                            terrarium_zoom=terrarium_zoom,
                            elevation_ref_m=elevation_ref_m,
                            baseline_offset_m=baseline_offset_m,
                        )
                        zb = np.asarray(zb, dtype=float).reshape(-1)
                        zs2 = np.asarray(zs2, dtype=float)
                        zs2[boundary_mask2] = zb
                except Exception:
                    pass

                pts3 = np.column_stack([tri_vertices[:, 0], tri_vertices[:, 1], np.asarray(zs2, dtype=float)])
                terrain_top = trimesh.Trimesh(vertices=pts3, faces=tri_faces, process=True)
            else:
                tri = Delaunay(pts_xy_arr)
                tri_faces = np.asarray(tri.simplices, dtype=np.int32)

                tri_pts = pts_xy_arr[tri_faces]  # (F,3,2)
                centroids = tri_pts.mean(axis=1)  # (F,2)
                centroid_keep = np.array([_in_poly_for_tris(Point(float(x), float(y))) for x, y in centroids], dtype=bool)
                if np.any(centroid_keep):
                    kept_idx = np.nonzero(centroid_keep)[0]
                    strict_keep = np.zeros(len(tri_faces), dtype=bool)
                    for fi in kept_idx:
                        ok = True
                        for k in range(3):
                            x = float(tri_pts[fi, k, 0])
                            y = float(tri_pts[fi, k, 1])
                            if not _in_poly_for_tris(Point(x, y)):
                                ok = False
                                break
                        strict_keep[fi] = ok
                    tri_faces = tri_faces[strict_keep]
                else:
                    tri_faces = tri_faces[:0]

                if tri_faces.size > 0:
                    terrain_top = trimesh.Trimesh(vertices=pts3, faces=tri_faces, process=True)

            if stitching_mode and terrain_top is None:
                raise ValueError("stitching_mode: failed to build polygon-aware terrain_top (would fall back to regular grid).")

        if terrain_top is None:
            terrain_top = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    except Exception as e:
        raise ValueError(f"Failed to create terrain mesh: {e}")
    
    # Перевірка на валідність та виправлення проблем
    if terrain_top is None:
        raise ValueError("Terrain top mesh is None")
    
    if len(terrain_top.vertices) == 0:
        raise ValueError("Terrain top mesh has no vertices")
    
    if len(terrain_top.faces) == 0:
        raise ValueError("Terrain top mesh has no faces")
    
    # Видаляємо дегенеровані трикутники (з нульовою площею)
    try:
        terrain_top.remove_duplicate_faces()
        terrain_top.remove_unreferenced_vertices()
    except Exception as e:
        print(f"[WARN] Failed to clean terrain mesh: {e}")
    
    # Перевірка після очищення
    if len(terrain_top.faces) == 0:
        raise ValueError("Terrain mesh has no faces after cleaning")
    
    # Покращення якості mesh: виправлення нормалей для кращого освітлення
    try:
        terrain_top.fix_normals()
    except Exception as e:
        print(f"[WARN] Failed to fix normals: {e}")
    
    # Додаткове згладжування вершин (ТІЛЬКИ для регулярної сітки).
    # Для polygon-aware Delaunay terrain_top кількість вершин != res_x*res_y, тому reshape дає артефакти.
    try:
        if int(terrain_top.vertices.shape[0]) == int(res_y * res_x):
            vertices_2d = terrain_top.vertices.reshape(res_y, res_x, 3)
            z_coords = vertices_2d[:, :, 2]
            from scipy.ndimage import gaussian_filter
            z_smoothed = gaussian_filter(z_coords, sigma=0.5, mode="reflect")
            vertices_2d[:, :, 2] = z_smoothed
            terrain_top.vertices = vertices_2d.reshape(-1, 3)
            terrain_top.fix_normals()
    except Exception as e:
        print(f"[WARN] Vertex smoothing skipped/failed: {e}")
    
    # Заповнюємо дірки та оновлюємо грані
    if not terrain_top.is_watertight:
        try:
            terrain_top.fill_holes()
        except Exception as e:
            print(f"[WARN] Failed to fill holes: {e}")
    
    # Фінальна перевірка
    if len(terrain_top.vertices) == 0 or len(terrain_top.faces) == 0:
        raise ValueError("Terrain mesh is invalid after processing")
    
    # Subdivision для плавнішого mesh (збільшує кількість трикутників)
    if subdivide and subdivide_levels > 0:
        try:
            print(f"[INFO] Applying subdivision (levels={subdivide_levels}) for smoother mesh...")
            initial_verts = len(terrain_top.vertices)
            initial_faces = len(terrain_top.faces)
            
            for level in range(subdivide_levels):
                # Subdivide кожен трикутник на 4 менші (поділяємо кожну сторону навпіл)
                terrain_top = terrain_top.subdivide()
                
                # Після subdivision знову виправляємо нормалі для кращого освітлення
                terrain_top.fix_normals()
                
                # Додаткове згладжування вершин після subdivision для плавності
                try:
                    # Згладжуємо нові вершини для плавнішого вигляду
                    from scipy.spatial import cKDTree
                    tree = cKDTree(terrain_top.vertices)
                    smoothed_verts = terrain_top.vertices.copy()
                    
                    # Для кожної вершини знаходимо сусідів і згладжуємо
                    k_neighbors = min(7, len(terrain_top.vertices))
                    for i, vertex in enumerate(terrain_top.vertices):
                        # Знаходимо найближчих сусідів
                        result = tree.query(vertex, k=k_neighbors)
                        if k_neighbors == 1:
                            distances, indices = result, np.array([result])
                        else:
                            distances, indices = result
                        
                        # Перетворюємо в масив якщо потрібно
                        if not isinstance(indices, np.ndarray):
                            indices = np.array([indices])
                        
                        if len(indices) > 1:
                            # Згладжуємо Z координату як середнє значення сусідів (легко)
                            neighbor_indices = indices[1:] if indices[0] == i else indices
                            if len(neighbor_indices) > 0:
                                neighbor_zs = terrain_top.vertices[neighbor_indices, 2]
                                smoothed_verts[i, 2] = vertex[2] * 0.7 + np.mean(neighbor_zs) * 0.3
                    
                    terrain_top.vertices = smoothed_verts
                    terrain_top.fix_normals()
                except Exception as e:
                    print(f"[WARN] Vertex smoothing after subdivision failed: {e}")
                
                print(f"[INFO] Subdivision level {level + 1} complete: {len(terrain_top.vertices)} vertices (+{len(terrain_top.vertices) - initial_verts}), {len(terrain_top.faces)} faces (+{len(terrain_top.faces) - initial_faces})")
                initial_verts = len(terrain_top.vertices)
                initial_faces = len(terrain_top.faces)
        except Exception as e:
            print(f"[WARN] Subdivision failed: {e}, continuing without subdivision")
    
    # КРИТИЧНО: Якщо є полігон зони, обрізаємо terrain_top ДО створення стінок
    # Це забезпечує, що стінки формуються від обрізаного terrain
    terrain_top_clipped = terrain_top
    if zone_polygon is not None:
        try:
            from services.mesh_clipper import clip_mesh_to_polygon
            from shapely.geometry import Polygon as ShapelyPolygon
            
            # Отримуємо координати полігону
            if hasattr(zone_polygon, 'exterior'):
                poly_coords = list(zone_polygon.exterior.coords)
            else:
                poly_coords = list(zone_polygon.coords)
            
            # Полігон вже в локальних координатах, тому використовуємо як є
            # Створюємо Shapely полігон для перевірки
            poly_coords_2d = [(x, y) for x, y, *_ in poly_coords] if len(poly_coords[0]) > 2 else poly_coords
            shapely_poly = ShapelyPolygon(poly_coords_2d)
            
            if shapely_poly.is_valid and not shapely_poly.is_empty:
                # Якщо верхній рельєф вже побудовано polygon-aware (Delaunay всередині полігону),
                # додаткове mesh-level clipping тільки погіршує край (тонкі трикутники/шипи).
                # Тому кліпаємо ТІЛЬКИ якщо terrain_top явно більший за полігон (регулярна сітка fallback).
                try:
                    tb = terrain_top.bounds
                    pb = shapely_poly.bounds
                    terrain_covers_poly_bbox = (
                        float(tb[0][0]) <= float(pb[0]) and float(tb[0][1]) <= float(pb[1]) and
                        float(tb[1][0]) >= float(pb[2]) and float(tb[1][1]) >= float(pb[3])
                    )
                except Exception:
                    terrain_covers_poly_bbox = True

                # Heuristic: regular grid terrain has exactly res_x*res_y vertices, polygon-aware won't.
                is_regular_grid = False
                try:
                    is_regular_grid = int(len(terrain_top.vertices)) == int(res_x * res_y)
                except Exception:
                    is_regular_grid = False

                if terrain_covers_poly_bbox and is_regular_grid:
                    clipped = clip_mesh_to_polygon(terrain_top, poly_coords_2d, global_center=None, tolerance=0.1)
                    if clipped is not None and len(clipped.vertices) > 0:
                        terrain_top_clipped = clipped
                    else:
                        print(f"[WARN] Не вдалося обрізати terrain по полігону, використовується необрізаний terrain")
        except Exception as e:
            print(f"[WARN] Помилка обрізання terrain по полігону: {e}, використовується необрізаний terrain")
            import traceback
            traceback.print_exc()
    
    # Створюємо твердотільний рельєф (з дном та стінами)
    # Використовуємо обрізаний terrain_top для правильного створення стінок
    #
    # CRITICAL (stitching): when using a GLOBAL elevation_ref_m, tiles must share the same Z-origin.
    # export_scene later shifts each tile so its min Z becomes 0. If each tile has a different base bottom,
    # that per-tile shift breaks height continuity across shared edges.
    #
    # Fix: force a CONSTANT bottom plane across tiles in global elevation mode.
    #
    # IMPORTANT:
    # `Z` in this pipeline has historically been inconsistent between "relative" and "absolute-like" meters
    # depending on provider / legacy code paths.
    # If we assume the wrong space, we get "tower bases" (huge base_thickness) and zones won't stitch.
    #
    # Strategy:
    # - Infer whether `Z` is "absolute-like" (around elevation_ref_m in meters above sea level) vs "relative" (near 0).
    # - Choose a global floor in the SAME space as `Z`.
    # - Adapt base thickness so that min_z (bottom of solid) is exactly that global floor for every tile.
    # IMPORTANT: base_thickness is already computed from mm/scale_factor in world meters.
    # Do NOT adapt it per-tile based on min(Z) — that causes vertical offsets between adjacent zones.
    base_thickness_for_solid = float(base_thickness)

    terrain_solid = create_solid_terrain(
        terrain_top_clipped,  # Використовуємо обрізаний terrain
        X, Y, Z, 
        base_thickness=base_thickness_for_solid,
        zone_polygon=zone_polygon,  # Передаємо полігон зони для форми base та стінок
        terrain_provider=terrain_provider,  # Передаємо TerrainProvider для отримання висот на полігоні
        stitching_mode=bool(elevation_ref_m is not None and np.isfinite(float(elevation_ref_m))),
    )

    # Stitching-mode sanity: the solid MUST reach the zone polygon boundary.
    # If boundary triangles are accidentally filtered out (e.g. due to rounding pushing points outside),
    # the terrain snaps inward by ~one grid step, producing a visible seam gap and "triangular walls"
    # where roads/water still reach the true border.
    if zone_polygon is not None and (elevation_ref_m is not None and np.isfinite(float(elevation_ref_m))):
        try:
            pb = getattr(zone_polygon, "bounds", None)
            mb = terrain_solid.bounds
            if pb is not None and mb is not None:
                poly_minx, poly_miny, poly_maxx, poly_maxy = map(float, pb)
                mesh_minx, mesh_miny = float(mb[0][0]), float(mb[0][1])
                mesh_maxx, mesh_maxy = float(mb[1][0]), float(mb[1][1])

                # expected grid step in local meters
                dx = float(width_m) / max(1.0, float(res_x - 1))
                dy = float(height_m) / max(1.0, float(res_y - 1))
                tol = 0.75 * min(dx, dy)  # ~1 grid step is always wrong; allow some numeric slack

                inset_right = poly_maxx - mesh_maxx
                inset_left = mesh_minx - poly_minx
                inset_top = poly_maxy - mesh_maxy
                inset_bottom = mesh_miny - poly_miny

                if max(inset_right, inset_left, inset_top, inset_bottom) > tol:
                    raise ValueError(
                        "stitching_mode: terrain/base does not reach zone polygon boundary "
                        f"(inset_m right={inset_right:.3f}, left={inset_left:.3f}, top={inset_top:.3f}, bottom={inset_bottom:.3f}; "
                        f"grid_step_m≈{min(dx, dy):.3f})."
                    )
        except Exception as e:
            # In stitching mode we prefer failing loudly over silently exporting incorrect geometry.
            raise
    
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
    quantile: float = 0.90,  # Не використовується, залишено для сумісності
    all_touched: bool = True,
    min_cells: int = 2,
) -> np.ndarray:
    """
    Вирівнює рельєф під будівлями (heightfield Z) до стабільної висоти.

    Це робить посадку будівель друк-стабільною і прибирає ефект:
    - "частина в землі / частина в повітрі"
    - "дуже глибоко під землею" через slope_span фундамент.
    
    ВИПРАВЛЕННЯ: Використовує медіану замість quantile для стабільності.
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
                # ВИПРАВЛЕННЯ: Використовуємо медіану замість quantile для вирівнювання під будівлями
                # Медіана менш чутлива до викидів та дає більш стабільне вирівнювання
                # Це забезпечить, що земля під будівлею буде на стабільному рівні
                ref = float(np.median(h))  # Медіана для стабільності
                Z_out[mask] = ref
                
                # ПЕРЕВІРКА: Перевіряємо, що рельєф дійсно вирівняний
                flattened_heights = Z_out[mask]
                flattened_heights = flattened_heights[np.isfinite(flattened_heights)]
                if len(flattened_heights) > 0:
                    height_range = float(np.max(flattened_heights) - np.min(flattened_heights))
                    if height_range > 0.01:  # Якщо різниця більше 1см - щось не так
                        print(f"[WARN] Рельєф під полігоном не повністю вирівняний: range={height_range:.4f}м")
        
        # Підрахунок вирівняних полігонів
        flattened_count = sum(1 for g in building_geometries for _ in _iter_polygons(g))
        if flattened_count > 0:
            print(f"[DEBUG] Вирівняно рельєф під {flattened_count} полігонами будівель")
        return Z_out
    except Exception as e:
        # Fallback (повільніше): без rasterio rasterize. Просто повертаємо як є.
        print(f"[WARN] flatten_heightfield_under_buildings failed: {e}")
        import traceback
        traceback.print_exc()
        return Z_out


def flatten_heightfield_under_polygons(
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    geometries: Iterable[BaseGeometry],
    quantile: float = 0.50,
    all_touched: bool = True,
    min_cells: int = 2,
) -> np.ndarray:
    """
    Generic terrain-first flattener: set Z under polygons to a stable reference height (quantile of heights).
    Intended for roads/parks/other polygons to make surfaces smoother and more readable.
    """
    Z_out = np.array(Z, dtype=float, copy=True)
    if X.ndim != 2 or Y.ndim != 2 or Z_out.ndim != 2:
        return Z_out
    rows, cols = Z_out.shape
    if rows < 2 or cols < 2:
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
                ref = float(np.quantile(h, float(quantile)))
                Z_out[mask] = ref
        return Z_out
    except Exception:
        return Z_out


def depress_heightfield_under_polygons(
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    geometries: Iterable[BaseGeometry],
    depth: float,
    min_floor: Optional[float] = None,
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

    # If provided, prevent depression from lowering the global minimum.
    # This is CRITICAL for stitched tiles: water carve must not change the tile's min Z,
    # otherwise the solid base becomes taller and per-tile export shifting creates seams.
    if min_floor is not None:
        try:
            min_floor = float(min_floor)
        except Exception:
            min_floor = None

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
                # Для quantile=0.0 використовуємо мінімум, для інших - quantile
                if float(quantile) <= 0.0:
                    surface = float(np.min(h))  # Мінімальна висота для найглибшого depression
                else:
                    surface = float(np.quantile(h, float(quantile)))
                Z_out[mask] = surface - depth
                if min_floor is not None:
                    Z_out[mask] = np.maximum(Z_out[mask], min_floor)
        return Z_out
    except Exception:
        return Z_out


def get_elevation_data(
    X: np.ndarray,
    Y: np.ndarray,
    latlon_bbox: Optional[Tuple[float, float, float, float]],
    z_scale: float,
    source_crs: Optional[object] = None,
    terrarium_zoom: Optional[int] = None,
    elevation_ref_m: Optional[float] = None,
    baseline_offset_m: float = 0.0,
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
    from services.elevation_api import get_elevation_abs_meters_from_api, get_elevation_simple_terrain
    
    # 1) Try absolute DEM first (so we can apply global baseline)
    Z_abs = None
    # If caller didn't provide CRS (common in terrain_only mode), infer it from the lat/lon bbox.
    # Without this we silently fall back to synthetic terrain, which breaks stitching.
    if latlon_bbox is not None and source_crs is None:
        try:
            from services.crs_utils import bbox_latlon_to_utm
            north, south, east, west = latlon_bbox
            _, _, _, _, utm_crs, _, _ = bbox_latlon_to_utm(north, south, east, west)
            source_crs = utm_crs
        except Exception:
            source_crs = None

    if latlon_bbox is not None and source_crs is not None:
        # Використовуємо реальний рельєф з API (Terrarium/OpenTopoData/GeoTIFF)
        # Отримуємо абсолютні висоти в метрах над рівнем моря
        Z_abs = get_elevation_abs_meters_from_api(
            bbox_latlon=latlon_bbox,
            X_meters=X,
            Y_meters=Y,
            source_crs=source_crs,
            terrarium_zoom=terrarium_zoom,
        )
    
    if Z_abs is None:
        # Використовуємо синтетичний рельєф для демонстрації
        # bbox тут не потрібен для математики, але параметр збережено для сумісності
        Z_rel = get_elevation_simple_terrain(X, Y, (0, 0, 0, 0), z_scale)
        # baseline offset (meters) still applies
        try:
            Z_rel = np.asarray(Z_rel, dtype=float) + float(baseline_offset_m or 0.0)
        except Exception:
            pass
        return Z_rel

    # 2) Convert to relative heights:
    # - If elevation_ref_m provided => global reference (city+surroundings)
    # - Otherwise fallback to local minimum normalization (old behavior)
    Z_abs = np.asarray(Z_abs, dtype=float)
    if elevation_ref_m is not None and np.isfinite(elevation_ref_m):
        Z_rel = (Z_abs - float(elevation_ref_m)) * float(z_scale)
    else:
        zmin = float(np.nanmin(Z_abs)) if np.any(np.isfinite(Z_abs)) else 0.0
        Z_rel = (Z_abs - zmin) * float(z_scale)
    
    # Підсилення контрасту для кращої видимості рельєфу
    # CRITICAL: do NOT do per-zone contrast boost when using a global elevation_ref_m,
    # otherwise adjacent tiles get different affine transforms (different means) and won't stitch.
    if elevation_ref_m is None or not np.isfinite(elevation_ref_m):
        z_rel_range = float(np.max(Z_rel) - np.min(Z_rel)) if np.any(np.isfinite(Z_rel)) else 0.0
        if z_rel_range > 0.01:  # Якщо є варіація висот
            z_rel_mean = float(np.nanmean(Z_rel)) if np.any(np.isfinite(Z_rel)) else 0.0
            # Підсилюємо контраст на 80% для кращої видимості
            contrast_boost = 1.8
            Z_rel = (Z_rel - z_rel_mean) * contrast_boost + z_rel_mean

    # 3) Shift baseline so minimum is not 0 (e.g. 1mm on model after scaling)
    try:
        Z_rel = Z_rel + float(baseline_offset_m or 0.0)
    except Exception:
        pass

    # CRITICAL (stitching + base thickness):
    # When using a GLOBAL elevation_ref_m, do NOT allow negative relative heights.
    # Negative Z causes per-tile minZ shifts on export and huge/variable base thickness.
    # Clamp to 0 so all tiles share the same Z floor.
    if elevation_ref_m is not None and np.isfinite(elevation_ref_m):
        try:
            Z_rel = np.maximum(np.asarray(Z_rel, dtype=float), 0.0)
        except Exception:
            pass

    # CRITICAL: never replace missing elevation with 0.0 here.
    # If elevation_ref_m is used, 0.0 becomes a huge negative after (Z_abs - elevation_ref_m),
    # producing extreme terrain_min_z (e.g. -1000m) and breaking base/walls/buildings near edges.
    try:
        finite = np.isfinite(Z_rel)
        fill = float(np.nanmin(Z_rel[finite])) if np.any(finite) else 0.0
        Z_rel = np.where(finite, Z_rel, fill)
    except Exception:
        Z_rel = np.where(np.isnan(Z_rel), float(np.nanmin(Z_rel)) if np.any(np.isfinite(Z_rel)) else 0.0, Z_rel)
    return Z_rel


def create_grid_faces(rows: int, cols: int) -> np.ndarray:
    """
    Створює грані (трикутники) для регулярної сітки вершин
    
    Args:
        rows: Кількість рядків у сітці
        cols: Кількість стовпців у сітці
    
    Returns:
        Масив граней форми (N, 3) де N = (rows-1) * (cols-1) * 2
        Кожна грань - це трикутник з індексами вершин
    """
    faces = []
    for i in range(rows - 1):
        for j in range(cols - 1):
            # Індекси вершин для квадрата клітинки
            top_left = i * cols + j
            top_right = i * cols + (j + 1)
            bottom_left = (i + 1) * cols + j
            bottom_right = (i + 1) * cols + (j + 1)
            
            # Квадрат розбивається на два трикутники
            # Трикутник 1: top_left -> bottom_left -> top_right (CCW)
            faces.append([top_left, bottom_left, top_right])
            # Трикутник 2: top_right -> bottom_left -> bottom_right (CCW)
            faces.append([top_right, bottom_left, bottom_right])
    
    return np.array(faces, dtype=np.int32)


def create_solid_terrain(
    terrain_top: trimesh.Trimesh,
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    base_thickness: float = 5.0,
    zone_polygon: Optional[BaseGeometry] = None,  # Полігон зони для форми base та стінок
    terrain_provider: Optional[object] = None,  # TerrainProvider для отримання висот на полігоні
    stitching_mode: bool = False,  # якщо True: спільний Z-floor між зонами (нижня площина однакова для всіх)
) -> trimesh.Trimesh:
    """
    Створює твердотільний рельєф з дном та стінами (watertight mesh)
    
    Args:
        terrain_top: Верхня поверхня рельєфу
        X: 2D масив X координат
        Y: 2D масив Y координат
        Z: 2D масив висот
        base_thickness: Товщина основи в метрах
        
    Returns:
        Твердотільний меш рельєфу (watertight)
    """
    if terrain_top is None or len(terrain_top.vertices) == 0:
        raise ValueError("Terrain top mesh is invalid")
    
    # Знаходимо мінімальну висоту для бази
    # ВАЖЛИВО: Використовуємо мінімальну висоту з heightfield (Z масив)
    # Але також враховуємо, що base_thickness має бути мінімальною для синхронізації зон
    terrain_min_z = float(np.min(Z))
    terrain_max_z = float(np.max(Z))
    z_range = float(terrain_max_z - terrain_min_z)
    
    # CRITICAL: base_thickness is already computed from mm/scale_factor in world meters.
    # Do NOT clamp it with hardcoded "meters" thresholds: that breaks print thickness and creates per-zone inconsistencies.
    effective_base_thickness = float(base_thickness)
    if not np.isfinite(effective_base_thickness) or effective_base_thickness <= 0:
        effective_base_thickness = 0.001  # safety: 1mm in world units, will be scaled later
    
    # Global stitching mode:
    # - bottom of all tiles must be identical (so "podlozhka" bottoms align)
    # - top of base is a shared Z=0 plane (terrain sits above it; holes avoided by earlier Z>=0 clamp)
    if stitching_mode:
        terrain_min_z = 0.0
        min_z = -effective_base_thickness
    else:
        # Legacy: base top follows the local min of the heightfield.
        min_z = terrain_min_z - effective_base_thickness

    print(
        f"[DEBUG] Terrain height range: {z_range:.2f}м, base_thickness: {effective_base_thickness:.3f}м "
        f"(requested: {float(base_thickness):.3f}м, min: {float(np.min(Z)):.2f}м, max: {float(np.max(Z)):.2f}м, "
        f"stitching_mode={bool(stitching_mode)})"
    )
    
    # Helper: build a watertight solid by extruding the OPEN boundary loop of terrain_top down to min_z.
    # This avoids "teeth" / huge triangles on side walls caused by hole-filling after loose concatenation.
    def _solidify_from_boundary(top_mesh: trimesh.Trimesh, floor_z: float) -> Optional[trimesh.Trimesh]:
        try:
            m = top_mesh.copy()
            if m.faces is None or len(m.faces) == 0:
                return None
            # Boundary edges (each belongs to exactly one face). Do NOT rely on trimesh.edges_boundary
            # because API differs between versions.
            edges = None
            try:
                faces = np.asarray(m.faces, dtype=np.int64)
                e01 = faces[:, [0, 1]]
                e12 = faces[:, [1, 2]]
                e20 = faces[:, [2, 0]]
                all_e = np.vstack([e01, e12, e20])
                all_e_sorted = np.sort(all_e, axis=1)
                # count unique edges
                uniq, counts = np.unique(all_e_sorted, axis=0, return_counts=True)
                edges = uniq[counts == 1]
            except Exception:
                edges = None
            if edges is None or len(edges) < 3:
                return None

            # Build adjacency for boundary graph
            adj: dict[int, list[int]] = {}
            for a, b in edges:
                a = int(a); b = int(b)
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)

            # Walk loops
            loops: list[list[int]] = []
            visited_edges: set[tuple[int, int]] = set()

            def _mark(a: int, b: int):
                visited_edges.add((a, b))
                visited_edges.add((b, a))

            for start in list(adj.keys()):
                # find an unvisited outgoing edge
                nxts = adj.get(start, [])
                if not nxts:
                    continue
                if all((start, n) in visited_edges for n in nxts):
                    continue

                loop = [start]
                prev = None
                cur = start
                # pick first unvisited neighbor
                next_v = None
                for n in nxts:
                    if (cur, int(n)) not in visited_edges:
                        next_v = int(n)
                        break
                if next_v is None:
                    continue

                _mark(cur, next_v)
                prev = cur
                cur = next_v
                loop.append(cur)

                # continue until closed or stuck
                for _ in range(len(edges) + 5):
                    nbrs = adj.get(cur, [])
                    if not nbrs:
                        break
                    # choose neighbor not equal prev, prefer unvisited
                    cand = [int(n) for n in nbrs if int(n) != int(prev)]
                    if not cand:
                        cand = [int(n) for n in nbrs]
                    pick = None
                    for n in cand:
                        if (cur, n) not in visited_edges:
                            pick = n
                            break
                    if pick is None:
                        # allow closing back to start if edge exists
                        if start in cand and (cur, start) not in visited_edges:
                            pick = start
                        else:
                            break
                    _mark(cur, pick)
                    prev, cur = cur, pick
                    if cur == start:
                        break
                    loop.append(cur)

                # Ensure closed loop
                if len(loop) >= 3 and loop[0] == loop[-1]:
                    loop = loop[:-1]
                if len(loop) >= 3:
                    loops.append(loop)

            if not loops:
                return None

            # Pick the largest loop by projected area
            verts = np.asarray(m.vertices, dtype=float)
            best = None
            best_area = -1.0
            for loop in loops:
                try:
                    pts = verts[np.array(loop, dtype=int), :2]
                    from shapely.geometry import Polygon as _Poly
                    poly = _Poly([(float(x), float(y)) for x, y in pts])
                    a = float(abs(poly.area))
                except Exception:
                    a = float(len(loop))
                if a > best_area:
                    best_area = a
                    best = loop
            if best is None or len(best) < 3:
                return None

            loop_idx = [int(i) for i in best]
            loop_xy = verts[np.array(loop_idx, dtype=int), :2]

            # Smooth/simplify boundary to avoid "ribbed" side walls from tiny zig-zags along the triangulation.
            # IMPORTANT: keep Z from the *real* top mesh boundary vertices (do NOT invent new top vertices).
            try:
                from shapely.geometry import LineString as _LineString
                line = _LineString([(float(x), float(y)) for x, y in loop_xy])
                simp = line.simplify(1.0, preserve_topology=True)  # meters
                coords = list(simp.coords)
                if len(coords) >= 3:
                    # Snap simplified coords to nearest existing boundary vertices (preserves true Z)
                    try:
                        from scipy.spatial import cKDTree
                        tree = cKDTree(np.asarray(loop_xy, dtype=float))
                        _, nn = tree.query(np.asarray(coords, dtype=float), k=1)
                        nn = [int(i) for i in np.asarray(nn).reshape(-1)]
                        # De-dup while preserving order
                        picked = []
                        seen = set()
                        for i in nn:
                            if i in seen:
                                continue
                            seen.add(i)
                            picked.append(i)
                        if len(picked) >= 3:
                            loop_idx = [loop_idx[i] for i in picked]
                            loop_xy = verts[np.array(loop_idx, dtype=int), :2]
                    except Exception:
                        pass
            except Exception:
                pass

            # Additional: compress collinear points so long straight edges (hex sides) become flat segments.
            # This keeps walls planar and also helps stitched tiles share exactly the same edge vertices.
            try:
                if len(loop_idx) >= 4:
                    keep = []
                    n = len(loop_idx)
                    # NOTE: coordinates are typically in model units (mm) by this stage; allow tiny floating noise.
                    eps = 1e-6
                    for i in range(n):
                        p0 = loop_xy[(i - 1) % n]
                        p1 = loop_xy[i % n]
                        p2 = loop_xy[(i + 1) % n]
                        v1 = p1 - p0
                        v2 = p2 - p1
                        cross = float(v1[0] * v2[1] - v1[1] * v2[0])
                        if abs(cross) > eps:
                            keep.append(i)
                    if len(keep) >= 3:
                        loop_idx = [loop_idx[i] for i in keep]
                        loop_xy = verts[np.array(loop_idx, dtype=int), :2]
            except Exception:
                pass

            # Create bottom vertices (aligned with boundary XY)
            bottom_map: dict[int, int] = {}
            new_vertices = verts.tolist()
            for vi in loop_idx:
                bottom_map[vi] = len(new_vertices)
                new_vertices.append([float(verts[vi, 0]), float(verts[vi, 1]), float(floor_z)])

            # Wall faces (reuse top boundary vertices)
            wall_faces: list[list[int]] = []
            n = len(loop_idx)
            for i in range(n):
                a = loop_idx[i]
                b = loop_idx[(i + 1) % n]
                a2 = bottom_map[a]
                b2 = bottom_map[b]
                # two triangles
                wall_faces.append([a, b, b2])
                wall_faces.append([a, b2, a2])

            # Bottom triangulation from boundary polygon
            bottom_faces: list[list[int]] = []
            try:
                from shapely.geometry import Polygon as _Poly
                from trimesh.creation import triangulate_polygon
                poly = _Poly([(float(x), float(y)) for x, y in loop_xy])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly is not None and not poly.is_empty:
                    v2d, f2d = triangulate_polygon(poly)
                    # Add bottom interior vertices at floor_z
                    base_offset = len(new_vertices)
                    for x, y in np.asarray(v2d, dtype=float):
                        new_vertices.append([float(x), float(y), float(floor_z)])
                    for tri in np.asarray(f2d, dtype=np.int64):
                        # orient bottom faces downward (flip winding)
                        bottom_faces.append([base_offset + int(tri[2]), base_offset + int(tri[1]), base_offset + int(tri[0])])
            except Exception:
                bottom_faces = []

            # In stitching mode we require a watertight solid. If we can't triangulate the bottom,
            # returning a partial solid will create undefined behavior (and tempting fallbacks).
            if stitching_mode and not bottom_faces:
                return None

            all_faces = np.vstack([np.asarray(m.faces, dtype=np.int64), np.asarray(wall_faces, dtype=np.int64)] + ([np.asarray(bottom_faces, dtype=np.int64)] if bottom_faces else []))
            solid = trimesh.Trimesh(vertices=np.asarray(new_vertices, dtype=float), faces=all_faces, process=True)
            try:
                solid.remove_duplicate_faces()
                solid.remove_unreferenced_vertices()
                solid.fix_normals()
            except Exception:
                pass
            return solid if len(solid.vertices) > 0 and len(solid.faces) > 0 else None
        except Exception:
            return None

    # Try boundary-based solidify first (best quality)
    # Stitching mode is STRICT: we must not invent walls/bottoms via bbox fallbacks,
    # otherwise adjacent tiles can diverge and the seam becomes crooked.
    if stitching_mode:
        solid_boundary = _solidify_from_boundary(terrain_top, min_z)
        if solid_boundary is None:
            raise ValueError("stitching_mode: failed to solidify terrain from top boundary (would fall back to bbox walls).")
        return solid_boundary
    else:
        if zone_polygon is None:
            try:
                solid_boundary = _solidify_from_boundary(terrain_top, min_z)
                if solid_boundary is not None:
                    return solid_boundary
            except Exception:
                pass

    # Отримуємо межі рельєфу
    bounds = terrain_top.bounds
    min_x, min_y = float(bounds[0][0]), float(bounds[0][1])
    max_x, max_y = float(bounds[1][0]), float(bounds[1][1])
    
    # КРИТИЧНО: Якщо є полігон зони, використовуємо його для base та стінок
    # Інакше використовуємо квадратний bbox
    if zone_polygon is not None:
        try:
            # Створюємо НИЖНЮ площину base по формі полігону (БЕЗ екструзії).
            # ВАЖЛИВО: extrude_polygon вже створює бокові стінки.
            # Якщо ми потім ще додаємо свої стінки, виходять "подвійні/криві" стінки + зайві трикутники.
            from shapely.geometry import Polygon as ShapelyPolygon
            from trimesh.creation import triangulate_polygon
            
            # Отримуємо координати полігону
            if hasattr(zone_polygon, 'exterior'):
                coords = list(zone_polygon.exterior.coords)
            else:
                coords = list(zone_polygon.coords)
            
            # Створюємо Shapely полігон для екструзії
            poly_coords_2d = [(x, y) for x, y, *_ in coords] if len(coords[0]) > 2 else coords
            shapely_poly = ShapelyPolygon(poly_coords_2d)
            
            if shapely_poly.is_valid and not shapely_poly.is_empty:
                v2d, f2d = triangulate_polygon(shapely_poly)
                v2d = np.asarray(v2d, dtype=float)
                f2d = np.asarray(f2d, dtype=np.int64)
                if v2d.size > 0 and f2d.size > 0:
                    bottom_vertices = np.column_stack([v2d[:, 0], v2d[:, 1], np.full((len(v2d),), float(min_z), dtype=float)])
                    bottom_faces = []
                    for tri in f2d:
                        # orient bottom downward
                        bottom_faces.append([int(tri[2]), int(tri[1]), int(tri[0])])
                    bottom_mesh = trimesh.Trimesh(vertices=bottom_vertices, faces=np.asarray(bottom_faces, dtype=np.int64), process=True)
                else:
                    raise ValueError("triangulate_polygon returned empty result")
            else:
                # Fallback до квадратного base
                bottom_vertices = np.array([
                    [min_x, min_y, min_z],
                    [max_x, min_y, min_z],
                    [max_x, max_y, min_z],
                    [min_x, max_y, min_z]
                ], dtype=np.float64)
                bottom_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
                bottom_mesh = trimesh.Trimesh(vertices=bottom_vertices, faces=bottom_faces, process=True)
        except Exception as e:
            print(f"[WARN] Помилка створення base по полігону: {e}, використовується квадратний base")
            import traceback
            traceback.print_exc()
            # Fallback до квадратного base
            bottom_vertices = np.array([
                [min_x, min_y, min_z],
                [max_x, min_y, min_z],
                [max_x, max_y, min_z],
                [min_x, max_y, min_z]
            ], dtype=np.float64)
            bottom_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
            bottom_mesh = trimesh.Trimesh(vertices=bottom_vertices, faces=bottom_faces, process=True)
    else:
        # Створюємо дно (плоска поверхня на мінімальній висоті) - квадратний bbox
        bottom_vertices = np.array([
            [min_x, min_y, min_z],
            [max_x, min_y, min_z],
            [max_x, max_y, min_z],
            [min_x, max_y, min_z]
        ], dtype=np.float64)
        bottom_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        bottom_mesh = trimesh.Trimesh(vertices=bottom_vertices, faces=bottom_faces, process=True)
    
    # Створюємо стіни по краю (щоб меш був watertight)
    # КРИТИЧНО: Якщо є полігон зони, використовуємо його для стінок
    # Інакше використовуємо граничні вершини по bbox
    side_meshes = []
    verts = terrain_top.vertices
    
    # Толерантність для визначення граничних вершин (2% від розміру, але мінімум 0.1м)
    tol_x = max((max_x - min_x) * 0.02, 0.1)
    tol_y = max((max_y - min_y) * 0.02, 0.1)
    
    # Знаходимо граничні вершини за координатами
    # ОПТИМІЗАЦІЯ: Обмежуємо кількість вершин для стінок, щоб уникнути занадто багатьох вертикальних ліній
    max_wall_vertices = 50  # Максимальна кількість вершин на одну стінку
    
    def simplify_edge_vertices(edge_verts, sort_axis, max_verts=max_wall_vertices):
        """Спрощує граничні вершини, об'єднуючи близькі"""
        if len(edge_verts) == 0:
            return edge_verts
        
        # Сортуємо по заданій осі
        edge_verts = edge_verts[edge_verts[:, sort_axis].argsort()]
        
        # Якщо вершин занадто багато, вибираємо рівномірно розподілені
        if len(edge_verts) > max_verts:
            # Вибираємо рівномірно розподілені вершини
            indices = np.linspace(0, len(edge_verts) - 1, max_verts, dtype=int)
            edge_verts = edge_verts[indices]
        
        return edge_verts

    def add_wall(v1_top: np.ndarray, v2_top: np.ndarray):
        """Створює стіну з правильним порядком вершин (CCW)"""
        # Перевірка на валідність вершин
        if v1_top is None or v2_top is None or len(v1_top) != 3 or len(v2_top) != 3:
            return

        # Перевірка на NaN/Inf
        if not (np.all(np.isfinite(v1_top)) and np.all(np.isfinite(v2_top))):
            return

        # Використовуємо точні координати вершин (копіюємо для безпеки)
        v1_top = np.array(v1_top, dtype=np.float64).copy()
        v2_top = np.array(v2_top, dtype=np.float64).copy()

        # Створюємо нижні вершини з точно такими ж X, Y координатами
        v1_bottom = v1_top.copy()
        v2_bottom = v2_top.copy()
        v1_bottom[2] = min_z
        v2_bottom[2] = min_z

        # Перевірка на однакові вершини (уникаємо дегенерованих трикутників)
        if np.allclose(v1_top, v2_top, atol=1e-6):
            return

        wall_vertices = np.array([v1_top, v2_top, v2_bottom, v1_bottom], dtype=np.float64)

        # Перевірка на дегенеровані трикутники (перевірка площі)
        tri1_area = 0.5 * np.linalg.norm(np.cross(v2_top - v1_top, v2_bottom - v1_top))
        tri2_area = 0.5 * np.linalg.norm(np.cross(v2_bottom - v1_top, v1_bottom - v1_top))
        if tri1_area < 1e-10 or tri2_area < 1e-10:
            return

        wall_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        try:
            wall_mesh = trimesh.Trimesh(vertices=wall_vertices, faces=wall_faces, process=True)
            if wall_mesh is not None and len(wall_mesh.vertices) > 0 and len(wall_mesh.faces) > 0:
                side_meshes.append(wall_mesh)
        except Exception:
            pass
    
    # Ініціалізуємо змінні для bbox стінок
    south_verts = np.array([])
    north_verts = np.array([])
    west_verts = np.array([])
    east_verts = np.array([])
    
    # КРИТИЧНО: Якщо є полігон зони, використовуємо його для стінок
    # Створюємо стінки безпосередньо від полігону зони, а не від вершин terrain
    use_bbox_walls = True
    if zone_polygon is not None:
        # Build flat walls strictly along the polygon edges (no ribbed/zig-zag walls).
        # Also compress collinear points so hex edges become exactly 6 segments.
        try:
            # Optional KDTree for fast nearest lookup on terrain_top vertices
            try:
                from scipy.spatial import cKDTree
                _edge_tree = cKDTree(np.asarray(verts[:, :2], dtype=float))
            except Exception:
                _edge_tree = None

            # Extract polygon coords
            if hasattr(zone_polygon, "exterior"):
                raw = list(zone_polygon.exterior.coords)
            else:
                raw = list(getattr(zone_polygon, "coords", []))

            poly2d = [(float(p[0]), float(p[1])) for p in raw if p is not None and len(p) >= 2]
            if len(poly2d) >= 2 and poly2d[0] == poly2d[-1]:
                poly2d = poly2d[:-1]

            # Remove collinear points (keep corners only)
            def _compress_collinear(pts: list[tuple[float, float]], eps: float = 1e-9) -> list[tuple[float, float]]:
                if len(pts) < 4:
                    return pts
                out: list[tuple[float, float]] = []
                n = len(pts)
                for i in range(n):
                    p0 = pts[(i - 1) % n]
                    p1 = pts[i % n]
                    p2 = pts[(i + 1) % n]
                    v1x, v1y = (p1[0] - p0[0]), (p1[1] - p0[1])
                    v2x, v2y = (p2[0] - p1[0]), (p2[1] - p1[1])
                    cross = v1x * v2y - v1y * v2x
                    if abs(cross) > eps:
                        out.append(p1)
                # de-dupe consecutive duplicates
                dedup: list[tuple[float, float]] = []
                for p in out:
                    if not dedup or (abs(dedup[-1][0] - p[0]) > 1e-9 or abs(dedup[-1][1] - p[1]) > 1e-9):
                        dedup.append(p)
                return dedup if len(dedup) >= 3 else pts

            poly2d = _compress_collinear(poly2d)
            if len(poly2d) < 3:
                raise ValueError("zone_polygon has too few vertices for walls")

            # Close ring
            ring = poly2d + [poly2d[0]]

            nearest_tol = 2.0  # meters (local coords)

            def get_terrain_height_at_xy(x: float, y: float) -> float:
                # Prefer nearest vertex on terrain_top if close
                if _edge_tree is not None:
                    try:
                        dist, idx = _edge_tree.query([float(x), float(y)], k=1)
                        if np.isfinite(dist) and float(dist) <= float(nearest_tol):
                            return float(verts[int(idx), 2])
                    except Exception:
                        pass
                # TerrainProvider interpolation fallback
                if terrain_provider is not None:
                    try:
                        if hasattr(terrain_provider, "get_height_at"):
                            h = terrain_provider.get_height_at(float(x), float(y))
                        elif hasattr(terrain_provider, "get_height"):
                            h = terrain_provider.get_height(float(x), float(y))
                        else:
                            h = None
                        if h is not None and np.isfinite(h):
                            return float(h)
                    except Exception:
                        pass
                # last resort: nearest by brute force
                d = np.sqrt((verts[:, 0] - float(x)) ** 2 + (verts[:, 1] - float(y)) ** 2)
                j = int(np.argmin(d))
                return float(verts[j, 2])

            made = 0
            for i in range(len(ring) - 1):
                x1, y1 = ring[i]
                x2, y2 = ring[i + 1]
                if float(np.hypot(x2 - x1, y2 - y1)) < 0.1:
                    continue
                z1 = get_terrain_height_at_xy(x1, y1)
                z2 = get_terrain_height_at_xy(x2, y2)
                if not (np.isfinite(z1) and np.isfinite(z2)):
                    continue
                z1 = max(float(z1), float(terrain_min_z))
                z2 = max(float(z2), float(terrain_min_z))
                add_wall(np.array([x1, y1, z1], dtype=np.float64), np.array([x2, y2, z2], dtype=np.float64))
                made += 1

            if made >= 3:
                use_bbox_walls = False
        except Exception as e:
            print(f"[WARN] Zone polygon walls failed: {e}; falling back to bbox walls")
    
    # Якщо немає полігону або fallback - використовуємо bbox стінки
    if use_bbox_walls:
        # South edge (y ≈ min_y)
        south_verts = verts[np.abs(verts[:, 1] - min_y) < tol_y]
        if len(south_verts) > 0:
            south_verts = simplify_edge_vertices(south_verts, 0)  # Сортуємо по X
        else:
            # Якщо не знайдено, використовуємо найближчі вершини
            south_idx = np.argmin(verts[:, 1])
            south_verts = verts[[south_idx]]
        
        # North edge (y ≈ max_y)
        north_verts = verts[np.abs(verts[:, 1] - max_y) < tol_y]
        if len(north_verts) > 0:
            north_verts = simplify_edge_vertices(north_verts, 0)  # Сортуємо по X
        else:
            # Якщо не знайдено, використовуємо найближчі вершини
            north_idx = np.argmax(verts[:, 1])
            north_verts = verts[[north_idx]]
        
        # West edge (x ≈ min_x)
        west_verts = verts[np.abs(verts[:, 0] - min_x) < tol_x]
        if len(west_verts) > 0:
            west_verts = simplify_edge_vertices(west_verts, 1)  # Сортуємо по Y
        else:
            # Якщо не знайдено, використовуємо найближчі вершини
            west_idx = np.argmin(verts[:, 0])
            west_verts = verts[[west_idx]]
        
        # East edge (x ≈ max_x)
        east_verts = verts[np.abs(verts[:, 0] - max_x) < tol_x]
        if len(east_verts) > 0:
            east_verts = simplify_edge_vertices(east_verts, 1)  # Сортуємо по Y
        else:
            # Якщо не знайдено, використовуємо найближчі вершини
            east_idx = np.argmax(verts[:, 0])
            east_verts = verts[[east_idx]]

    # South edge (y ≈ min_y, нижній край) - від min_x до max_x
    try:
        if len(south_verts) >= 2:
            for i in range(len(south_verts) - 1):
                add_wall(south_verts[i], south_verts[i + 1])
    except Exception as e:
        print(f"[WARN] Failed to create south wall: {e}")
    
    # North edge (y ≈ max_y, верхній край) - зворотний порядок для CCW
    try:
        if len(north_verts) >= 2:
            for i in range(len(north_verts) - 1):
                # Зворотний порядок для правильного CCW
                add_wall(north_verts[i + 1], north_verts[i])
    except Exception as e:
        print(f"[WARN] Failed to create north wall: {e}")
    
    # West edge (x ≈ min_x, лівий край) - від min_y до max_y
    try:
        if len(west_verts) >= 2:
            for i in range(len(west_verts) - 1):
                # Зворотний порядок для правильного CCW
                add_wall(west_verts[i + 1], west_verts[i])
    except Exception as e:
        print(f"[WARN] Failed to create west wall: {e}")
    
    # East edge (x ≈ max_x, правий край) - від min_y до max_y
    try:
        if len(east_verts) >= 2:
            for i in range(len(east_verts) - 1):
                add_wall(east_verts[i], east_verts[i + 1])
    except Exception as e:
        print(f"[WARN] Failed to create east wall: {e}")
    
    # Об'єднуємо всі частини
    all_meshes = [terrain_top, bottom_mesh]
    
    # Додаємо тільки валідні стіни
    for wall_mesh in side_meshes:
        if wall_mesh is not None and len(wall_mesh.vertices) > 0 and len(wall_mesh.faces) > 0:
            all_meshes.append(wall_mesh)
    
    # Перевірка: маємо хоча б верхню поверхню та дно
    if len(all_meshes) < 2:
        print("[WARN] Not enough mesh parts, returning top surface only")
        return terrain_top
    
    try:
        # Об'єднуємо всі mesh частини
        solid_terrain = trimesh.util.concatenate(all_meshes)
        
        if solid_terrain is None:
            raise ValueError("Failed to concatenate terrain meshes")
        
        # Перевірка після об'єднання
        if len(solid_terrain.vertices) == 0:
            raise ValueError("Solid terrain has no vertices after concatenation")
        if len(solid_terrain.faces) == 0:
            raise ValueError("Solid terrain has no faces after concatenation")
        
        # Об'єднуємо дублікати вершин на стиках (агресивне об'єднання для закриття щілин)
        # Використовуємо кілька проходів об'єднання для забезпечення watertight mesh
        try:
            # Перший прохід: стандартне об'єднання
            solid_terrain.merge_vertices(merge_tex=True, merge_norm=True)
            
            # Другий прохід: додаткове об'єднання для закриття щілин
            # Знаходимо дублікати вершин вручну з толерантністю 0.0001м (0.1мм)
            from scipy.spatial import cKDTree
            tree = cKDTree(solid_terrain.vertices)
            pairs = tree.query_pairs(r=0.0001, output_type='ndarray')
            
            if len(pairs) > 0:
                # Створюємо мапу для об'єднання вершин
                # Використовуємо union-find підхід для правильного об'єднання
                vertex_map = np.arange(len(solid_terrain.vertices))
                
                def find_root(idx):
                    """Знаходить корінь для union-find"""
                    while vertex_map[idx] != idx:
                        vertex_map[idx] = vertex_map[vertex_map[idx]]  # Path compression
                        idx = vertex_map[idx]
                    return idx
                
                def union(idx1, idx2):
                    """Об'єднує два індекси"""
                    root1 = find_root(idx1)
                    root2 = find_root(idx2)
                    if root1 != root2:
                        # Завжди об'єднуємо на менший індекс
                        if root1 < root2:
                            vertex_map[root2] = root1
                        else:
                            vertex_map[root1] = root2
                
                # Об'єднуємо всі пари близьких вершин
                for pair in pairs:
                    idx1, idx2 = int(pair[0]), int(pair[1])
                    if idx1 < len(vertex_map) and idx2 < len(vertex_map):
                        union(idx1, idx2)
                
                # Нормалізуємо мапу (всі вказують на корінь)
                for i in range(len(vertex_map)):
                    vertex_map[i] = find_root(i)
                
                # Застосовуємо мапу до граней
                solid_terrain.faces = vertex_map[solid_terrain.faces]
                
                # Видаляємо невикористані вершини
                solid_terrain.remove_unreferenced_vertices()
                
                # Третій прохід: фінальне об'єднання для забезпечення чистоти
                solid_terrain.merge_vertices(merge_tex=True, merge_norm=True)
        except Exception as e:
            print(f"[WARN] Failed to merge vertices: {e}")
            # Fallback до стандартного об'єднання
            try:
                solid_terrain.merge_vertices(merge_tex=True, merge_norm=True)
            except:
                pass
        
        # Видаляємо дегенеровані грані
        try:
            solid_terrain.remove_duplicate_faces()
            solid_terrain.remove_unreferenced_vertices()
        except Exception as e:
            print(f"[WARN] Failed to clean solid terrain: {e}")
        
        # Перевірка після очищення
        if len(solid_terrain.faces) == 0:
            raise ValueError("Solid terrain has no faces after cleaning")
        
        # Виправлення нормалей для правильного відображення
        try:
            solid_terrain.fix_normals()
        except Exception as e:
            print(f"[WARN] Failed to fix normals: {e}")
        
        # IMPORTANT: avoid aggressive hole filling/repair for zone tiles.
        # These operations can invent large "sheet" triangles (the mysterious walls you saw).
        # For stitched hex tiles we prefer a clean, predictable mesh (bottom + explicit walls).
        if not solid_terrain.is_watertight:
            print("[WARN] Solid terrain is not watertight; skipping fill_holes/repair to avoid phantom walls.")
        
        # Фінальна валідація
        if len(solid_terrain.vertices) == 0 or len(solid_terrain.faces) == 0:
            raise ValueError("Solid terrain mesh is empty after processing")
        
        # Перевірка на дегенеровані трикутники (дуже малі площі)
        try:
            # Обчислюємо площі трикутників вручну
            vertices = solid_terrain.vertices
            faces = solid_terrain.faces
            if len(faces) > 0 and len(vertices) > 0:
                areas = []
                for face in faces:
                    if len(face) == 3:
                        v0, v1, v2 = vertices[face[0]], vertices[face[1]], vertices[face[2]]
                        # Площа трикутника = 0.5 * |(v1-v0) x (v2-v0)|
                        area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
                        areas.append(area)
                
                if len(areas) > 0:
                    areas = np.array(areas)
                    min_area = np.min(areas)
                    if min_area < 1e-12:
                        print(f"[WARN] Degenerate triangles detected (min_area={min_area}), removing...")
                        # Видаляємо трикутники з дуже малою площею
                        valid_faces = areas > 1e-12
                        if np.any(valid_faces) and np.sum(valid_faces) < len(faces):
                            solid_terrain.update_faces(valid_faces)
                            solid_terrain.remove_unreferenced_vertices()
        except Exception as e:
            print(f"[WARN] Failed to check for degenerate triangles: {e}")
        
        return solid_terrain
        
    except Exception as e:
        print(f"[WARN] Failed to create solid terrain: {e}")
        print("[WARN] Returning top surface only")
        # Повертаємо хоча б верхню поверхню
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

