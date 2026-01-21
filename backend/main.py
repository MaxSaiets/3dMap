"""
FastAPI backend для 3D Map Generator
Реалізує логіку генерації 3D моделей з OpenStreetMap даних
"""
import warnings
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Tuple
import os
import uuid
from pathlib import Path
import trimesh

# Придушення deprecation warnings від pandas/geopandas
warnings.filterwarnings('ignore', category=DeprecationWarning, module='pandas')
warnings.filterwarnings('ignore', category=DeprecationWarning, module='geopandas')

from services.data_loader import fetch_city_data
from services.road_processor import process_roads, build_road_polygons
from services.terrain_generator import create_terrain_mesh
from services.building_processor import process_buildings
from services.water_processor import process_water
from services.extras_loader import fetch_extras
from services.green_processor import process_green_areas
from services.poi_processor import process_pois
from services.model_exporter import export_scene, export_preview_parts_stl
from services.generation_task import GenerationTask
from services.mesh_quality import improve_mesh_for_3d_printing, validate_mesh_for_3d_printing
from services.global_center import get_or_create_global_center, set_global_center, get_global_center, GlobalCenter
from services.hexagonal_grid import generate_hexagonal_grid, hexagons_to_geojson, validate_hexagonal_grid, calculate_grid_center_from_geojson
from services.elevation_sync import calculate_global_elevation_reference, calculate_optimal_base_thickness
from shapely.ops import transform

app = FastAPI(title="3D Map Generator API", version="1.0.0")



# CORS налаштування для інтеграції з frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Зберігання задач генерації
tasks: dict[str, GenerationTask] = {}
# Зберігання зв'язків між множинними задачами (task_id -> list of task_ids)
multiple_tasks_map: dict[str, list[str]] = {}

# Директорія для збереження згенерованих файлів
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


class GenerationRequest(BaseModel):
    """Запит на генерацію 3D моделі"""
    model_config = ConfigDict(protected_namespaces=())
    
    north: float
    south: float
    east: float
    west: float
    # Параметри генерації
    road_width_multiplier: float = 1.0
    # Print-aware параметри (в МІЛІМЕТРАХ на фінальній моделі)
    road_height_mm: float = Field(default=0.5, ge=0.2, le=5.0)
    road_embed_mm: float = Field(default=0.3, ge=0.0, le=2.0)
    building_min_height: float = 2.0
    building_height_multiplier: float = 1.0
    building_foundation_mm: float = Field(default=0.6, ge=0.1, le=5.0)
    building_embed_mm: float = Field(default=0.2, ge=0.0, le=2.0)
    # Максимальна глибина фундаменту (мм НА ФІНАЛЬНІЙ МОДЕЛІ).
    # Це "запобіжник" для крутих схилів/шумного DEM: щоб будівлі не йшли надто глибоко під землю.
    building_max_foundation_mm: float = Field(default=2.5, ge=0.2, le=10.0)
    # Extra detail layers
    include_parks: bool = True
    parks_height_mm: float = Field(default=0.6, ge=0.1, le=5.0)
    parks_embed_mm: float = Field(default=0.2, ge=0.0, le=2.0)
    include_pois: bool = False
    poi_size_mm: float = Field(default=0.6, ge=0.2, le=3.0)
    poi_height_mm: float = Field(default=0.8, ge=0.2, le=5.0)
    poi_embed_mm: float = Field(default=0.2, ge=0.0, le=2.0)
    water_depth: float = 2.0  # мм
    terrain_enabled: bool = True
    terrain_z_scale: float = 3.0  # Збільшено для кращої видимості рельєфу
    # Тонка основа для друку: за замовчуванням 1мм (користувач може змінити).
    terrain_base_thickness_mm: float = Field(default=1.0, ge=0.5, le=20.0)  # Мінімум 0.5мм для синхронізованих зон
    # Деталізація рельєфу
    # - terrain_resolution: кількість точок по осі (mesh деталь). Вища = детальніше, повільніше.
    terrain_resolution: int = Field(default=350, ge=80, le=600)  # Висока деталізація для максимально плавного рельєфу
    # Subdivision: додаткова деталізація mesh після створення (для ще плавнішого рельєфу)
    terrain_subdivide: bool = Field(default=True, description="Застосувати subdivision для плавнішого mesh")
    terrain_subdivide_levels: int = Field(default=1, ge=0, le=2, description="Рівні subdivision (0-2, більше = плавніше але повільніше)")
    # - terrarium_zoom: зум DEM tiles (Terrarium). Вища = детальніше, але більше тайлів.
    terrarium_zoom: int = Field(default=15, ge=10, le=16)
    # Згладжування рельєфу (sigma в клітинках heightfield). 0 = без згладжування.
    # Допомагає прибрати "грубі грані/шум" на DEM, особливо при високому zoom.
    terrain_smoothing_sigma: float = Field(default=2.0, ge=0.0, le=5.0)  # Оптимальне згладжування для ідеального рельєфу
    # Terrain-first стабілізація: вирівняти (flatten) рельєф під будівлями, щоб будівлі не були "криво" на схилах/шумному DEM.
    flatten_buildings_on_terrain: bool = True
    # Terrain-first стабілізація для доріг: робить дороги більш читабельними на малому масштабі і прибирає "шипи" на бокових стінках.
    flatten_roads_on_terrain: bool = True
    export_format: str = "3mf"  # "stl" або "3mf"
    model_size_mm: float = 80.0  # Розмір моделі в міліметрах (за замовчуванням 80мм = 8см)
    # Контекст навколо зони (в метрах): завантажуємо OSM/Extras з більшим bbox,
    # але фінальні меші все одно обрізаємо по полігону зони.
    # Це потрібно, щоб коректно визначати мости/перетини біля краю зони.
    context_padding_m: float = Field(default=400.0, ge=0.0, le=5000.0)
    # Тестування: генерувати тільки рельєф без будівель/доріг/води (за замовчуванням False - повна модель)
    terrain_only: bool = False  # Тестовий режим вимкнено за замовчуванням
    # Синхронізація висот між зонами (для гексагональної сітки)
    elevation_ref_m: Optional[float] = None  # Глобальна базова висота (метри над рівнем моря)
    baseline_offset_m: float = 0.0  # Зміщення baseline (метри)
    # Preserve global XY coordinates (do NOT center per tile) for perfect stitching across zones/sessions.
    preserve_global_xy: bool = False


class GenerationResponse(BaseModel):
    """Відповідь з ID задачі"""
    task_id: str
    status: str
    message: Optional[str] = None
    all_task_ids: Optional[List[str]] = None  # Для множинних зон


@app.get("/")
async def root():
    return {"message": "3D Map Generator API", "version": "1.0.0"}


@app.post("/api/generate", response_model=GenerationResponse)
async def generate_model(request: GenerationRequest, background_tasks: BackgroundTasks):
    """
    Створює задачу генерації 3D моделі
    """
    try:
        print(f"[INFO] Отримано запит на генерацію: north={request.north}, south={request.south}, east={request.east}, west={request.west}")
        task_id = str(uuid.uuid4())
        task = GenerationTask(task_id=task_id, request=request)
        tasks[task_id] = task
        
        # Запускаємо генерацію в фоні
        background_tasks.add_task(generate_model_task, task_id, request)
        
        print(f"[INFO] Створено задачу {task_id} для генерації моделі")
        return GenerationResponse(task_id=task_id, status="processing", message="Задача створена")
    except Exception as e:
        print(f"[ERROR] Помилка створення задачі: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Помилка створення задачі: {str(e)}")


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    """
    Отримує статус задачі генерації або множинних задач
    """
    # Перевіряємо, чи це batch запит на множинні задачі (формат: batch_<uuid>)
    if task_id.startswith("batch_"):
        all_task_ids_list = multiple_tasks_map.get(task_id)
        if not all_task_ids_list:
            raise HTTPException(status_code=404, detail="Multiple tasks not found")
        
        # Повертаємо статус всіх задач
        tasks_status = []
        for tid in all_task_ids_list:
            if tid in tasks:
                t = tasks[tid]
                output_files = getattr(t, "output_files", {}) or {}
                tasks_status.append({
                    "task_id": tid,
                    "status": t.status,
                    "progress": t.progress,
                    "message": t.message,
                    "output_file": t.output_file,
                    "output_files": output_files,
                    "download_url": f"/api/download/{tid}" if t.status == "completed" else None,
                })
        
        return {
            "task_id": task_id,
            "status": "multiple",
            "tasks": tasks_status,
            "total": len(tasks_status),
            "completed": sum(1 for t in tasks_status if t["status"] == "completed"),
            "all_task_ids": all_task_ids_list
        }
    
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    output_files = getattr(task, "output_files", {}) or {}
    return {
        "task_id": task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "download_url": f"/api/download/{task_id}" if task.status == "completed" else None,
        # Додаткові URL-и (не ламають старий frontend, але корисні для preview/format fallback)
        "download_url_stl": f"/api/download/{task_id}?format=stl" if task.status == "completed" and ("stl" in output_files) else None,
        "download_url_3mf": f"/api/download/{task_id}?format=3mf" if task.status == "completed" and ("3mf" in output_files) else None,
        "preview_parts": {
            "base": f"/api/download/{task_id}?format=stl&part=base" if task.status == "completed" and ("base_stl" in output_files) else None,
            "roads": f"/api/download/{task_id}?format=stl&part=roads" if task.status == "completed" and ("roads_stl" in output_files) else None,
            "buildings": f"/api/download/{task_id}?format=stl&part=buildings" if task.status == "completed" and ("buildings_stl" in output_files) else None,
            "water": f"/api/download/{task_id}?format=stl&part=water" if task.status == "completed" and ("water_stl" in output_files) else None,
            "parks": f"/api/download/{task_id}?format=stl&part=parks" if task.status == "completed" and ("parks_stl" in output_files) else None,
            "poi": f"/api/download/{task_id}?format=stl&part=poi" if task.status == "completed" and ("poi_stl" in output_files) else None,
        },
    }


@app.get("/api/download/{task_id}")
async def download_model(
    task_id: str,
    format: Optional[str] = Query(default=None, description="Optional: stl або 3mf"),
    part: Optional[str] = Query(default=None, description="Optional preview part: base|roads|buildings|water"),
):
    """
    Завантажує згенерований файл
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    if task.status != "completed" or not task.output_file:
        raise HTTPException(status_code=400, detail="Model not ready")
    
    # Якщо запитали конкретний формат/частину — пробуємо віддати її (якщо існує)
    selected_path: Optional[str] = None
    if format or part:
        fmt = (format or "stl").lower().strip(".")
        if part:
            p = part.lower()
            key = f"{p}_{fmt}"
            selected_path = getattr(task, "output_files", {}).get(key)
            if not selected_path:
                raise HTTPException(status_code=404, detail=f"Requested part not available: {p} ({fmt})")
        else:
            selected_path = getattr(task, "output_files", {}).get(fmt)
            if not selected_path:
                raise HTTPException(status_code=404, detail=f"Requested format not available: {fmt}")
    else:
        selected_path = task.output_file

    # Перевіряємо існування файлу (з абсолютним шляхом)
    file_path = Path(selected_path)
    if not file_path.exists():
        # Спробуємо знайти файл відносно OUTPUT_DIR
        alt_path = OUTPUT_DIR / file_path.name
        if alt_path.exists():
            file_path = alt_path
        else:
            raise HTTPException(
                status_code=404, 
                detail=f"File not found: {selected_path} (also tried: {alt_path})"
            )

    # content-type залежно від розширення (для коректнішої детекції на фронті)
    ext = file_path.suffix.lower()
    if ext == ".3mf":
        media_type = "model/3mf"
    elif ext == ".stl":
        media_type = "model/stl"
    else:
        media_type = "application/octet-stream"

    return FileResponse(
        str(file_path),
        media_type=media_type,
        filename=file_path.name
    )


@app.post("/api/merge-zones")
async def merge_zones_endpoint(
    task_ids: List[str] = Query(..., description="Список task_id зон для об'єднання"),
    format: str = Query(default="3mf", description="Формат вихідного файлу (stl або 3mf)")
):
    """
    Об'єднує кілька зон в один файл для відображення разом.
    
    Args:
        task_ids: Список task_id зон для об'єднання
        format: Формат вихідного файлу (stl або 3mf)
    
    Returns:
        Об'єднаний файл моделі
    """
    if not task_ids or len(task_ids) == 0:
        raise HTTPException(status_code=400, detail="Не вказано task_ids для об'єднання")
    
    # Перевіряємо, чи всі задачі завершені
    completed_tasks = []
    for tid in task_ids:
        if tid not in tasks:
            raise HTTPException(status_code=404, detail=f"Task {tid} not found")
        task = tasks[tid]
        if task.status != "completed":
            raise HTTPException(status_code=400, detail=f"Task {tid} not completed yet")
        completed_tasks.append(task)
    
    # Завантажуємо всі меші
    all_meshes = []
    
    for task in completed_tasks:
        try:
            # Завантажуємо STL файл (він містить об'єднану модель)
            stl_file = task.output_file
            if stl_file and stl_file.endswith('.stl'):
                mesh = trimesh.load(stl_file)
                if mesh is not None:
                    all_meshes.append(mesh)
        except Exception as e:
            print(f"[WARN] Помилка завантаження мешу з {task.task_id}: {e}")
            continue
    
    if not all_meshes:
        raise HTTPException(status_code=400, detail="Не вдалося завантажити жодного мешу")
    
    # Об'єднуємо всі меші
    try:
        merged_mesh = trimesh.util.concatenate(all_meshes)
        if merged_mesh is None:
            raise HTTPException(status_code=500, detail="Не вдалося об'єднати меші")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Помилка об'єднання мешів: {str(e)}")
    
    # Зберігаємо об'єднаний файл
    # Зберігаємо об'єднаний файл
    merged_id = f"merged_{uuid.uuid4()}"
    if format.lower() == "3mf":
        output_file = OUTPUT_DIR / f"{merged_id}.3mf"
        merged_mesh.export(str(output_file), file_type="3mf")
    else:
        output_file = OUTPUT_DIR / f"{merged_id}.stl"
        merged_mesh.export(str(output_file), file_type="stl")
    
    return FileResponse(
        str(output_file),
        media_type="model/3mf" if format.lower() == "3mf" else "model/stl",
        filename=output_file.name
    )


@app.get("/api/test-model")
async def get_test_model():
    """
    Повертає тестову модель центру Києва (1км x 1км)
    Спочатку намагається повернути STL (надійніше), потім 3MF
    """
    # Спочатку перевіряємо STL (надійніше для завантаження)
    test_model_stl = OUTPUT_DIR / "test_model_kyiv.stl"
    if test_model_stl.exists():
        return FileResponse(
            test_model_stl,
            media_type="application/octet-stream",
            filename="test_model_kyiv.stl"
        )
    
    # Якщо STL немає, перевіряємо 3MF
    test_model_3mf = OUTPUT_DIR / "test_model_kyiv.3mf"
    if test_model_3mf.exists():
        return FileResponse(
            test_model_3mf,
            media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
            filename="test_model_kyiv.3mf"
        )
    
    raise HTTPException(
        status_code=404, 
        detail="Test model not found. Run generate_test_model.py first."
    )


@app.get("/api/test-model/manifest")
async def get_test_model_manifest():
    """
    Маніфест STL частин для кольорового прев'ю (base/roads/buildings/water/parks/poi)
    """
    parts = {}
    for p in ["base", "roads", "buildings", "water", "parks", "poi"]:
        fp = OUTPUT_DIR / f"test_model_kyiv_{p}.stl"
        if fp.exists():
            parts[p] = f"/api/test-model/part/{p}"
    if not parts:
        raise HTTPException(status_code=404, detail="No test-model parts found. Run generate_test_model.py first.")
    return {"parts": parts}


@app.get("/api/test-model/part/{part_name}")
async def get_test_model_part(part_name: str):
    p = part_name.lower()
    file_path = OUTPUT_DIR / f"test_model_kyiv_{p}.stl"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Test model part not found")
    return FileResponse(str(file_path), media_type="model/stl", filename=file_path.name)


@app.post("/api/global-center")
async def set_global_center_endpoint(center_lat: float = Query(...), center_lon: float = Query(...), utm_zone: Optional[int] = Query(None)):
    """
    Встановлює глобальний центр карти для синхронізації квадратів
    
    Args:
        center_lat: Широта глобального центру (WGS84)
        center_lon: Довгота глобального центру (WGS84)
        utm_zone: UTM зона (опціонально, визначається автоматично якщо не вказано)
    
    Returns:
        Інформація про встановлений центр
    """
    try:
        global_center = set_global_center(center_lat, center_lon, utm_zone)
        center_x_utm, center_y_utm = global_center.get_center_utm()
        return {
            "status": "success",
            "center": {
                "lat": center_lat,
                "lon": center_lon,
                "utm_zone": global_center.utm_zone,
                "utm_x": center_x_utm,
                "utm_y": center_y_utm,
            },
            "message": f"Глобальний центр встановлено: ({center_lat:.6f}, {center_lon:.6f})"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Помилка встановлення глобального центру: {str(e)}")


@app.get("/api/global-center")
async def get_global_center_endpoint():
    """
    Отримує поточний глобальний центр карти
    
    Returns:
        Інформація про поточний центр або null якщо не встановлено
    """
    global_center = get_global_center()
    if global_center is None:
        return {"status": "not_set", "center": None}
    
    center_x_utm, center_y_utm = global_center.get_center_utm()
    return {
        "status": "set",
        "center": {
            "lat": global_center.center_lat,
            "lon": global_center.center_lon,
            "utm_zone": global_center.utm_zone,
            "utm_x": center_x_utm,
            "utm_y": center_y_utm,
        }
    }


class HexagonalGridRequest(BaseModel):
    """Запит для генерації сітки (шестикутники або квадрати)"""
    north: float
    south: float
    east: float
    west: float
    hex_size_m: float = Field(default=400.0, ge=100.0, le=10000.0)  # 0.4 км за замовчуванням
    grid_type: str = Field(default="hexagonal", description="Тип сітки: 'hexagonal' (шестикутники) або 'square' (квадрати)")


class HexagonalGridResponse(BaseModel):
    """Відповідь з гексагональною сіткою"""
    geojson: dict
    hex_count: int
    is_valid: bool
    validation_errors: List[str] = []
    grid_center: Optional[dict] = None  # Центр сітки для синхронізації координат


@app.post("/api/hexagonal-grid", response_model=HexagonalGridResponse)
async def generate_hexagonal_grid_endpoint(request: HexagonalGridRequest):
    """
    Генерує гексагональну сітку для заданої області.
    Шестикутники мають розмір hex_size_m (за замовчуванням 0.5 км).
    КЕШУЄ сітку після першої генерації для швидшого доступу.
    """
    import hashlib
    import json
    
    try:
        # Створюємо хеш параметрів для ідентифікації сітки
        grid_type = request.grid_type.lower() if hasattr(request, 'grid_type') else 'hexagonal'
        cache_key = f"{request.north:.6f}_{request.south:.6f}_{request.east:.6f}_{request.west:.6f}_{request.hex_size_m:.1f}_{grid_type}"
        cache_hash = hashlib.md5(cache_key.encode()).hexdigest()
        
        # Шлях до кешу сіток
        cache_dir = Path("cache/grids")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"grid_{cache_hash}.json"
        
        # Перевіряємо чи є збережена сітка
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                    print(f"[INFO] Використовується збережена сітка з кешу: {cache_file.name}")
                    return HexagonalGridResponse(**cached_data)
            except Exception as e:
                print(f"[WARN] Помилка читання кешу сітки: {e}, генеруємо нову")
        
        print(f"[INFO] Генерація нової сітки: north={request.north}, south={request.south}, east={request.east}, west={request.west}, hex_size_m={request.hex_size_m}")
        
        # Перевірка валідності координат
        if request.north <= request.south or request.east <= request.west:
            raise ValueError(f"Невірні координати: north={request.north} <= south={request.south} або east={request.east} <= west={request.west}")
        
        # Конвертуємо lat/lon bbox в UTM для генерації сітки
        from services.crs_utils import bbox_latlon_to_utm
        bbox_utm = bbox_latlon_to_utm(
            request.north, request.south, request.east, request.west
        )
        bbox_meters = bbox_utm[:4]  # (minx, miny, maxx, maxy)
        to_wgs84 = bbox_utm[6]  # Функція для конвертації UTM -> WGS84 (індекс 6)
        
        # Генеруємо сітку (шестикутники або квадрати)
        if grid_type == 'square':
            from services.hexagonal_grid import generate_square_grid
            cells = generate_square_grid(bbox_meters, square_size_m=request.hex_size_m)
            print(f"[INFO] Згенеровано {len(cells)} квадратів")
        else:
            cells = generate_hexagonal_grid(bbox_meters, hex_size_m=request.hex_size_m)
            print(f"[INFO] Згенеровано {len(cells)} шестикутників")
        
        # Конвертуємо в GeoJSON з конвертацією координат UTM -> WGS84
        geojson = hexagons_to_geojson(cells, to_wgs84=to_wgs84)
        
        # Валідуємо сітку (тільки для шестикутників)
        is_valid = True
        errors = []
        if grid_type == 'hexagonal':
            is_valid, errors = validate_hexagonal_grid(cells)
            if errors:
                print(f"[WARN] Помилки валідації сітки: {errors}")
        
        # Обчислюємо центр сітки для синхронізації координат
        grid_center = None
        try:
            center_lat, center_lon = calculate_grid_center_from_geojson(geojson, to_wgs84=to_wgs84)
            grid_center = {
                "lat": center_lat,
                "lon": center_lon
            }
            print(f"[INFO] Центр сітки: lat={center_lat:.6f}, lon={center_lon:.6f}")
        except Exception as e:
            print(f"[WARN] Не вдалося обчислити центр сітки: {e}")
        
        response = HexagonalGridResponse(
            geojson=geojson,
            hex_count=len(cells),
            is_valid=is_valid,
            validation_errors=errors,
            grid_center=grid_center
        )
        
        # Зберігаємо сітку в кеш
        try:
            cache_data = {
                "geojson": response.geojson,
                "hex_count": response.hex_count,
                "is_valid": response.is_valid,
                "validation_errors": response.validation_errors,
                "grid_center": response.grid_center
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            print(f"[INFO] Сітка збережена в кеш: {cache_file.name}")
        except Exception as e:
            print(f"[WARN] Не вдалося зберегти сітку в кеш: {e}")
        
        return response
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] Помилка генерації сітки: {e}\n{error_trace}")
        raise HTTPException(status_code=500, detail=f"Помилка генерації сітки: {str(e)}")


class ZoneGenerationRequest(BaseModel):
    """Запит для генерації моделей для вибраних зон"""
    model_config = ConfigDict(protected_namespaces=())
    
    zones: List[dict]  # Список зон (GeoJSON features)
    # Hex grid parameters (used to reconstruct exact zone polygons in metric space for perfect stitching)
    hex_size_m: float = Field(default=400.0, ge=100.0, le=10000.0)
    # IMPORTANT: city/area bbox (WGS84) for a stable global reference across sessions.
    # If provided, global_center + DEM bbox + elevation_ref are computed/cached from this bbox,
    # so later "add more zones" runs stitch perfectly with earlier prints.
    north: Optional[float] = None
    south: Optional[float] = None
    east: Optional[float] = None
    west: Optional[float] = None
    # Всі інші параметри як у GenerationRequest
    model_size_mm: float = Field(default=80.0, ge=10.0, le=500.0)
    road_width_multiplier: float = Field(default=0.8, ge=0.1, le=5.0)
    road_height_mm: float = Field(default=0.5, ge=0.1, le=10.0)
    road_embed_mm: float = Field(default=0.3, ge=0.0, le=5.0)
    building_min_height: float = Field(default=5.0, ge=1.0, le=100.0)
    building_height_multiplier: float = Field(default=1.8, ge=0.1, le=10.0)
    building_foundation_mm: float = Field(default=0.6, ge=0.0, le=10.0)
    building_embed_mm: float = Field(default=0.2, ge=0.0, le=5.0)
    building_max_foundation_mm: float = Field(default=5.0, ge=0.0, le=20.0)
    water_depth: float = Field(default=2.0, ge=0.1, le=10.0)
    terrain_enabled: bool = True
    terrain_z_scale: float = Field(default=0.5, ge=0.1, le=10.0)
    terrain_base_thickness_mm: float = Field(default=2.0, ge=0.5, le=20.0)  # Мінімум 0.5мм для синхронізованих зон
    terrain_resolution: int = Field(default=180, ge=50, le=500)
    terrarium_zoom: int = Field(default=15, ge=10, le=18)
    terrain_smoothing_sigma: Optional[float] = Field(default=None, ge=0.0, le=5.0)
    terrain_subdivide: bool = False
    terrain_subdivide_levels: int = Field(default=1, ge=1, le=3)
    flatten_buildings_on_terrain: bool = True
    flatten_roads_on_terrain: bool = False
    export_format: str = Field(default="3mf", pattern="^(stl|3mf)$")
    context_padding_m: float = Field(default=400.0, ge=0.0, le=5000.0)
    # Fast mode for stitching diagnostics: generate only terrain (optionally with water depression)
    terrain_only: bool = False
    include_parks: bool = True
    include_pois: bool = False


@app.post("/api/generate-zones", response_model=GenerationResponse)
async def generate_zones_endpoint(request: ZoneGenerationRequest, background_tasks: BackgroundTasks):
    """
    Генерує 3D моделі для вибраних зон гексагональної сітки.
    Кожна зона генерується як окрема модель.
    ВАЖЛИВО: Всі зони використовують ОДИН глобальний центр для синхронізації координат.
    """
    if not request.zones or len(request.zones) == 0:
        raise HTTPException(status_code=400, detail="Не вибрано жодної зони")
    
    # КРИТИЧНО: Визначаємо глобальний центр для ВСІЄЇ сітки.
    # If client provides city bbox, use it for a stable reference; otherwise fallback to selected zones bbox.
    # Це забезпечує, що всі зони використовують одну точку відліку (0,0)
    # і ідеально підходять одна до одної
    print(f"[INFO] Визначення глобального центру для всієї сітки ({len(request.zones)} зон)...")
    
    grid_bbox = None
    # 1) Prefer explicit city bbox (stable across later zone additions)
    try:
        if request.north is not None and request.south is not None and request.east is not None and request.west is not None:
            if float(request.north) > float(request.south) and float(request.east) > float(request.west):
                grid_bbox = {
                    "north": float(request.north),
                    "south": float(request.south),
                    "east": float(request.east),
                    "west": float(request.west),
                }
    except Exception:
        grid_bbox = None

    # 2) Fallback: compute bbox from selected zones (old behavior)
    if grid_bbox is None:
        all_lons = []
        all_lats = []
        for zone in request.zones:
            geometry = zone.get('geometry', {})
            if geometry.get('type') != 'Polygon':
                continue
            coordinates = geometry.get('coordinates', [])
            if not coordinates or len(coordinates) == 0:
                continue
            all_coords = [coord for ring in coordinates for coord in ring]
            zone_lons = [coord[0] for coord in all_coords]
            zone_lats = [coord[1] for coord in all_coords]
            all_lons.extend(zone_lons)
            all_lats.extend(zone_lats)
        if len(all_lons) == 0 or len(all_lats) == 0:
            raise HTTPException(status_code=400, detail="Не вдалося визначити координати зон")
        grid_bbox = {
            'north': max(all_lats),
            'south': min(all_lats),
            'east': max(all_lons),
            'west': min(all_lons)
        }
    
    # Визначаємо центр всієї сітки
    grid_center_lat = (grid_bbox['north'] + grid_bbox['south']) / 2.0
    grid_center_lon = (grid_bbox['east'] + grid_bbox['west']) / 2.0
    
    print(f"[INFO] Глобальний центр сітки: lat={grid_center_lat:.6f}, lon={grid_center_lon:.6f}")
    print(f"[INFO] Bbox всієї сітки: north={grid_bbox['north']:.6f}, south={grid_bbox['south']:.6f}, east={grid_bbox['east']:.6f}, west={grid_bbox['west']:.6f}")
    
    # Cache global city reference so future "add more zones" uses the same values.
    grid_bbox_latlon = (grid_bbox['north'], grid_bbox['south'], grid_bbox['east'], grid_bbox['west'])
    import hashlib, json
    cache_dir = Path("cache/cities")
    cache_dir.mkdir(parents=True, exist_ok=True)
    # cache version bump: elevation baseline logic changed (needs refresh)
    city_key = f"v4_{grid_bbox_latlon[0]:.6f}_{grid_bbox_latlon[1]:.6f}_{grid_bbox_latlon[2]:.6f}_{grid_bbox_latlon[3]:.6f}_z{int(request.terrarium_zoom)}_zs{float(request.terrain_z_scale):.3f}_ms{float(request.model_size_mm):.1f}"
    city_hash = hashlib.md5(city_key.encode()).hexdigest()
    city_cache_file = cache_dir / f"city_{city_hash}.json"

    cached = None
    if city_cache_file.exists():
        try:
            cached = json.loads(city_cache_file.read_text(encoding="utf-8"))
            print(f"[INFO] Використовуємо кеш міста: {city_cache_file.name}")
        except Exception:
            cached = None

    if cached and isinstance(cached, dict) and "center" in cached:
        try:
            c = cached.get("center") or {}
            global_center = set_global_center(float(c["lat"]), float(c["lon"]))
        except Exception:
            global_center = set_global_center(grid_center_lat, grid_center_lon)
    else:
        global_center = set_global_center(grid_center_lat, grid_center_lon)
    print(f"[INFO] Глобальний центр встановлено: lat={global_center.center_lat:.6f}, lon={global_center.center_lon:.6f}, UTM zone={global_center.utm_zone}")

    # CRITICAL: store global DEM bbox so all zones sample elevations from the same tile set (and it is stable across sessions)
    try:
        from services.global_center import set_global_dem_bbox_latlon
        set_global_dem_bbox_latlon(grid_bbox_latlon)
    except Exception:
        pass
    
    # КРИТИЧНО: Обчислюємо глобальний elevation_ref_m для всієї сітки
    # Це забезпечує, що всі зони використовують одну базову висоту для нормалізації
    # і ідеально стикуються одна з одною
    print(f"[INFO] Обчислення глобального elevation_ref для синхронізації висот між зонами...")
    
    # Визначаємо source_crs для обчислення elevation_ref
    source_crs = None
    try:
        from services.crs_utils import bbox_latlon_to_utm
        bbox_utm_result = bbox_latlon_to_utm(*grid_bbox_latlon)
        source_crs = bbox_utm_result[4]  # CRS
    except Exception as e:
        print(f"[WARN] Не вдалося визначити source_crs для elevation_ref: {e}")
    
    # Обчислюємо глобальний elevation_ref_m та baseline_offset_m
    # Guard against corrupted/invalid cached refs (we've seen Terrarium outlier pixels produce huge negative mins).
    cached_elev = None
    if cached and isinstance(cached, dict):
        try:
            ce = cached.get("elevation_ref_m")
            if ce is not None:
                ce = float(ce)
                # Reject clearly bogus negative refs (Terrarium outliers) that create "tower bases".
                if -120.0 <= ce <= 9000.0:
                    cached_elev = ce
        except Exception:
            cached_elev = None

    if cached_elev is not None:
        global_elevation_ref_m = float(cached.get("elevation_ref_m"))
        global_baseline_offset_m = float(cached.get("baseline_offset_m") or 0.0)
        print(f"[INFO] Глобальний elevation_ref_m (кеш): {global_elevation_ref_m:.2f}м")
        print(f"[INFO] Глобальний baseline_offset_m (кеш): {global_baseline_offset_m:.3f}м")
    else:
        global_elevation_ref_m, global_baseline_offset_m = calculate_global_elevation_reference(
            zones=request.zones,
            source_crs=source_crs,
            terrarium_zoom=request.terrarium_zoom if hasattr(request, 'terrarium_zoom') else 15,
            z_scale=float(request.terrain_z_scale),
            sample_points_per_zone=25,  # Кількість точок для семплінгу в кожній зоні
            global_center=global_center,  # ВАЖЛИВО: передаємо глобальний центр для конвертації координат
        )
    
    if global_elevation_ref_m is not None:
        print(f"[INFO] Глобальний elevation_ref_m: {global_elevation_ref_m:.2f}м (висота над рівнем моря)")
        print(f"[INFO] Глобальний baseline_offset_m: {global_baseline_offset_m:.3f}м")
    else:
        print(f"[WARN] Не вдалося обчислити глобальний elevation_ref_m, кожна зона використовуватиме локальну нормалізацію")
    
    # Обчислюємо оптимальну товщину підложки для всіх зон
    # Мінімізуємо товщину, але забезпечуємо стабільність
    # CRITICAL (stitching across sessions): base thickness must be stable across "add more zones".
    # Do not make it depend on how many zones were selected in this request.
    final_base_thickness_mm = max(float(request.terrain_base_thickness_mm), 0.5)
    print(f"[INFO] Фінальна товщина підложки: {final_base_thickness_mm:.2f}мм (користувацька: {request.terrain_base_thickness_mm:.2f}мм)")

    # Save/refresh city cache for future requests
    try:
        cache_payload = {
            "bbox": {"north": grid_bbox_latlon[0], "south": grid_bbox_latlon[1], "east": grid_bbox_latlon[2], "west": grid_bbox_latlon[3]},
            "center": {"lat": float(global_center.center_lat), "lon": float(global_center.center_lon)},
            "terrarium_zoom": int(request.terrarium_zoom),
            "terrain_z_scale": float(request.terrain_z_scale),
            "model_size_mm": float(request.model_size_mm),
            "elevation_ref_m": float(global_elevation_ref_m) if global_elevation_ref_m is not None else None,
            "baseline_offset_m": float(global_baseline_offset_m) if global_baseline_offset_m is not None else 0.0,
            "terrain_base_thickness_mm": float(final_base_thickness_mm),
        }
        city_cache_file.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    
    task_ids = []
    
    for zone_idx, zone in enumerate(request.zones):
        # Отримуємо bbox з зони
        geometry = zone.get('geometry', {})
        if geometry.get('type') != 'Polygon':
            continue
        
        coordinates = geometry.get('coordinates', [])
        if not coordinates or len(coordinates) == 0:
            continue
        
        # Знаходимо min/max координати
        all_coords = [coord for ring in coordinates for coord in ring]
        lons = [coord[0] for coord in all_coords]
        lats = [coord[1] for coord in all_coords]
        
        zone_bbox = {
            'north': max(lats),
            'south': min(lats),
            'east': max(lons),
            'west': min(lons)
        }
        
        # Створюємо GenerationRequest для цієї зони
        # Використовуємо дефолтне значення для terrain_smoothing_sigma якщо None
        terrain_smoothing_sigma = request.terrain_smoothing_sigma if request.terrain_smoothing_sigma is not None else 2.0
        
        zone_request = GenerationRequest(
            north=zone_bbox['north'],
            south=zone_bbox['south'],
            east=zone_bbox['east'],
            west=zone_bbox['west'],
            model_size_mm=request.model_size_mm,
            road_width_multiplier=request.road_width_multiplier,
            road_height_mm=request.road_height_mm,
            road_embed_mm=request.road_embed_mm,
            building_min_height=request.building_min_height,
            building_height_multiplier=request.building_height_multiplier,
            building_foundation_mm=request.building_foundation_mm,
            building_embed_mm=request.building_embed_mm,
            building_max_foundation_mm=request.building_max_foundation_mm,
            water_depth=request.water_depth,
            terrain_enabled=request.terrain_enabled,
            terrain_z_scale=request.terrain_z_scale,
            terrain_base_thickness_mm=final_base_thickness_mm,  # Використовуємо оптимальну товщину
            terrain_resolution=request.terrain_resolution,
            terrarium_zoom=request.terrarium_zoom,
            terrain_smoothing_sigma=terrain_smoothing_sigma,
            terrain_subdivide=request.terrain_subdivide if request.terrain_subdivide is not None else False,
            terrain_subdivide_levels=request.terrain_subdivide_levels if request.terrain_subdivide_levels is not None else 1,
            flatten_buildings_on_terrain=request.flatten_buildings_on_terrain,
            flatten_roads_on_terrain=request.flatten_roads_on_terrain if request.flatten_roads_on_terrain is not None else False,
            export_format=request.export_format,
            context_padding_m=request.context_padding_m,
            terrain_only=bool(getattr(request, "terrain_only", False)),
            include_parks=bool(getattr(request, "include_parks", True)),
            include_pois=bool(getattr(request, "include_pois", True)),
            # КРИТИЧНО: Передаємо глобальні параметри для синхронізації висот
            elevation_ref_m=global_elevation_ref_m,  # Глобальна базова висота для всіх зон
            baseline_offset_m=global_baseline_offset_m,  # Глобальне зміщення baseline
            preserve_global_xy=True,  # IMPORTANT: export in a shared coordinate frame for stitching
        )
        
        # Генеруємо модель для зони
        task_id = str(uuid.uuid4())
        zone_id_str = zone.get('id', f'zone_{zone_idx}')
        props = zone.get("properties") or {}
        zone_row = props.get("row")
        zone_col = props.get("col")
        task = GenerationTask(task_id=task_id, request=zone_request)
        tasks[task_id] = task
        
        # Зберігаємо форму зони (полігон) для обрізання мешів
        zone_polygon_coords = coordinates[0] if coordinates else None  # Зовнішній ring полігону
        
        print(f"[INFO] Створюємо задачу {task_id} для зони {zone_id_str} (зона {zone_idx + 1}/{len(request.zones)})")
        print(f"[DEBUG] Zone bbox: north={zone_bbox['north']:.6f}, south={zone_bbox['south']:.6f}, east={zone_bbox['east']:.6f}, west={zone_bbox['west']:.6f}")
        
        background_tasks.add_task(
            generate_model_task,
            task_id=task_id,
            request=zone_request,
            zone_id=zone_id_str,
            zone_polygon_coords=zone_polygon_coords,  # Передаємо координати полігону для обрізання (fallback)
            zone_row=zone_row,
            zone_col=zone_col,
            grid_bbox_latlon=grid_bbox_latlon,
            hex_size_m=float(getattr(request, "hex_size_m", 500.0)),
        )
        
        task_ids.append(task_id)
        print(f"[DEBUG] Задача {task_id} додана до background_tasks. Всього задач: {len(task_ids)}")
    
    if len(task_ids) == 0:
        raise HTTPException(status_code=400, detail="Не вдалося створити задачі для зон")
    
    print(f"[INFO] Створено {len(task_ids)} задач для генерації зон: {task_ids}")
    
    # Зберігаємо зв'язок для множинних задач
    # ВАЖЛИВО: груповий task_id має бути унікальним, інакше multiple_2 буде колізити між запусками
    if len(task_ids) > 1:
        main_task_id = f"batch_{uuid.uuid4()}"
        multiple_tasks_map[main_task_id] = task_ids
        print(f"[INFO] Batch задачі: {main_task_id} -> {task_ids}")
        print(f"[INFO] Для відображення всіх зон разом використовуйте all_task_ids: {task_ids}")
    else:
        main_task_id = task_ids[0]
    
    # Повертаємо список task_id
    # ВАЖЛИВО: all_task_ids містить всі task_id для кожної зони
    # Фронтенд має завантажити всі файли з цих task_id та об'єднати їх
    return GenerationResponse(
        task_id=main_task_id,
        status="processing",
        message=f"Створено {len(task_ids)} задач для генерації зон. Використовуйте all_task_ids для завантаження всіх зон.",
        all_task_ids=task_ids  # Додаємо список всіх task_id
    )


async def generate_model_task(
    task_id: str,
    request: GenerationRequest,
    zone_id: Optional[str] = None,
    zone_polygon_coords: Optional[list] = None,
    zone_row: Optional[int] = None,
    zone_col: Optional[int] = None,
    grid_bbox_latlon: Optional[Tuple[float, float, float, float]] = None,
    hex_size_m: Optional[float] = None,
):
    """
    Фонова задача генерації 3D моделі
    """
    print(f"[INFO] === ПОЧАТОК ГЕНЕРАЦІЇ МОДЕЛІ === Task ID: {task_id}, Zone ID: {zone_id}")
    task = tasks[task_id]
    zone_prefix = f"[{zone_id}] " if zone_id else ""
    
    try:
        # 0) Глобальний центр (потрібний для коректної локальної системи координат + padding bbox)
        # ВАЖЛИВО: Якщо глобальний центр вже встановлено (наприклад, для сітки зон),
        # використовуємо його. Інакше створюємо новий на основі bbox цієї зони.
        # For batch zones: use a single global DEM bbox so heights are consistent and seams don't appear.
        try:
            from services.global_center import get_global_dem_bbox_latlon
            latlon_bbox = get_global_dem_bbox_latlon() or (request.north, request.south, request.east, request.west)
        except Exception:
            latlon_bbox = (request.north, request.south, request.east, request.west)
        
        # Перевіряємо, чи вже є встановлений глобальний центр (для сітки зон)
        existing_global_center = get_global_center()
        if existing_global_center is not None:
            global_center = existing_global_center
            print(f"[INFO] Використовується ВЖЕ ВСТАНОВЛЕНИЙ глобальний центр (для сітки): lat={global_center.center_lat:.6f}, lon={global_center.center_lon:.6f}")
        else:
            # Якщо немає встановленого центру, створюємо новий для цієї зони
            global_center = get_or_create_global_center(bbox_latlon=latlon_bbox)
            print(f"[INFO] Створено новий глобальний центр для зони: lat={global_center.center_lat:.6f}, lon={global_center.center_lon:.6f}")

        # 1) zone polygon (local) + bbox_meters + scale_factor
        # CRITICAL: for stitched zones, scale_factor must be derived from the SAME geometric reference
        # (zone polygon bounds), not from per-zone bbox (which varies and breaks mm->meters conversions).
        zone_polygon_local = None
        reference_xy_m = None

        # BEST (stitching-critical): reconstruct exact hex polygon in metric space (no lat/lon round-trip).
        if (
            global_center is not None
            and grid_bbox_latlon is not None
            and zone_row is not None
            and zone_col is not None
            and hex_size_m is not None
        ):
            try:
                import math
                from shapely.geometry import Polygon as ShapelyPolygon
                from services.crs_utils import bbox_latlon_to_utm
                from services.hexagonal_grid import hexagon_center_to_corner

                north, south, east, west = grid_bbox_latlon
                minx_utm_grid, miny_utm_grid, _, _, _, _, _ = bbox_latlon_to_utm(float(north), float(south), float(east), float(west))

                hs = float(hex_size_m)
                hex_width = math.sqrt(3.0) * hs
                hex_height = 1.5 * hs

                r = int(zone_row)
                c = int(zone_col)

                center_x = float(minx_utm_grid + c * hex_width + (hex_width / 2.0 if (r % 2) == 1 else 0.0))
                center_y = float(miny_utm_grid + r * hex_height)

                corners_utm = hexagon_center_to_corner(center_x, center_y, hs)  # list[(x,y)]
                local_coords = []
                for x_utm, y_utm in corners_utm:
                    x_local, y_local = global_center.to_local(float(x_utm), float(y_utm))
                    local_coords.append((float(x_local), float(y_local)))

                zone_polygon_local = ShapelyPolygon(local_coords)
                if not zone_polygon_local.is_valid:
                    zone_polygon_local = zone_polygon_local.buffer(0)

                if zone_polygon_local is not None and not zone_polygon_local.is_empty:
                    # IMPORTANT: hexagon_center_to_corner orientation produces:
                    # width ~= sqrt(3)*size, height ~= 2*size
                    reference_xy_m = (float(hex_width), float(2.0 * hs))
                    print(
                        f"[DEBUG] Reconstructed hex zone polygon from row/col ({r},{c}) in local coords; "
                        f"reference_xy_m={reference_xy_m[0]:.2f}x{reference_xy_m[1]:.2f}м"
                    )
            except Exception as e:
                print(f"[WARN] Failed to reconstruct hex polygon from row/col: {e}")

        # Fallback: use provided polygon coordinates (lat/lon -> local), may have small drift.
        if zone_polygon_local is None and zone_polygon_coords is not None and global_center is not None:
            try:
                from shapely.geometry import Polygon as ShapelyPolygon
                local_coords = []
                for coord in zone_polygon_coords:
                    lon, lat = coord[0], coord[1]
                    x_utm, y_utm = global_center.to_utm(lon, lat)
                    x_local, y_local = global_center.to_local(x_utm, y_utm)
                    local_coords.append((x_local, y_local))
                if len(local_coords) >= 3:
                    zone_polygon_local = ShapelyPolygon(local_coords)
                    if not zone_polygon_local.is_valid:
                        zone_polygon_local = zone_polygon_local.buffer(0)
                    if zone_polygon_local is not None and not zone_polygon_local.is_empty:
                        b = zone_polygon_local.bounds  # (minx, miny, maxx, maxy) in LOCAL meters
                        reference_xy_m = (float(b[2] - b[0]), float(b[3] - b[1]))
                        print(f"[DEBUG] Полігон зони перетворено в локальні координати ({len(local_coords)} точок), reference_xy_m={reference_xy_m[0]:.2f}x{reference_xy_m[1]:.2f}м")
            except Exception as e:
                print(f"[WARN] Помилка створення полігону зони: {e}")

        # bbox_meters (локальні координати)
        # Prefer exact zone_polygon bounds (stitching-safe); fallback to request bbox.
        if zone_polygon_local is not None and not zone_polygon_local.is_empty:
            b = zone_polygon_local.bounds
            bbox_meters = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            print(f"[DEBUG] Bbox для зони (з полігону, локальні координати): {bbox_meters}")
        else:
            from services.crs_utils import bbox_latlon_to_utm
            bbox_utm_result = bbox_latlon_to_utm(request.north, request.south, request.east, request.west)
            bbox_utm_coords = bbox_utm_result[:4]  # (minx, miny, maxx, maxy) в UTM

            minx_utm, miny_utm, maxx_utm, maxy_utm = bbox_utm_coords
            minx_local, miny_local = global_center.to_local(minx_utm, miny_utm)
            maxx_local, maxy_local = global_center.to_local(maxx_utm, maxy_utm)

            bbox_meters = (float(minx_local), float(miny_local), float(maxx_local), float(maxy_local))
            print(f"[DEBUG] Bbox для зони (локальні координати): {bbox_meters}")

        scale_factor = None
        try:
            # Prefer polygon bounds for stable scaling across stitched tiles.
            if reference_xy_m is not None:
                sx, sy = float(reference_xy_m[0]), float(reference_xy_m[1])
                avg_xy = (sx + sy) / 2.0 if (sx > 0 and sy > 0) else max(sx, sy)
                if avg_xy and avg_xy > 0:
                    scale_factor = float(request.model_size_mm) / float(avg_xy)
                    print(f"[DEBUG] Scale factor (polygon) для зони: {scale_factor:.6f} мм/м (reference: {sx:.1f} x {sy:.1f} м)")
            if scale_factor is None:
                # Fallback: use bbox_meters (already in local coords) if local vars are not available
                try:
                    size_x = float(bbox_meters[2] - bbox_meters[0])
                    size_y = float(bbox_meters[3] - bbox_meters[1])
                except Exception:
                    size_x = 0.0
                    size_y = 0.0
                avg_xy = (size_x + size_y) / 2.0 if (size_x > 0 and size_y > 0) else max(size_x, size_y)
                if avg_xy and avg_xy > 0:
                    scale_factor = float(request.model_size_mm) / float(avg_xy)
                    print(f"[DEBUG] Scale factor (bbox) для зони: {scale_factor:.6f} мм/м (розмір зони: {size_x:.1f} x {size_y:.1f} м)")
        except Exception as e:
            print(f"[WARN] Помилка обчислення scale_factor: {e}")
            scale_factor = None

        # 2) Завантаження даних ТІЛЬКИ для конкретної зони (без padding, без кешу)
        # Дані будуть одразу обрізатись по полігону зони після завантаження
        task.update_status("processing", 10, "Завантаження даних OSM для зони...")

        # Завантажуємо дані ТІЛЬКИ для цієї зони
        # ВАЖЛИВО: Для доріг використовуємо padding, щоб отримати повні мости з сусідніх зон
        print(f"[DEBUG] Завантаження даних для зони: north={request.north}, south={request.south}, east={request.east}, west={request.west}")
        
        # Padding для доріг (0.01° ≈ 1км) для детекції мостів
        road_padding = 0.01
        gdf_buildings, gdf_water, G_roads = fetch_city_data(
            request.north + road_padding, 
            request.south - road_padding, 
            request.east + road_padding, 
            request.west - road_padding,
            padding=0.002  # Стандартний padding для будівель/води
        )
        
        # Логування завантажених даних
        num_buildings = len(gdf_buildings) if gdf_buildings is not None and not gdf_buildings.empty else 0
        num_water = len(gdf_water) if gdf_water is not None and not gdf_water.empty else 0
        num_roads = 0
        if G_roads is not None:
            if hasattr(G_roads, 'edges'):
                num_roads = len(G_roads.edges)
            else:
                import geopandas as gpd
                if isinstance(G_roads, gpd.GeoDataFrame) and not G_roads.empty:
                    num_roads = len(G_roads)
        print(f"[DEBUG] Завантажено: {num_buildings} будівель, {num_water} вод, {num_roads} доріг")

        task.update_status("processing", 20, "Генерація рельєфу...")
        
        # Тестування: якщо terrain_only=True, генеруємо тільки рельєф та воду (без будівель, доріг)
        # ВАЖЛИВО: перевіряємо terrain_only ДО створення рельєфу, щоб не вирівнювати під будівлями/дорогами
        if request.terrain_only:
            task.update_status("processing", 25, "Створення рельєфу для тестування (з водою, без будівель, доріг)...")
            
            # Створюємо рельєф БЕЗ урахування будівель, доріг, АЛЕ З водою
            source_crs = None
            try:
                if gdf_buildings is not None and not gdf_buildings.empty:
                    source_crs = gdf_buildings.crs
                elif G_roads is not None and hasattr(G_roads, "crs"):
                    source_crs = getattr(G_roads, "crs", None)
            except Exception:
                pass
            
            # water depth in meters (world units before scaling)
            # ВАЖЛИВО: обчислюємо water_depth_m ПЕРЕД створенням рельєфу, щоб правильно вирізати depression
            water_depth_m = None
            has_water = gdf_water is not None and not gdf_water.empty
            if has_water:
                if scale_factor and scale_factor > 0:
                    water_depth_m = float(request.water_depth) / float(scale_factor)
                else:
                    # Fallback: використовуємо приблизну глибину (2мм на моделі = ~0.002м у світі для 100мм моделі)
                    water_depth_m = float(request.water_depth) / 1000.0  # мм -> метри
            
            # Передаємо water_geometries тільки якщо є вода та water_depth_m > 0
            water_geoms_for_terrain = None
            water_depth_for_terrain = 0.0
            if has_water and water_depth_m is not None and water_depth_m > 0:
                water_geoms_for_terrain = list(gdf_water.geometry.values)
                water_depth_for_terrain = float(water_depth_m)
            
            # КРИТИЧНО: Використовуємо глобальні параметри для синхронізації висот між зонами
            elevation_ref_m = getattr(request, 'elevation_ref_m', None)
            baseline_offset_m = getattr(request, 'baseline_offset_m', 0.0)
            
            terrain_mesh, terrain_provider = create_terrain_mesh(
                bbox_meters,
                z_scale=request.terrain_z_scale,
                resolution=request.terrain_resolution,
                latlon_bbox=latlon_bbox,
                source_crs=source_crs,
                terrarium_zoom=request.terrarium_zoom,
                # КРИТИЧНО: Глобальні параметри для синхронізації висот між зонами
                elevation_ref_m=elevation_ref_m,  # Глобальна базова висота (метри над рівнем моря)
                baseline_offset_m=baseline_offset_m,  # Глобальне зміщення baseline (метри)
                base_thickness=(float(request.terrain_base_thickness_mm) / float(scale_factor)) if scale_factor else 5.0,
                flatten_buildings=False,  # Не вирівнюємо під будівлями в тестовому режимі
                building_geometries=None,  # Немає будівель
                flatten_roads=False,  # Немає доріг
                road_geometries=None,
                smoothing_sigma=float(request.terrain_smoothing_sigma) if request.terrain_smoothing_sigma is not None else 0.0,
                water_geometries=water_geoms_for_terrain,  # Додаємо воду тільки якщо є вода та depth > 0
                water_depth_m=water_depth_for_terrain,  # Глибина depression в рельєфі
                # Subdivision для плавнішого mesh
                subdivide=bool(request.terrain_subdivide),
                subdivide_levels=int(request.terrain_subdivide_levels),
                global_center=global_center,  # ВАЖЛИВО: передаємо глобальний центр для синхронізації
            )
            
            if terrain_mesh is None:
                raise ValueError("Terrain mesh не створено, але terrain_only=True. Переконайтеся, що terrain_enabled=True або вказано валідні координати.")
            
            # Створюємо water mesh для тестового режиму
            # ВАЖЛИВО: water_surface має бути на рівні ground + depth_meters, де ground вже включає depression
            water_mesh = None
            print(f"[DEBUG] Water check: has_water={has_water}, terrain_provider={'OK' if terrain_provider else 'None'}, water_depth_m={water_depth_m}")
            if has_water:
                print(f"[DEBUG] gdf_water: {len(gdf_water)} об'єктів")
            if has_water and terrain_provider is not None and water_depth_m is not None and water_depth_m > 0:
                task.update_status("processing", 30, "Створення води для тестування...")
                from services.water_processor import process_water_surface
                
                # Збільшуємо товщину води для кращої видимості (1.5-3.0мм на моделі)
                # Використовуємо 30-50% від глибини води, але мінімум 1.5мм для видимості
                min_thickness_mm = 1.5  # Мінімальна товщина для видимості
                max_thickness_mm = min(request.water_depth * 0.5, 3.0)  # Максимум 50% глибини або 3мм
                surface_mm = float(max(min_thickness_mm, min(max_thickness_mm, request.water_depth * 0.4)))
                thickness_m = float(surface_mm) / float(scale_factor) if scale_factor else (water_depth_m * 0.4)
                water_mesh = process_water_surface(
                    gdf_water,
                    thickness_m=float(thickness_m),
                    depth_meters=float(water_depth_m),
                    terrain_provider=terrain_provider,
                    global_center=global_center,  # ВАЖЛИВО: передаємо глобальний центр для перетворення координат
                )
                if water_mesh:
                    print(f"Вода: {len(water_mesh.vertices)} вершин, {len(water_mesh.faces)} граней")
                else:
                    print(f"[WARN] Water mesh не створено! Перевірте gdf_water та параметри")
            else:
                print(f"[WARN] Water не створюється: has_water={has_water}, terrain_provider={'OK' if terrain_provider else 'None'}, water_depth_m={water_depth_m}")
        
            # Експортуємо рельєф та воду
            task.update_status("processing", 90, "Експорт рельєфу та води (тестовий режим)...")
            primary_format = request.export_format.lower()
            output_file = OUTPUT_DIR / f"{task_id}.{primary_format}"
            output_file_abs = output_file.resolve()
            
            export_scene(
                terrain_mesh=terrain_mesh,
                road_mesh=None,
                building_meshes=None,
                water_mesh=water_mesh,  # Додаємо воду
                parks_mesh=None,
                poi_mesh=None,
                filename=str(output_file_abs),
                format=request.export_format,
                model_size_mm=request.model_size_mm,
                # In terrain_only mode we still want perfect stitching behavior.
                add_flat_base=(terrain_mesh is None),
                base_thickness_mm=float(request.terrain_base_thickness_mm),
                reference_xy_m=reference_xy_m,
                preserve_z=bool(getattr(request, "elevation_ref_m", None) is not None),
                preserve_xy=bool(getattr(request, "preserve_global_xy", False)),
            )
            
            # STL для preview якщо обрано 3MF
            if primary_format == "3mf":
                stl_preview_abs = (OUTPUT_DIR / f"{task_id}.stl").resolve()
                export_scene(
                    terrain_mesh=terrain_mesh,
                    road_mesh=None,
                    building_meshes=None,
                    water_mesh=water_mesh,  # Додаємо воду
                    parks_mesh=None,
                    poi_mesh=None,
                    filename=str(stl_preview_abs),
                    format="stl",
                    model_size_mm=request.model_size_mm,
                    add_flat_base=(terrain_mesh is None),
                    base_thickness_mm=float(request.terrain_base_thickness_mm),
                    reference_xy_m=reference_xy_m,
                    preserve_z=bool(getattr(request, "elevation_ref_m", None) is not None),
                    preserve_xy=bool(getattr(request, "preserve_global_xy", False)),
                )
                task.set_output("stl", str(stl_preview_abs))
            
            task.set_output(primary_format, str(output_file_abs))
            task.complete(str(output_file_abs))
            task.update_status("completed", 100, "Рельєф та вода готові!")
            print(f"[OK] Terrain-only задача {task_id} завершена. Файл: {output_file_abs}")
            return
        
        # 2.1 Генерація рельєфу (якщо увімкнено і НЕ terrain_only) - СПОЧАТКУ, щоб мати TerrainProvider
        # ВИПРАВЛЕННЯ: Перетворюємо координати будівель ОДИН РАЗ на початку
        gdf_buildings_local = None
        building_geometries_for_flatten = None
        if gdf_buildings is not None and not gdf_buildings.empty and global_center is not None:
            try:
                from shapely.ops import transform as _transform_buildings
                print(f"[DEBUG] Перетворюємо координати будівель ОДИН РАЗ для використання в flatten та process_buildings")
                def to_local_transform(x, y, z=None):
                    """Трансформер: UTM -> локальні координати"""
                    x_local, y_local = global_center.to_local(x, y)
                    if z is not None:
                        return (x_local, y_local, z)
                    return (x_local, y_local)
                
                # Створюємо копію з перетвореними координатами
                gdf_buildings_local = gdf_buildings.copy()
                gdf_buildings_local['geometry'] = gdf_buildings_local['geometry'].apply(
                    lambda geom: _transform_buildings(to_local_transform, geom) if geom is not None and not geom.is_empty else geom
                )
                
                # Створюємо список геометрій для flatten (в локальних координатах)
                building_geometries_for_flatten = []
                for geom in gdf_buildings_local.geometry.values:
                    if geom is not None and not geom.is_empty:
                        building_geometries_for_flatten.append(geom)
                
                print(f"[DEBUG] Перетворено {len(building_geometries_for_flatten)} геометрій будівель в локальні координати")
            except Exception as e:
                print(f"[WARN] Помилка перетворення координат будівель: {e}")
                import traceback
                traceback.print_exc()
                # Fallback: використовуємо оригінальні дані
                gdf_buildings_local = gdf_buildings
                building_geometries_for_flatten = list(gdf_buildings.geometry.values) if not gdf_buildings.empty else None
        
        terrain_mesh = None
        terrain_provider = None
        if request.terrain_enabled and not request.terrain_only:
            task.update_status("processing", 20, "Генерація рельєфу...")
            latlon_bbox = (request.north, request.south, request.east, request.west)
            source_crs = None
            try:
                if not gdf_buildings.empty:
                    source_crs = gdf_buildings.crs
                elif G_roads is not None and hasattr(G_roads, "crs"):
                    source_crs = getattr(G_roads, "crs", None)
                else:
                    source_crs = None
            except Exception:
                source_crs = None
            # Precompute road polygons once (also can be used to flatten terrain under roads)
            merged_roads_geom = None
            try:
                merged_roads_geom = build_road_polygons(G_roads, width_multiplier=float(request.road_width_multiplier))
            except Exception:
                merged_roads_geom = None

            # water depth in meters (world units before scaling)
            water_depth_m = None
            if scale_factor and scale_factor > 0:
                water_depth_m = float(request.water_depth) / float(scale_factor)

            # КРИТИЧНО: Використовуємо глобальні параметри для синхронізації висот між зонами
            elevation_ref_m = getattr(request, 'elevation_ref_m', None)
            baseline_offset_m = getattr(request, 'baseline_offset_m', 0.0)
            
            if elevation_ref_m is not None:
                print(f"[INFO] Використовується глобальний elevation_ref_m: {elevation_ref_m:.2f}м для синхронізації висот")
                print(f"[INFO] Використовується глобальний baseline_offset_m: {baseline_offset_m:.3f}м")
            else:
                print(f"[INFO] elevation_ref_m не задано, використовується локальна нормалізація для цієї зони")
            
        # NOTE: zone_polygon_local already computed above (before scale_factor), keep using it below.

            # CRITICAL: Clip source geometries to the zone polygon BEFORE meshing.
            # This prevents broken/degenerate meshes at edges caused by triangle-level mesh clipping.
            preclipped_to_zone = False
            if zone_polygon_local is not None and not zone_polygon_local.is_empty:
                try:
                    from shapely.geometry import Polygon as _Poly, MultiPolygon as _MultiPoly, GeometryCollection as _GC

                    def _keep_polygons(g):
                        if g is None or g.is_empty:
                            return None
                        gt = getattr(g, "geom_type", None)
                        if gt in ("Polygon", "MultiPolygon"):
                            return g
                        if gt == "GeometryCollection":
                            polys = [gg for gg in g.geoms if getattr(gg, "geom_type", None) == "Polygon"]
                            if not polys:
                                return None
                            return _MultiPoly(polys) if len(polys) > 1 else polys[0]
                        return None

                    def _clip_geom(g):
                        if g is None or g.is_empty:
                            return None
                        try:
                            out = g.intersection(zone_polygon_local)
                        except Exception:
                            return g
                        out = _keep_polygons(out)
                        if out is None or out.is_empty:
                            return None
                        # drop tiny slivers
                        try:
                            if hasattr(out, "area") and float(out.area) < 1e-6:
                                return None
                        except Exception:
                            pass
                        return out

                    # Clip buildings (local)
                    if gdf_buildings_local is not None and not gdf_buildings_local.empty:
                        gdf_buildings_local = gdf_buildings_local.copy()
                        gdf_buildings_local["geometry"] = gdf_buildings_local["geometry"].apply(_clip_geom)
                        gdf_buildings_local = gdf_buildings_local[gdf_buildings_local.geometry.notna()]
                        gdf_buildings_local = gdf_buildings_local[~gdf_buildings_local.geometry.is_empty]
                        # Keep flatten geometries consistent
                        building_geometries_for_flatten = [
                            g for g in list(gdf_buildings_local.geometry.values) if g is not None and not g.is_empty
                        ]

                    # Prepare water geometries in local coords
                    # - gdf_water_local: clipped to zone (for water carving + water surface meshes)
                    # - water_geometries_local_for_bridges: NOT clipped to zone (for bridge detection; needs context)
                    gdf_water_local = None
                    water_geometries_local = None
                    water_geometries_local_for_bridges = None
                    if gdf_water is not None and not gdf_water.empty and global_center is not None:
                        try:
                            from shapely.ops import transform as _transform

                            def _to_local(x, y, z=None):
                                x_local, y_local = global_center.to_local(x, y)
                                return (x_local, y_local) if z is None else (x_local, y_local, z)

                            gdf_water_local_raw = gdf_water.copy()
                            gdf_water_local_raw["geometry"] = gdf_water_local_raw["geometry"].apply(
                                lambda geom: _transform(_to_local, geom) if geom is not None and not geom.is_empty else geom
                            )

                            # For bridges we MUST keep un-clipped water (context) in local coords
                            try:
                                water_geometries_local_for_bridges = list(gdf_water_local_raw.geometry.values)
                            except Exception:
                                water_geometries_local_for_bridges = None

                            # clip water to zone for mesh generation/carving
                            gdf_water_local = gdf_water_local_raw.copy()
                            gdf_water_local["geometry"] = gdf_water_local["geometry"].apply(_clip_geom)
                            gdf_water_local = gdf_water_local[gdf_water_local.geometry.notna()]
                            gdf_water_local = gdf_water_local[~gdf_water_local.geometry.is_empty]
                            water_geometries_local = list(gdf_water_local.geometry.values)
                        except Exception:
                            gdf_water_local = None
                            water_geometries_local = None
                            water_geometries_local_for_bridges = None

                    # Convert road polygons to local for terrain flattening + clip to zone
                    merged_roads_geom_local = None
                    merged_roads_geom_local_raw = None
                    if merged_roads_geom is not None and global_center is not None:
                        try:
                            from shapely.ops import transform as _transform

                            def _to_local(x, y, z=None):
                                x_local, y_local = global_center.to_local(x, y)
                                return (x_local, y_local) if z is None else (x_local, y_local, z)

                            merged_roads_geom_local_raw = _transform(_to_local, merged_roads_geom)
                            # For terrain flattening we can clip to zone, but for bridges we need the context geometry.
                            merged_roads_geom_local = merged_roads_geom_local_raw.intersection(zone_polygon_local)
                        except Exception:
                            merged_roads_geom_local = None
                            merged_roads_geom_local_raw = None

                    preclipped_to_zone = True
                except Exception:
                    preclipped_to_zone = False
            
            terrain_mesh, terrain_provider = create_terrain_mesh(
                bbox_meters,
                z_scale=request.terrain_z_scale,
                resolution=max(float(request.terrain_resolution), 1.0) if request.terrain_resolution is not None else 1.0,
                latlon_bbox=latlon_bbox,
                source_crs=source_crs,
                terrarium_zoom=request.terrarium_zoom,
                # КРИТИЧНО: Глобальні параметри для синхронізації висот між зонами
                elevation_ref_m=elevation_ref_m,  # Глобальна базова висота (метри над рівнем моря)
                baseline_offset_m=baseline_offset_m,  # Глобальне зміщення baseline (метри)
                # base_thickness в метрах; конвертуємо з "мм на моделі" -> "метри у світі"
                base_thickness=(float(request.terrain_base_thickness_mm) / float(scale_factor)) if scale_factor else 5.0,
                flatten_buildings=bool(request.flatten_buildings_on_terrain),
                building_geometries=building_geometries_for_flatten,  # ВИПРАВЛЕННЯ: використовуємо вже перетворені координати
                flatten_roads=bool(request.flatten_roads_on_terrain),
                road_geometries=locals().get("merged_roads_geom_local") or merged_roads_geom,
                smoothing_sigma=float(request.terrain_smoothing_sigma) if request.terrain_smoothing_sigma is not None else 0.0,
                # water depression terrain-first
                water_geometries=locals().get("water_geometries_local")
                or (list(gdf_water.geometry.values) if (gdf_water is not None and not gdf_water.empty) else None),
                water_depth_m=float(water_depth_m) if water_depth_m is not None else 0.0,
                global_center=global_center,  # ВАЖЛИВО: передаємо глобальний центр для синхронізації
                bbox_is_local=True,  # ВАЖЛИВО: bbox_meters вже в локальних координатах
                # Subdivision для плавнішого mesh
                subdivide=bool(request.terrain_subdivide),
                subdivide_levels=int(request.terrain_subdivide_levels),
                # КРИТИЧНО: Передаємо полігон зони для форми base та стінок
                zone_polygon=zone_polygon_local,
            )
            
            # ВАЖЛИВО: Terrain обрізається в create_terrain_mesh перед створенням стінок
            # Не обрізаємо тут, щоб уникнути подвійного обрізання
        
        task.update_status("processing", 40, "Обробка доріг...")
        
        # 3. Обробка доріг (з урахуванням рельєфу, якщо доступний)
        # Print-safe товщини: якщо scale_factor відомий, конвертуємо mm->meters
        road_height_m = None
        road_embed_m = None
        if scale_factor and scale_factor > 0:
            road_height_m = float(request.road_height_mm) / float(scale_factor)
            road_embed_m = float(request.road_embed_mm) / float(scale_factor)


        # Підготовка водних геометрій для визначення мостів
        # КРИТИЧНО: Використовуємо КЕШІ МІСТА для детекції мостів
        # Це вирішує проблему, коли міст перетинає межу зони (другий берег в іншій зоні)
        water_geoms_for_bridges = None
        if gdf_water is not None and not gdf_water.empty:
            try:
                water_geoms_for_bridges = list(gdf_water.geometry.values)
            except Exception:
                water_geoms_for_bridges = None
        
        # Додатково завантажуємо воду з КЕШУ МІСТА для детекції мостів на краях зони
        # Кеш містить дані для всієї області (всі зони), тому захоплює віддалені береги
        try:
            city_cache_key = getattr(request, 'city_cache_key', None)
            if city_cache_key:
                print(f"[DEBUG] Завантаження води з кешу міста для детекції мостів (key={city_cache_key})...")
                from services.data_loader import load_city_cache
                
                city_data = load_city_cache(city_cache_key)
                if city_data and 'water' in city_data:
                    gdf_water_city = city_data['water']
                    if gdf_water_city is not None and not gdf_water_city.empty:
                        # Об'єднуємо з оригінальною водою (щоб не втратити локальні водойми)
                        if water_geoms_for_bridges is None:
                            water_geoms_for_bridges = list(gdf_water_city.geometry.values)
                        else:
                            # Додаємо тільки унікальні геометрії (щоб не дублювати)
                            existing_bounds = {g.bounds for g in water_geoms_for_bridges if g is not None}
                            for g in gdf_water_city.geometry.values:
                                if g is not None and g.bounds not in existing_bounds:
                                    water_geoms_for_bridges.append(g)
                        print(f"[DEBUG] Додано {len(gdf_water_city)} водних об'єктів з кешу міста для детекції мостів")
                else:
                    print(f"[DEBUG] Кеш міста не містить води, використовуємо тільки локальну воду")
            else:
                print(f"[DEBUG] city_cache_key не задано, використовуємо тільки локальну воду для детекції мостів")
        except Exception as e:
            print(f"[WARN] Не вдалося завантажити воду з кешу міста: {e}")
            # Продовжуємо з оригінальною водою
        
        
        road_mesh = None
        if G_roads is not None:
            # For roads+bridges we keep everything consistent:
            # - pass UTM merged_roads + UTM water_geometries
            # - pass global_center so road_processor converts edges+roads+water to LOCAL consistently
            merged_roads_for_mesh = locals().get("merged_roads_geom")
            gc_for_roads = global_center
            water_geoms_for_bridges_final = water_geoms_for_bridges

            # Minimum printable road width (mm on model) -> meters in world units
            min_road_width_m = None
            try:
                if scale_factor and scale_factor > 0:
                    # Minimum printable road width on the model -> world meters
                    # Keep it small and cap to avoid absurd widths on huge bboxes.
                    min_road_width_m = float(1.0) / float(scale_factor)  # 1.0mm мінімум
                    min_road_width_m = float(min(min_road_width_m, 14.0))
            except Exception:
                min_road_width_m = None

            road_mesh = process_roads(
                G_roads,
                request.road_width_multiplier,
                terrain_provider=terrain_provider,
                road_height=float(road_height_m) if road_height_m is not None else 1.0,
                road_embed=float(road_embed_m) if road_embed_m is not None else 0.0,
                merged_roads=merged_roads_for_mesh,
                water_geometries=water_geoms_for_bridges_final,  # Для визначення мостів
                bridge_height_multiplier=1.5,  # make bridges/overpasses visibly elevated
                global_center=gc_for_roads,
                min_width_m=min_road_width_m,
                clip_polygon=zone_polygon_local,  # pre-clip roads to zone BEFORE extrusion
                city_cache_key=city_cache_key,  # For cross-zone bridge detection
            )
            if road_mesh is None:
                print("[WARN] process_roads повернув None")
        else:
            print("[WARN] G_roads is None, дороги не обробляються")
        
        task.update_status("processing", 50, "Обробка будівель...")
        
        # 4. Обробка будівель (з урахуванням рельєфу, якщо доступний)
        foundation_m = None
        embed_m = None
        max_foundation_m = None
        if scale_factor and scale_factor > 0:
            foundation_m = float(request.building_foundation_mm) / float(scale_factor)
            embed_m = float(request.building_embed_mm) / float(scale_factor)
            max_foundation_m = float(request.building_max_foundation_mm) / float(scale_factor)

        # ВИПРАВЛЕННЯ: Передаємо вже перетворені координати (якщо вони були перетворені)
        buildings_for_processing = gdf_buildings_local if gdf_buildings_local is not None else gdf_buildings

        building_meshes = process_buildings(
            buildings_for_processing,  # ВИПРАВЛЕННЯ: використовуємо вже перетворені координати
            min_height=request.building_min_height,
            height_multiplier=request.building_height_multiplier,
            terrain_provider=terrain_provider,
            foundation_depth=float(foundation_m) if foundation_m is not None else 1.0,
            embed_depth=float(embed_m) if embed_m is not None else 0.0,
            max_foundation_depth=float(max_foundation_m) if max_foundation_m is not None else None,
            global_center=None,  # ВИПРАВЛЕННЯ: координати вже перетворені, не потрібно перетворювати знову
            coordinates_already_local=True,  # ВИПРАВЛЕННЯ: вказуємо, що координати вже в локальних
        )
        
        task.update_status("processing", 60, "Обробка води...")
        
        # 5. Water:
        # - For terrain-enabled: depression is carved directly into terrain heightfield (see create_terrain_mesh),
        #   and for preview/3MF we provide a thin surface mesh (so it doesn't "cover everything").
        # - For terrain-disabled: keep old behavior (depression mesh).
        water_mesh = None
        if gdf_water is not None and not gdf_water.empty:
            water_depth_m = None
            if scale_factor and scale_factor > 0:
                water_depth_m = float(request.water_depth) / float(scale_factor)
            if request.terrain_enabled and terrain_provider is not None and water_depth_m is not None:
                from services.water_processor import process_water_surface

                # thin surface for preview/3MF (0.6mm default, but not thicker than requested depth)
                surface_mm = float(min(max(request.water_depth, 0.2), 0.6))
                thickness_m = float(surface_mm) / float(scale_factor) if scale_factor else 0.001
                water_mesh = process_water_surface(
                    (locals().get("gdf_water_local") if locals().get("gdf_water_local") is not None else gdf_water),
                    thickness_m=float(thickness_m),
                    depth_meters=float(water_depth_m),
                    terrain_provider=terrain_provider,
                    # If we already converted gdf_water to local coords, don't convert again.
                    global_center=None if locals().get("gdf_water_local") is not None else global_center,
                )
            else:
                water_mesh = process_water(
                    (locals().get("gdf_water_local") if locals().get("gdf_water_local") is not None else gdf_water),
                    depth_mm=float(request.water_depth),
                    depth_meters=float(water_depth_m) if water_depth_m is not None else None,
                    terrain_provider=terrain_provider,
                )
        
        # 5.5 Extra layers: parks + POIs (benches)
        parks_mesh = None
        poi_mesh = None
        try:
            # Extras також завантажуємо тільки для цієї зони (без padding)
            gdf_green, gdf_pois = fetch_extras(request.north, request.south, request.east, request.west)
            if scale_factor and scale_factor > 0 and terrain_provider is not None:
                if request.include_parks and gdf_green is not None and not gdf_green.empty:
                    # ВАЖЛИВО: gdf_green приходить в UTM (метри), але terrain_provider + вся сцена вже в локальних координатах.
                    # Якщо не перетворити, intersection з clip_box (локальним) обнулить все -> parks_mesh стане None.
                    try:
                        from shapely.ops import transform as _transform_geom
                        
                        def to_local_transform(x, y, z=None):
                            x_local, y_local = global_center.to_local(x, y)
                            if z is not None:
                                return (x_local, y_local, z)
                            return (x_local, y_local)
                        gdf_green = gdf_green.copy()
                        gdf_green["geometry"] = gdf_green["geometry"].apply(
                            lambda geom: _transform_geom(to_local_transform, geom) if geom is not None and not geom.is_empty else geom
                        )
                    except Exception as e:
                        print(f"[WARN] Не вдалося перетворити gdf_green в локальні координати: {e}")

                    # CRITICAL: Clip parks to zone polygon BEFORE extrusion to avoid huge triangle sheets at edges.
                    if zone_polygon_local is not None and not zone_polygon_local.is_empty:
                        try:
                            def _clip_to_zone(geom):
                                if geom is None or geom.is_empty:
                                    return None
                                try:
                                    out = geom.intersection(zone_polygon_local)
                                except Exception:
                                    return geom
                                if out is None or out.is_empty:
                                    return None
                                # drop tiny artifacts
                                try:
                                    if hasattr(out, "area") and float(out.area) < 10.0:
                                        return None
                                except Exception:
                                    pass
                                return out

                            gdf_green = gdf_green.copy()
                            gdf_green["geometry"] = gdf_green["geometry"].apply(_clip_to_zone)
                            gdf_green = gdf_green[gdf_green.geometry.notna()]
                            gdf_green = gdf_green[~gdf_green.geometry.is_empty]
                        except Exception:
                            pass

                    # Підготовка полігонів доріг для вирізання з парків
                    # СТРАТЕГІЯ "ШИРОКОГО ВИРІЗУ": Створюємо широку маску (з додатковим буфером 1.5м)
                    # для створення "узбіччя" між дорогою та стіною парку
                    road_polygons_for_clipping = None
                    try:
                        print("[INFO] Генерація маски для вирізання доріг (ШИРОКА, з узбіччям 1.5м)...")
                        # Створюємо полігони з додатковим буфером 1.5 метра з кожного боку
                        # Ця геометрія НЕ буде видимою, вона тільки для вирізання дірок у траві
                        cutting_mask_polys = build_road_polygons(
                            G_roads,
                            width_multiplier=float(request.road_width_multiplier),
                            extra_buffer_m=1.5  # <-- ВАЖЛИВО: Додаємо "узбіччя" 1.5м з кожного боку
                        )
                        
                        # Перетворюємо в локальні координати, якщо потрібно
                        if cutting_mask_polys is not None and global_center is not None:
                            from shapely.ops import transform as _transform_cutting_mask
                            def _to_local_cutting(x, y, z=None):
                                x_local, y_local = global_center.to_local(x, y)
                                return (x_local, y_local) if z is None else (x_local, y_local, z)
                            # Перевіряємо, чи потрібно перетворювати (чи вже в локальних)
                            sample_bounds = cutting_mask_polys.bounds if hasattr(cutting_mask_polys, 'bounds') else None
                            if sample_bounds and max(abs(float(sample_bounds[0])), abs(float(sample_bounds[1])), 
                                                      abs(float(sample_bounds[2])), abs(float(sample_bounds[3]))) > 100000.0:
                                # Виглядає як UTM, перетворюємо
                                road_polygons_for_clipping = _transform_cutting_mask(_to_local_cutting, cutting_mask_polys)
                            else:
                                # Вже в локальних координатах
                                road_polygons_for_clipping = cutting_mask_polys
                        else:
                            road_polygons_for_clipping = cutting_mask_polys
                        
                        # Обрізаємо по зоні, якщо потрібно
                        if road_polygons_for_clipping is not None and zone_polygon_local is not None:
                            try:
                                road_polygons_for_clipping = road_polygons_for_clipping.intersection(zone_polygon_local)
                            except Exception:
                                pass
                    except Exception as e:
                        print(f"[WARN] Не вдалося підготувати широку маску доріг для вирізання: {e}")
                        # Fallback до старої логіки
                        try:
                            road_polygons_for_clipping = locals().get("merged_roads_geom_local_raw")
                            if road_polygons_for_clipping is None:
                                road_polygons_for_clipping = locals().get("merged_roads_geom_local")
                            if road_polygons_for_clipping is None and locals().get("merged_roads_geom") is not None and global_center is not None:
                                from shapely.ops import transform as _transform_roads
                                def _to_local_roads(x, y, z=None):
                                    x_local, y_local = global_center.to_local(x, y)
                                    return (x_local, y_local) if z is None else (x_local, y_local, z)
                                road_polygons_for_clipping = _transform_roads(_to_local_roads, locals().get("merged_roads_geom"))
                        except Exception as e2:
                            print(f"[WARN] Fallback також не вдався: {e2}")
                            road_polygons_for_clipping = None
                    
                    # Зменшуємо висоту зелених зон в 2 рази для кращого візуального балансу
                    parks_mesh = process_green_areas(
                        gdf_green,
                        height_m=(float(request.parks_height_mm) / float(scale_factor)) / 1.2,  # Висота зменшена в 2 рази
                        embed_m=float(request.parks_embed_mm) / float(scale_factor),
                        terrain_provider=terrain_provider,
                        global_center=None,  # already in local coords
                        scale_factor=float(scale_factor),
                        # --- КРИТИЧНО: Передаємо полігони доріг для вирізання ---
                        road_polygons=road_polygons_for_clipping,
                    )
                    if parks_mesh is None:
                        print(f"[WARN] process_green_areas повернув None для {len(gdf_green)} парків")
                else:
                    if not request.include_parks:
                        print("[INFO] Парки вимкнені (include_parks=False)")
                    elif gdf_green is None or gdf_green.empty:
                        print("[INFO] Немає даних про парки (gdf_green порожній)")
                if request.include_pois and gdf_pois is not None and not gdf_pois.empty:
                    poi_mesh = process_pois(
                        gdf_pois,
                        size_m=float(request.poi_size_mm) / float(scale_factor),
                        height_m=float(request.poi_height_mm) / float(scale_factor),
                        embed_m=float(request.poi_embed_mm) / float(scale_factor),
                        terrain_provider=terrain_provider,
                        global_center=global_center,  # ВАЖЛИВО: передаємо глобальний центр для перетворення координат
                    )
        except Exception as e:
            print(f"[WARN] extras layers failed: {e}")

        task.update_status("processing", 75, "Покращення якості mesh для 3D принтера...")
        
        # 5.9 Покращення якості всіх mesh для 3D принтера
        if terrain_mesh is not None:
            terrain_mesh = improve_mesh_for_3d_printing(terrain_mesh, aggressive=True)
            is_valid, mesh_warnings = validate_mesh_for_3d_printing(terrain_mesh, scale_factor=scale_factor, model_size_mm=request.model_size_mm)
            if mesh_warnings:
                print(f"[INFO] Попередження щодо якості terrain mesh:")
                for w in mesh_warnings:
                    print(f"  - {w}")
        
        if road_mesh is not None:
            # Вже покращено в road_processor, але перевіряємо
            is_valid, mesh_warnings = validate_mesh_for_3d_printing(road_mesh, scale_factor=scale_factor, model_size_mm=request.model_size_mm)
            if mesh_warnings:
                print(f"[INFO] Попередження щодо якості road mesh:")
                for w in mesh_warnings:
                    print(f"  - {w}")
        
        if building_meshes is not None:
            improved_buildings = []
            for i, bmesh in enumerate(building_meshes):
                if bmesh is not None:
                    improved = improve_mesh_for_3d_printing(bmesh, aggressive=True)
                    improved_buildings.append(improved)
            building_meshes = improved_buildings
        
        if water_mesh is not None:
            water_mesh = improve_mesh_for_3d_printing(water_mesh, aggressive=True)
        
        if parks_mesh is not None:
            parks_mesh = improve_mesh_for_3d_printing(parks_mesh, aggressive=True)
        
        if poi_mesh is not None:
            poi_mesh = improve_mesh_for_3d_printing(poi_mesh, aggressive=True)
        
        task.update_status("processing", 80, "Обрізання мешів по bbox...")
        
        # 5.10 ВИПРАВЛЕННЯ: Обрізаємо всі меші по bbox зони (якщо він відрізняється від OSM bounds)
        # Використовуємо більший tolerance для зон, щоб не втратити дані
        from services.mesh_clipper import clip_mesh_to_bbox
        
        # Перевіряємо, чи bbox_meters відрізняється від зони (може бути більший через OSM bounds)
        # Якщо так, обрізаємо меші по формі зони (полігон) або bbox зони
        # КРИТИЧНО: Використовуємо мінімальний tolerance для точного обрізання біля країв
        clip_tolerance = 0.1  # Tolerance для обрізання (0.1 метра) - точне обрізання біля країв
        
        # ВАЖЛИВО: Якщо є форма зони (полігон), обрізаємо по ній, інакше по bbox
        from services.mesh_clipper import clip_mesh_to_polygon
        
        if terrain_mesh is not None:
            # CRITICAL: terrain is generated with zone_polygon-aware base/walls; mesh-level clipping re-introduces
            # edge artifacts (big thin triangles). Only bbox-clip when polygon is NOT provided.
            if zone_polygon_coords is None:
                clipped_terrain = clip_mesh_to_bbox(terrain_mesh, bbox_meters, tolerance=clip_tolerance)
                if clipped_terrain is not None and len(clipped_terrain.vertices) > 0:
                    terrain_mesh = clipped_terrain
                else:
                    print(f"[WARN] Terrain mesh став порожнім після обрізання, залишаємо оригінальний")
        
        if road_mesh is not None:
            # CRITICAL: roads are already pre-clipped to zone polygon BEFORE extrusion (clip_polygon=zone_polygon_local).
            # Mesh-level clipping here causes "curtains"/huge vertical sheets because it keeps triangles by centroid
            # and does not rebuild boundary caps.
            if zone_polygon_coords is None:
                clipped_road = clip_mesh_to_bbox(road_mesh, bbox_meters, tolerance=clip_tolerance)
                if clipped_road is not None and len(clipped_road.vertices) > 0 and len(clipped_road.faces) > 0:
                    road_mesh = clipped_road
                else:
                    road_mesh = None
        
        if building_meshes is not None:
            # If we already clipped building geometries to zone polygon before meshing, avoid triangle-level clipping (creates spikes).
            if locals().get("preclipped_to_zone"):
                pass
            else:
                clipped_buildings = []
                for i, bmesh in enumerate(building_meshes):
                    if bmesh is not None:
                        if zone_polygon_coords is not None:
                            clipped = clip_mesh_to_polygon(bmesh, zone_polygon_coords, global_center=global_center, tolerance=clip_tolerance)
                        else:
                            clipped = clip_mesh_to_bbox(bmesh, bbox_meters, tolerance=clip_tolerance)
                        if clipped is not None and len(clipped.vertices) > 0 and len(clipped.faces) > 0:
                            clipped_buildings.append(clipped)
                        else:
                            continue
                building_meshes = clipped_buildings if clipped_buildings else None
        
        if water_mesh is not None:
            # If we already clipped water geometries to zone polygon before meshing, avoid triangle-level clipping.
            if locals().get("preclipped_to_zone"):
                pass
            elif zone_polygon_coords is not None:
                clipped_water = clip_mesh_to_polygon(water_mesh, zone_polygon_coords, global_center=global_center, tolerance=clip_tolerance)
                if clipped_water is not None and len(clipped_water.vertices) > 0 and len(clipped_water.faces) > 0:
                    water_mesh = clipped_water
                else:
                    water_mesh = None
            else:
                clipped_water = clip_mesh_to_bbox(water_mesh, bbox_meters, tolerance=clip_tolerance)
                if clipped_water is not None and len(clipped_water.vertices) > 0 and len(clipped_water.faces) > 0:
                    water_mesh = clipped_water
                else:
                    water_mesh = None
        
        if parks_mesh is not None:
            # CRITICAL: parks are pre-clipped to zone polygon BEFORE extrusion; mesh clipping causes edge sheets.
            if zone_polygon_coords is None:
                clipped_parks = clip_mesh_to_bbox(parks_mesh, bbox_meters, tolerance=clip_tolerance)
                if clipped_parks is not None and len(clipped_parks.vertices) > 0 and len(clipped_parks.faces) > 0:
                    parks_mesh = clipped_parks
                else:
                    parks_mesh = None
        
        if poi_mesh is not None:
            if zone_polygon_coords is not None:
                clipped_poi = clip_mesh_to_polygon(poi_mesh, zone_polygon_coords, global_center=global_center, tolerance=clip_tolerance)
            else:
                clipped_poi = clip_mesh_to_bbox(poi_mesh, bbox_meters, tolerance=clip_tolerance)
            if clipped_poi is not None and len(clipped_poi.vertices) > 0:
                poi_mesh = clipped_poi
            else:
                poi_mesh = None
        
        task.update_status("processing", 82, "Експорт моделі...")

        # Reference XY size for export scaling (so tiles have identical scale and align on edges)
        # NOTE: reference_xy_m is computed early (before scale_factor) from the zone polygon bounds.
        
        # 6. Експорт сцени
        primary_format = request.export_format.lower()
        output_file = OUTPUT_DIR / f"{task_id}.{primary_format}"
        output_file_abs = output_file.resolve()
        
        # Діагностика мешів перед експортом
        print(f"Меші: terrain={'OK' if terrain_mesh else 'None'}, roads={'OK' if road_mesh else 'None'}, "
              f"buildings={len(building_meshes) if building_meshes else 0}, water={'OK' if water_mesh else 'None'}, "
              f"parks={'OK' if parks_mesh else 'None'}, poi={'OK' if poi_mesh else 'None'}")
        
        # Експортуємо основну модель
        preserve_z = bool(getattr(request, "elevation_ref_m", None) is not None)
        preserve_xy = bool(getattr(request, "preserve_global_xy", False))
        parts_from_main = export_scene(
            terrain_mesh=terrain_mesh,
            road_mesh=road_mesh,
            building_meshes=building_meshes,
            water_mesh=water_mesh,
            parks_mesh=parks_mesh,
            poi_mesh=poi_mesh,
            filename=str(output_file_abs),
            format=request.export_format,
            model_size_mm=request.model_size_mm,
            # ВАЖЛИВО: Плоска "BaseFlat" потрібна лише коли terrain_mesh відсутній.
            # Якщо terrain_mesh є — він вже включає base_thickness і форму зони,
            # а прямокутна BaseFlat додає "зайву територію" по боках.
            add_flat_base=(terrain_mesh is None),
            base_thickness_mm=float(request.terrain_base_thickness_mm),
            reference_xy_m=reference_xy_m,
            preserve_z=preserve_z,
            preserve_xy=preserve_xy,
        )
        
        # Якщо це STL і є окремі частини, зберігаємо їх
        if parts_from_main and isinstance(parts_from_main, dict) and request.export_format.lower() == "stl":
            for part_name, path in parts_from_main.items():
                task.set_output(f"{part_name}_stl", str(Path(path).resolve()))

        # ДЛЯ PREVIEW: якщо користувач обрав 3MF, паралельно зберігаємо STL (Three.js стабільно вантажить STL)
        # Це також вирішує проблему, коли 3MF loader падає, а frontend намагається парсити ZIP як STL.
        stl_preview_abs: Optional[Path] = None
        if primary_format == "3mf":
            stl_preview_abs = (OUTPUT_DIR / f"{task_id}.stl").resolve()
            export_scene(
                terrain_mesh=terrain_mesh,
                road_mesh=road_mesh,
                building_meshes=building_meshes,
                water_mesh=water_mesh,
                    parks_mesh=parks_mesh,
                    poi_mesh=poi_mesh,
                filename=str(stl_preview_abs),
                format="stl",
                model_size_mm=request.model_size_mm,
                add_flat_base=(terrain_mesh is None),
                base_thickness_mm=float(request.terrain_base_thickness_mm),
                reference_xy_m=reference_xy_m,
                preserve_z=preserve_z,
                preserve_xy=preserve_xy,
            )

        # Кольорове прев'ю: експортуємо STL частини (base/roads/buildings/water) з однаковими трансформаціями
        try:
            preview_items: List[Tuple[str, trimesh.Trimesh]] = []
            if terrain_mesh is not None:
                preview_items.append(("Base", terrain_mesh))
            if road_mesh is not None:
                preview_items.append(("Roads", road_mesh))
            if building_meshes:
                try:
                    combined_buildings = trimesh.util.concatenate([b for b in building_meshes if b is not None])
                    if combined_buildings is not None and len(combined_buildings.vertices) > 0:
                        preview_items.append(("Buildings", combined_buildings))
                except Exception:
                    pass
            if water_mesh is not None:
                preview_items.append(("Water", water_mesh))

            if parks_mesh is not None:
                preview_items.append(("Parks", parks_mesh))
            if poi_mesh is not None:
                preview_items.append(("POI", poi_mesh))

            if preview_items:
                prefix = str((OUTPUT_DIR / task_id).resolve())
                parts = export_preview_parts_stl(
                    output_prefix=prefix,
                    mesh_items=preview_items,
                    model_size_mm=request.model_size_mm,
                    # Flat BaseFlat is needed ONLY when terrain mesh is missing.
                    # If terrain exists it already includes the correct base thickness and zone shape.
                    add_flat_base=(terrain_mesh is None),
                    base_thickness_mm=float(request.terrain_base_thickness_mm),
                    rotate_to_ground=False,
                    reference_xy_m=reference_xy_m,
                    preserve_z=preserve_z,
                    preserve_xy=preserve_xy,
                )
                # Зберігаємо в output_files
                for part_name, path in parts.items():
                    task.set_output(f"{part_name}_stl", str(Path(path).resolve()))
        except Exception as e:
            print(f"[WARN] Preview parts export failed: {e}")
        
        # Перевіряємо, що файл дійсно створено
        if not output_file_abs.exists():
            # If 3MF export failed, model_exporter may have fallen back to STL.
            if primary_format == "3mf":
                stl_fallback = (OUTPUT_DIR / f"{task_id}.stl").resolve()
                if stl_fallback.exists():
                    task.set_output("stl", str(stl_fallback))
                    task.complete(str(stl_fallback))
                    task.update_status("completed", 100, "3MF не згенеровано, але STL створено (fallback).")
                    print(f"[WARN] 3MF не створено для {task_id}, використано STL fallback: {stl_fallback}")
                    return
            raise FileNotFoundError(f"Файл не було створено: {output_file_abs}")

        # Оновлюємо мапу output_files
        task.set_output(primary_format, str(output_file_abs))
        if stl_preview_abs and stl_preview_abs.exists():
            task.set_output("stl", str(stl_preview_abs))
        
        task.complete(str(output_file_abs))
        task.update_status("completed", 100, "Модель готова!")
        print(f"[OK] === ЗАВЕРШЕНО ГЕНЕРАЦІЮ МОДЕЛІ === Task ID: {task_id}, Zone ID: {zone_id}, Файл: {output_file_abs}")
        
    except Exception as e:
        print(f"[ERROR] === ПОМИЛКА ГЕНЕРАЦІЇ МОДЕЛІ === Task ID: {task_id}, Zone ID: {zone_id}, Error: {e}")
        import traceback
        traceback.print_exc()
        task.fail(str(e))
        # IMPORTANT: don't re-raise from background task, otherwise Starlette logs it as ASGI error
        # and it can interrupt other tasks. The failure is already recorded in task state.
        return


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

