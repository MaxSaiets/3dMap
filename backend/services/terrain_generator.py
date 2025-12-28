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
    # Виконуємо ДО flatten під будівлями, щоб flatten працював на стабільному heightfield.
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
            Z = depress_heightfield_under_polygons(
                X=X,  # Локальні координати (центровані)
                Y=Y,  # Локальні координати (центровані)
                Z=Z,
                geometries=water_geometries_local,  # Центровані геометрії води
                depth=float(water_depth_m),
                quantile=0.1,  # Використовуємо нижчий quantile для глибшого depression
            )
            print(f"[INFO] Water depression вирізано в рельєфі: depth={water_depth_m:.3f}м, quantile=0.1")
    except Exception as e:
        print(f"[WARN] water depression failed: {e}")
        Z_original_before_water = None
    
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
    # Також перевіряємо на екстремальні значення
    Z = np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)
    
    # Додаткова перевірка: якщо всі значення однакові або дуже малі, додаємо мінімальну варіацію
    z_range = float(np.max(Z) - np.min(Z))
    if z_range < 1e-6:
        # Дуже плоский рельєф - додаємо помітну варіацію для кращої видимості
        # Використовуємо детермінований генератор для стабільності
        rng = np.random.RandomState(42)
        # Збільшуємо амплітуду шуму для кращої видимості
        noise_amplitude = max(0.1, z_scale * 0.1)  # Адаптивна амплітуда залежно від z_scale
        noise = rng.uniform(-noise_amplitude, noise_amplitude, Z.shape)
        Z = Z + noise
        # Перевірка після додавання шуму
        Z = np.clip(Z, -1e6, 1e6)  # Обмежуємо екстремальні значення
    elif z_range < 0.5:
        # Рельєф занадто плоский - підсилюємо контраст
        z_mean = float(np.mean(Z))
        z_contrast = 2.0  # Множник контрасту
        Z = (Z - z_mean) * z_contrast + z_mean

    # Фінальне згладжування для уникнення фасетованого вигляду
    # Додаємо потужне згладжування навіть після всіх модифікацій для плавного рельєфу
    try:
        from scipy.ndimage import gaussian_filter, uniform_filter
        # Потужніше фінальне згладжування (1.0-2.0 sigma) для максимальної плавності
        # Адаптуємо до resolution: для нижчої resolution - більше згладжування
        final_smooth = max(1.0, min(2.0, 250.0 / max(resolution, 100.0)))
        
        # Комбіноване згладжування: Gaussian + Uniform для кращого результату
        # Gaussian filter для загального згладжування
        Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth, mode="reflect")
        # Друге згладжування для додаткової плавності
        Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.7, mode="reflect")
        # Третє легке згладжування для фінальної поліровки
        Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.4, mode="reflect")
        
        # Додаткове edge-preserving smoothing для збереження важливих деталей
        # Використовуємо більш складний фільтр, який зберігає різкі переходи
        try:
            # Bilateral-like effect: згладжуємо, але зберігаємо контраст
            Z_smooth = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.3, mode="reflect")
            # Змішуємо оригінал зі згладженим (85% згладженого, 15% оригіналу) для кращої плавності
            Z = Z * 0.85 + Z_smooth * 0.15
        except Exception:
            pass
        
        # Фінальне дуже легке згладжування для ідеальної плавності
        try:
            Z = gaussian_filter(Z.astype(float, copy=False), sigma=final_smooth * 0.2, mode="reflect")
        except Exception:
            pass
    except Exception:
        pass
    
    # Підсилення контрасту рельєфу для кращої видимості
    try:
        z_range = float(np.max(Z) - np.min(Z))
        if z_range > 0.01:  # Якщо є хоча б якась варіація
            z_mean = float(np.mean(Z))
            # Підсилюємо контраст на 50% для кращої видимості
            contrast_factor = 1.5
            Z = (Z - z_mean) * contrast_factor + z_mean
            
            # Додаткове покращення: adaptive contrast enhancement
            # Підсилюємо контраст більше в областях з великими перепадами
            try:
                from scipy.ndimage import gaussian_gradient_magnitude
                # Обчислюємо градієнт висот
                gradient = gaussian_gradient_magnitude(Z, sigma=1.0)
                # Нормалізуємо градієнт
                gradient_norm = gradient / (np.max(gradient) + 1e-10)
                # Підсилюємо контраст більше там, де є круті схили
                adaptive_boost = 1.0 + gradient_norm * 0.3  # До 30% додаткового підсилення
                Z = (Z - z_mean) * adaptive_boost + z_mean
            except Exception:
                pass
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
    try:
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
    
    # Додаткове згладжування вершин для ще плавнішого рельєфу
    # Використовуємо простіший підхід: згладжуємо Z координати вершин на основі сітки
    try:
        # Оскільки ми маємо регулярну сітку, можемо згладжувати Z координати напряму
        # Беремо Z координати з вершин та застосовуємо легке згладжування
        # ВИПРАВЛЕННЯ: використовуємо res_y, res_x для reshape
        vertices_2d = terrain_top.vertices.reshape(res_y, res_x, 3)
        z_coords = vertices_2d[:, :, 2]
        
        # Застосовуємо легке згладжування до Z координат
        from scipy.ndimage import gaussian_filter
        z_smoothed = gaussian_filter(z_coords, sigma=0.5, mode="reflect")
        
        # Оновлюємо Z координати вершин
        vertices_2d[:, :, 2] = z_smoothed
        terrain_top.vertices = vertices_2d.reshape(-1, 3)
        
        # Перераховуємо нормалі після зміни вершин
        terrain_top.fix_normals()
    except Exception as e:
        print(f"[WARN] Vertex smoothing failed: {e}")
    
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
                # Для quantile=0.0 використовуємо мінімум, для інших - quantile
                if float(quantile) <= 0.0:
                    surface = float(np.min(h))  # Мінімальна висота для найглибшого depression
                else:
                    surface = float(np.quantile(h, float(quantile)))
                Z_out[mask] = surface - depth
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

    Z_rel = np.where(np.isnan(Z_rel), 0.0, Z_rel)
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
    base_thickness: float = 5.0
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
    bottom_mesh = trimesh.Trimesh(vertices=bottom_vertices, faces=bottom_faces, process=True)
    
    # Створюємо стіни по всіх 4 краях (щоб меш був watertight)
    # Використовуємо межі для знаходження граничних вершин (працює з будь-яким mesh, включно з subdivision)
    side_meshes = []
    verts = terrain_top.vertices
    
    # Толерантність для визначення граничних вершин (2% від розміру, але мінімум 0.1м)
    tol_x = max((max_x - min_x) * 0.02, 0.1)
    tol_y = max((max_y - min_y) * 0.02, 0.1)
    
    # Знаходимо граничні вершини за координатами
    # South edge (y ≈ min_y)
    south_verts = verts[np.abs(verts[:, 1] - min_y) < tol_y]
    if len(south_verts) > 0:
        south_verts = south_verts[south_verts[:, 0].argsort()]  # Сортуємо по X
    else:
        # Якщо не знайдено, використовуємо найближчі вершини
        south_idx = np.argmin(verts[:, 1])
        south_verts = verts[[south_idx]]
    
    # North edge (y ≈ max_y)
    north_verts = verts[np.abs(verts[:, 1] - max_y) < tol_y]
    if len(north_verts) > 0:
        north_verts = north_verts[north_verts[:, 0].argsort()]  # Сортуємо по X
    else:
        # Якщо не знайдено, використовуємо найближчі вершини
        north_idx = np.argmax(verts[:, 1])
        north_verts = verts[[north_idx]]
    
    # West edge (x ≈ min_x)
    west_verts = verts[np.abs(verts[:, 0] - min_x) < tol_x]
    if len(west_verts) > 0:
        west_verts = west_verts[west_verts[:, 1].argsort()]  # Сортуємо по Y
    else:
        # Якщо не знайдено, використовуємо найближчі вершини
        west_idx = np.argmin(verts[:, 0])
        west_verts = verts[[west_idx]]
    
    # East edge (x ≈ max_x)
    east_verts = verts[np.abs(verts[:, 0] - max_x) < tol_x]
    if len(east_verts) > 0:
        east_verts = east_verts[east_verts[:, 1].argsort()]  # Сортуємо по Y
    else:
        # Якщо не знайдено, використовуємо найближчі вершини
        east_idx = np.argmax(verts[:, 0])
        east_verts = verts[[east_idx]]

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
        
        # Вершини стіни: верхній край -> нижній край (CCW)
        wall_vertices = np.array([
            v1_top,      # 0: верхній лівий
            v2_top,      # 1: верхній правий
            v2_bottom,   # 2: нижній правий
            v1_bottom    # 3: нижній лівий
        ], dtype=np.float64)
        
        # Перевірка на дегенеровані трикутники (перевірка площі)
        # Трикутник 1: v1_top, v2_top, v2_bottom
        tri1_area = 0.5 * np.linalg.norm(np.cross(v2_top - v1_top, v2_bottom - v1_top))
        # Трикутник 2: v1_top, v2_bottom, v1_bottom
        tri2_area = 0.5 * np.linalg.norm(np.cross(v2_bottom - v1_top, v1_bottom - v1_top))
        
        if tri1_area < 1e-10 or tri2_area < 1e-10:
            # Дегенерований трикутник - пропускаємо
            return
        
        # Два трикутники з правильним порядком (CCW)
        wall_faces = np.array([
            [0, 1, 2],  # Трикутник 1
            [0, 2, 3]   # Трикутник 2
        ], dtype=np.int32)
        
        try:
            wall_mesh = trimesh.Trimesh(vertices=wall_vertices, faces=wall_faces, process=True)
            if wall_mesh is not None and len(wall_mesh.vertices) > 0 and len(wall_mesh.faces) > 0:
                side_meshes.append(wall_mesh)
        except Exception:
            # Пропускаємо невалідні стіни
            pass

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
        
        # Перевірка та виправлення watertight
        if not solid_terrain.is_watertight:
            try:
                # Спочатку намагаємося заповнити дірки
                solid_terrain.fill_holes()
                # Якщо все ще не watertight, використовуємо repair
                if not solid_terrain.is_watertight:
                    try:
                        # Виправляємо mesh за допомогою repair
                        trimesh.repair.fix_normals(solid_terrain)
                        trimesh.repair.fix_winding(solid_terrain)
                        # Повторне об'єднання вершин після repair
                        solid_terrain.merge_vertices(merge_tex=True, merge_norm=True)
                    except Exception as repair_e:
                        print(f"[WARN] Failed to repair mesh: {repair_e}")
            except Exception as e:
                print(f"[WARN] Failed to fill holes in solid terrain: {e}")
        
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

