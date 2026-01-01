# Повний аналіз логіки проекту 3D Map Generator

## Загальна архітектура

Проект складається з модульної системи обробки геопросторових даних для створення 3D моделей міст. Основні компоненти:

### 1. Система координат

**GlobalCenter** (`global_center.py`) - центральний компонент для синхронізації координат:
- Визначає єдину точку відліку (0,0) для всіх квадратів карти
- Перетворює координати між системами:
  - WGS84 (lat/lon) ↔ UTM (метри) ↔ Локальні координати (відносно центру)
- Забезпечує узгодженість між різними квадратами карти

**Ключові методи:**
- `to_utm()` - WGS84 → UTM
- `to_wgs84()` - UTM → WGS84  
- `to_local()` - UTM → локальні (відносно центру)
- `from_local()` - локальні → UTM

---

## 2. Створення мешів рельєфу (землі)

### 2.1 Основна функція: `create_terrain_mesh()` (`terrain_generator.py`)

**Етапи створення рельєфу:**

#### Крок 1: Підготовка сітки координат
```python
# Розрахунок роздільності з урахуванням aspect ratio
width_m = maxx - minx
height_m = maxy - miny
aspect_ratio = width_m / height_m

# Адаптивна роздільність для збереження пропорцій
if width_m > height_m:
    res_x = resolution
    res_y = int(resolution / aspect_ratio)
else:
    res_y = resolution
    res_x = int(resolution * aspect_ratio)
```

**Ключові особливості:**
- Використовує глобальний центр для синхронізації квадратів
- Створює регулярну сітку в локальних координатах (центровано)
- Для отримання висот конвертує локальні координати в UTM

#### Крок 2: Отримання даних висот

**`get_elevation_data()`** - отримує висоти через:
1. **Terrarium Tiles** (за замовчуванням) - безкоштовний сервіс з Amazon S3
2. **OpenTopoData** - безкоштовний API (може бути повільний)
3. **Локальний GeoTIFF** - найточніший варіант (якщо є DEM_PATH)
4. **Синтетичний рельєф** - fallback для тестування

**Процес:**
- Конвертація UTM координат → WGS84 (lat/lon) для API
- Семплінг висот для кожної точки сітки
- Нормалізація: `Z_rel = (Z_abs - elevation_ref_m) * z_scale`
- Підсилення контрасту для кращої видимості

#### Крок 3: Обробка heightfield (Z масив)

**Згладжування:**
```python
# Gaussian filter для прибирання шуму DEM
Z = gaussian_filter(Z, sigma=smoothing_sigma, mode="reflect")
```

**Terrain-first стабілізація:**

1. **Flatten під будівлями** (`flatten_heightfield_under_buildings`):
   - Вирівнює рельєф під будівлями до медіани висот
   - Забезпечує стабільну посадку будівель
   - Використовує `rasterio.features.rasterize` для швидкості

2. **Flatten під дорогами** (`flatten_heightfield_under_polygons`):
   - Вирівнює рельєф під дорогами до quantile (за замовчуванням 0.50)
   - Робить дороги гладкішими та читабельнішими

3. **Water depression** (`depress_heightfield_under_polygons`):
   - Вирізає западини в рельєфі під водою
   - Глибина: `surface - depth` (де surface = quantile висот)
   - Зберігає оригінальні висоти ПЕРЕД вирізанням для правильного розміщення води

#### Крок 4: Створення mesh вершин та граней

**Вершини:**
```python
vertices = np.column_stack([
    X.flatten(),  # X координата (локальні)
    Y.flatten(),  # Y координата (локальні)
    Z.flatten()   # Z координата (висота)
])
```

**Грані (трикутники):**
```python
# create_grid_faces() - створює трикутники для регулярної сітки
# Кожна клітинка сітки розбивається на 2 трикутники:
# T1: top_left → bottom_left → top_right
# T2: top_right → bottom_left → bottom_right
```

#### Крок 5: Створення твердотільного рельєфу

**`create_solid_terrain()`** - створює watertight mesh:

1. **Верхня поверхня** - вже створена з heightfield
2. **Дно** - плоска поверхня на мінімальній висоті - `base_thickness`
3. **Стіни** - по всіх 4 краях для закриття mesh

**Процес:**
- Знаходить граничні вершини за координатами
- Створює стіни між верхніми та нижніми вершинами
- Об'єднує всі частини через `trimesh.util.concatenate()`
- Агресивне об'єднання вершин для забезпечення watertight

**Subdivision** (опціонально):
- Поділяє кожен трикутник на 4 менші для плавнішого mesh
- Згладжує нові вершини для кращої якості

---

## 3. Створення мешів води

### 3.1 Water Depression (вирізання в рельєфі)

**`depress_heightfield_under_polygons()`** (`terrain_generator.py`):
- Вирізає западини в heightfield під водними об'єктами
- Глибина: `surface - depth` (де surface = quantile висот, за замовчуванням 0.1)
- Виконується ПЕРЕД створенням terrain mesh

**Перетворення координат:**
- Water geometries з pbf_loader приходять в UTM
- Конвертуються в локальні координати (відносно глобального центру)
- Це забезпечує ідеальне накладання без floating point помилок

### 3.2 Water Surface Mesh

**`process_water_surface()`** (`water_processor.py`) - створює тонку поверхню води:

#### Крок 1: Перетворення координат
```python
# UTM → локальні координати (якщо використовується глобальний центр)
gdf_water_local = transform(to_local_transform, gdf_water)
```

#### Крок 2: Обрізання по межах рельєфу
```python
# Кліп по bbox рельєфу (щоб вода не з'являлась де не треба)
clip_box = box(min_x, min_y, max_x, max_y)
geom = geom.intersection(clip_box)
```

#### Крок 3: Створення mesh для кожного полігону
```python
# Екструзія полігону на товщину води
mesh = trimesh.creation.extrude_polygon(poly, height=thickness_m)
```

#### Крок 4: Розміщення на рельєфі

**КРИТИЧНА ЛОГІКА:**

1. **Отримання висот:**
   - Використовує `original_heights_provider` (оригінальні висоти ДО depression)
   - Отримує `depressed_ground` (висоти ПІСЛЯ depression)

2. **Розрахунок рівня води:**
   ```python
   # Вода має бути на рівні мінімального оригінального рельєфу (русло річки)
   min_original_ground = np.min(original_ground)
   base_water_level = min_original_ground - 0.02  # 2см нижче для безпеки
   
   # Рівень поверхні води для кожної точки
   surface_levels = np.minimum(
       depressed_ground + water_protrusion_m,  # Дно depression + protrusion
       original_ground - 0.02  # Але не вище оригінального рельєфу
   )
   ```

3. **Розміщення вершин:**
   ```python
   # Верхня поверхня (old_z == thickness_m): surface_levels
   # Нижня поверхня (old_z == 0): surface_levels - thickness
   # Проміжні: інтерполяція
   v[:, 2] = np.where(
       is_top_surface,
       surface_levels,  # Верхня поверхня = рівень води
       np.where(
           is_bottom_surface,
           surface_levels - thickness,  # Нижня поверхня = дно води
           surface_levels - (thickness - old_z)  # Проміжні: інтерполяція
       )
   )
   ```

**ВАЖЛИВО:**
- Вода НЕ використовує subdivision (створює нерегулярну сітку)
- Використовує тільки вершини mesh для семплінгу (регулярна сітка)
- Застосовує синій колір: RGB(0, 100, 255)

### 3.3 Water Depression Mesh (для булевого віднімання)

**`create_water_depression()`** (`water_processor.py`):
- Створює западину для булевого віднімання з бази
- Екструдує полігон на глибину `depth`
- Проектує на рельєф: `new_z = ground_z + old_z`

---

## 4. Створення мешів будівель

### 4.1 Основна функція: `process_buildings()` (`building_processor.py`)

**Етапи:**

#### Крок 1: Перетворення координат
- UTM → локальні координати (якщо використовується глобальний центр)

#### Крок 2: Визначення висоти будівлі
**`get_building_height()`**:
- З тегів OSM: `height`, `building:height`
- З рівнів: `building:levels` * 3.0м (за поверх)
- Додає `roof:height` або `roof:levels` * 1.5м
- Мінімальна висота: `min_height` (за замовчуванням 2.0м)

#### Крок 3: Розрахунок позиції на рельєфі

**Адаптивний семплінг рельєфу:**
```python
# Для малих будівель (< 100 м²): мінімальний семплінг
# Для середніх (100-1000 м²): середній семплінг (сітка 3x3)
# Для великих (> 1000 м²): щільний семплінг (сітка 5x5)
```

**Розрахунок translate_z:**
```python
# Отримуємо висоти рельєфу під будівлею
heights = ground_heights_for_geom(geom)

# Мінімальна висота рельєфу (рельєф вже вирівняний)
ground_min = np.min(heights)

# base_z - рівень підлоги будівлі
if embed_depth > 0:
    base_z = max(ground_min - embed_depth, ground_min - safety_margin)
else:
    base_z = ground_min + safety_margin

# translate_z - Z координата нижньої точки будівлі
translate_z = base_z - foundation_depth_eff
```

#### Крок 4: Екструзія полігону
```python
# Використовує trimesh.creation.extrude_polygon для надійної екструзії
mesh = trimesh.creation.extrude_polygon(geom, height=height)
mesh.apply_translation([0, 0, translate_z])
```

#### Крок 5: Агресивна перевірка та виправлення

**Перевірка нижніх вершин:**
```python
# Використовує нижні 20% висоти будівлі
bottom_threshold = translate_z + (building_height * 0.2)
is_bottom = vertices[:, 2] <= bottom_threshold

# Отримує висоти рельєфу для нижніх вершин
ground_heights = terrain_provider.get_heights_for_points(bottom_vertices_xy)

# Піднімає будівлю, якщо потрібно
if current_bottom_z < required_bottom_z:
    elevation_adjustment = required_bottom_z - current_bottom_z
    vertices[:, 2] += elevation_adjustment
```

**Фінальна перевірка всіх вершин:**
```python
# Перевіряє ВСІ вершини, щоб переконатися, що нічого не під землею
vertices_below_ground = vertices[:, 2] < (all_ground_heights - 0.05)

# Агресивне виправлення: піднімає всі вершини, що під землею
if np.any(vertices_below_ground):
    min_required_z = np.max(all_ground_heights[vertices_below_ground]) + 0.1
    elevation_adjustment = min_required_z - current_min_z
    vertices[:, 2] += elevation_adjustment
```

---

## 5. Створення мешів доріг

### 5.1 Основна функція: `process_roads()` (`road_processor.py`)

**Етапи:**

#### Крок 1: Створення полігонів доріг
**`build_road_polygons()`**:
- Буферизує кожну дорогу на половину ширини
- Ширина залежить від типу: motorway=12м, primary=8м, residential=4м тощо
- Об'єднує всі полігони через `unary_union`

#### Крок 2: Визначення мостів
**`detect_bridges()`**:
- Перевіряє тег `bridge=yes` в OSM
- Перевіряє перетин з водними об'єктами
- Визначає висоту моста за типом: suspension=5м, arch=4м, beam=3м

#### Крок 3: Екструзія та розміщення

**Для звичайних доріг:**
```python
# Адаптивний road_embed на крутих схилах
slope = max_ground_z - min_ground_z
if slope > road_embed * 2.0:
    adaptive_embed = road_embed * (1.0 - min(0.5, (slope - road_embed * 2.0) / (slope + 1.0)))

# Розміщення з мінімальною висотою над рельєфом
road_z = ground_z_values + old_z - adaptive_embed
min_road_z = ground_z_values + min_height_above_ground
vertices[:, 2] = np.maximum(road_z, min_road_z)
```

**Для мостів:**
```python
# Розраховує висоту води під мостом
water_levels_per_point = original_ground_z - 0.2
median_water_level = np.median(water_levels_per_point)

# Мінімальний зазор над водою
min_clearance_above_water = max(3.0, bridge_height_offset)

# Базова висота моста
bridge_base_z_per_point = water_levels_per_point + min_clearance_above_water
bridge_base_z_per_point = np.maximum(
    bridge_base_z_per_point,
    ground_z_values + bridge_height_offset
)

# Використовує медіанне значення для стабільності
bridge_base_z = np.median(bridge_base_z_per_point)
vertices[:, 2] = bridge_base_z + old_z
```

#### Крок 4: Створення опор для мостів
**`create_bridge_supports()`**:
- Розміщує опори по краях моста + центральні для довгих мостів
- Опори йдуть від моста до землі/води
- Необхідні для стабільності при 3D друку

---

## 6. Створення мешів зелених зон

### 6.1 `process_green_areas()` (`green_processor.py`)

**Процес:**
1. Перетворення координат: UTM → локальні
2. Обрізання по межах рельєфу
3. Екструзія полігону на висоту `height_m`
4. Проектування на рельєф: `new_z = ground_z + old_z - embed_m`
5. Застосування зеленого кольору: RGB(90, 140, 80)

---

## 7. Створення мешів POI

### 7.1 `process_pois()` (`poi_processor.py`)

**Процес:**
1. Обмеження кількості: максимум 600 POI
2. Перетворення координат: UTM → локальні
3. Створення маленьких box маркерів для кожної точки
4. Розміщення на рельєфі: `z_center = ground + (height_m / 2.0) - embed_m`
5. Застосування жовтого кольору: RGB(220, 180, 60)

---

## 8. Експорт моделей

### 8.1 `export_scene()` (`model_exporter.py`)

**Підтримувані формати:**
- **3MF** - з підтримкою мультиколірного друку
- **STL** - для сумісності

**Етапи експорту:**

1. **Підготовка мешів:**
   - Додає базу/рельєф
   - Додає дороги
   - Додає будівлі (окремо для 3MF, об'єднано для STL)
   - Додає воду (синій колір)
   - Додає парки (зелений колір)
   - Додає POI (жовтий колір)

2. **Трансформації:**
   ```python
   # 1. Центрування за centroid
   center = combined.centroid
   t0 = translation_matrix(-center)
   
   # 2. Масштабування XY до model_size_mm
   scale_factor = model_size_mm / avg_xy_dimension
   s = scale_matrix(scale_factor)
   
   # 3. Центрування за bounds + підняття minZ до 0
   t_center = translation_matrix(-final_center_from_bounds)
   t_minz = translation_matrix([0, 0, -min_z])
   t_xy = translation_matrix([-final_centroid_before[0], -final_centroid_before[1], 0.0])
   ```

3. **Застосування кольорів:**
   - Base: RGB(250, 250, 250) - білий
   - Roads: RGB(30, 30, 30) - темно-сірий
   - Buildings: RGB(180, 180, 180) - сірий
   - Water: RGB(0, 100, 255) - синій
   - Parks: RGB(90, 140, 80) - зелений
   - POI: RGB(220, 180, 60) - жовтий

4. **Експорт:**
   - 3MF: `scene.export(filename, file_type="3mf")`
   - STL: `combined.export(filename, file_type="stl")`

---

## 9. TerrainProvider - інтерполяція висот

### 9.1 Клас `TerrainProvider` (`terrain_provider.py`)

**Призначення:**
- Надає інтерполяцію висот рельєфу для будь-якої точки (X, Y)
- Використовується для "драпірування" об'єктів на рельєф

**Ключова особливість:**
```python
def _heights_on_terrain_triangles(self, xs, ys):
    """
    Інтерполяція висоти, яка ПОВНІСТЮ збігається з трикутниками terrain mesh.
    
    Terrain mesh розбиває кожну клітинку на два трикутники:
    T1: top_left → bottom_left → top_right (dx + dy <= 1)
    T2: top_right → bottom_left → bottom_right (dx + dy > 1)
    """
    # Визначає клітинку
    j = searchsorted(x_axis, xs) - 1
    i = searchsorted(y_axis, ys) - 1
    
    # Нормалізовані координати в межах клітинки
    dx = (xs - x0) / (x1 - x0)
    dy = (ys - y0) / (y1 - y0)
    
    # Визначає трикутник
    mask = (dx + dy) <= 1.0
    
    # Інтерполяція для T1 та T2
    z[mask] = z00 * (1.0 - dx - dy) + z10 * dx + z01 * dy
    z[~mask] = z11 * (dx + dy - 1.0) + z10 * (1.0 - dy) + z01 * (1.0 - dx)
```

**Це прибирає ефект "дороги в текстурі / в повітрі", який з'являється, коли draping робиться білінійною інтерполяцією, а рельєф — трикутниками.**

---

## 10. Ключові технічні рішення

### 10.1 Terrain-first підхід

**Проблема:** На шумному DEM або крутих схилах будівлі/дороги часто стають "криво": частина основи над землею, частина під землею.

**Рішення:**
1. Вирівнюємо (flatten) рельєф під будівлями/дорогами до стабільної висоти
2. Потім розміщуємо об'єкти на вирівняному рельєфі
3. Це робить посадку друк-стабільною

### 10.2 Water depression в рельєфі

**Проблема:** Якщо створювати воду як окремий об'єкт, вона може накладатися на все в preview.

**Рішення:**
1. Вирізаємо depression безпосередньо в heightfield (Z масив)
2. Створюємо тонку поверхню води на рівні оригінального рельєфу
3. Вода не виступає над берегами

### 10.3 Глобальний центр для синхронізації

**Проблема:** Різні квадрати карти мають різні точки відліку, що ускладнює об'єднання.

**Рішення:**
- Всі квадрати використовують єдиний глобальний центр (0,0)
- Координати конвертуються: UTM → локальні (відносно центру)
- Це забезпечує ідеальне накладання без floating point помилок

### 10.4 Адаптивний семплінг

**Для будівель:**
- Малі (< 100 м²): мінімальний семплінг (контур + центр)
- Середні (100-1000 м²): середній семплінг (сітка 3x3)
- Великі (> 1000 м²): щільний семплінг (сітка 5x5)

**Для доріг:**
- Адаптивний road_embed на крутих схилах
- Зменшує embed пропорційно до нахилу

---

## 11. Потік даних

```
1. PBF Loader → GeoDataFrames (UTM координати)
   ↓
2. GlobalCenter → Перетворення UTM → Локальні координати
   ↓
3. Terrain Generator:
   - Створює сітку координат (локальні)
   - Отримує висоти через API (UTM → WGS84 → API → висоти)
   - Обробляє heightfield (flatten, depression)
   - Створює terrain mesh
   ↓
4. TerrainProvider → Інтерполяція висот для об'єктів
   ↓
5. Processors:
   - Buildings → Екструзія + розміщення на рельєфі
   - Roads → Буферизація + екструзія + розміщення
   - Water → Depression + поверхня води
   - Green → Екструзія + розміщення
   - POI → Box маркери + розміщення
   ↓
6. Model Exporter:
   - Об'єднує всі меші
   - Застосовує трансформації (center, scale, align)
   - Застосовує кольори
   - Експортує в 3MF/STL
```

---

## 12. Важливі деталі реалізації

### 12.1 Створення граней для регулярної сітки

```python
def create_grid_faces(rows, cols):
    """
    Кожна клітинка сітки розбивається на 2 трикутники:
    - T1: top_left → bottom_left → top_right (CCW)
    - T2: top_right → bottom_left → bottom_right (CCW)
    """
    for i in range(rows - 1):
        for j in range(cols - 1):
            top_left = i * cols + j
            top_right = i * cols + (j + 1)
            bottom_left = (i + 1) * cols + j
            bottom_right = (i + 1) * cols + (j + 1)
            
            faces.append([top_left, bottom_left, top_right])
            faces.append([top_right, bottom_left, bottom_right])
```

### 12.2 Watertight mesh для рельєфу

```python
# Створює дно
bottom_vertices = [[min_x, min_y, min_z], ...]
bottom_faces = [[0, 1, 2], [0, 2, 3]]

# Створює стіни по краях
# Знаходить граничні вершини за координатами
# Створює стіни між верхніми та нижніми вершинами

# Об'єднує всі частини
solid_terrain = concatenate([terrain_top, bottom_mesh, side_meshes])

# Агресивне об'єднання вершин для забезпечення watertight
merge_vertices(merge_tex=True, merge_norm=True)
```

### 12.3 Обробка MultiPolygon

**Для будівель:**
- Кожен полігон обробляється окремо
- Кожен полігон має свій `translate_z` (розраховується окремо)

**Для води:**
- Кожен полігон обробляється окремо
- Всі меші об'єднуються в один

---

## 13. Оптимізації та покращення

### 13.1 Продуктивність

- **Rasterio rasterize** для швидкого flatten (замість point-in-polygon)
- **Кешування висот** в elevation API
- **Батчинг запитів** до OpenTopoData (по 100 точок)
- **Обмеження кількості POI** (максимум 600)

### 13.2 Якість mesh

- **Subdivision** для плавнішого рельєфу (опціонально)
- **Згладжування вершин** після subdivision
- **Виправлення нормалей** для правильного освітлення
- **Заповнення дірок** для watertight mesh

### 13.3 Стабільність

- **Агресивна перевірка** нижніх вершин будівель
- **Адаптивний embed** на крутих схилах
- **Safety margins** для захисту від "під землею"
- **Fallback механізми** на всіх етапах

---

## Висновок

Проект реалізує складну систему генерації 3D моделей міст з:
- Реалістичним рельєфом (з DEM даних)
- Правильно розміщеними будівлями, дорогами, водою
- Підтримкою мультиколірного друку
- Стабільною посадкою об'єктів на рельєф
- Оптимізаціями для продуктивності та якості

Ключові інновації:
1. **Terrain-first підхід** - вирівнювання рельєфу під об'єктами
2. **Water depression** - вирізання води безпосередньо в рельєф
3. **Глобальний центр** - синхронізація квадратів карти
4. **Точна інтерполяція** - збігається з трикутниками mesh

