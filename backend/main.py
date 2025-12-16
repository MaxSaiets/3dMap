"""
FastAPI backend для 3D Map Generator
Реалізує логіку генерації 3D моделей з OpenStreetMap даних
"""
import warnings
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
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
    include_pois: bool = True
    poi_size_mm: float = Field(default=0.6, ge=0.2, le=3.0)
    poi_height_mm: float = Field(default=0.8, ge=0.2, le=5.0)
    poi_embed_mm: float = Field(default=0.2, ge=0.0, le=2.0)
    water_depth: float = 2.0  # мм
    terrain_enabled: bool = True
    terrain_z_scale: float = 1.5
    # Тонка основа для друку: за замовчуванням 2мм (користувач може збільшити).
    terrain_base_thickness_mm: float = Field(default=2.0, ge=1.0, le=20.0)
    # Деталізація рельєфу
    # - terrain_resolution: кількість точок по осі (mesh деталь). Вища = детальніше, повільніше.
    terrain_resolution: int = Field(default=180, ge=80, le=320)
    # - terrarium_zoom: зум DEM tiles (Terrarium). Вища = детальніше, але більше тайлів.
    terrarium_zoom: int = Field(default=15, ge=10, le=16)
    # Згладжування рельєфу (sigma в клітинках heightfield). 0 = без згладжування.
    # Допомагає прибрати “грубі грані/шум” на DEM, особливо при високому zoom.
    terrain_smoothing_sigma: float = Field(default=0.6, ge=0.0, le=3.0)
    # Terrain-first стабілізація: вирівняти (flatten) рельєф під будівлями, щоб будівлі не були "криво" на схилах/шумному DEM.
    flatten_buildings_on_terrain: bool = True
    # Terrain-first стабілізація для доріг: робить дороги більш читабельними на малому масштабі і прибирає "шипи" на бокових стінках.
    flatten_roads_on_terrain: bool = True
    export_format: str = "3mf"  # "stl" або "3mf"
    model_size_mm: float = 100.0  # Розмір моделі в міліметрах (за замовчуванням 100мм = 10см)


class GenerationResponse(BaseModel):
    """Відповідь з ID задачі"""
    task_id: str
    status: str


@app.get("/")
async def root():
    return {"message": "3D Map Generator API", "version": "1.0.0"}


@app.post("/api/generate", response_model=GenerationResponse)
async def generate_model(request: GenerationRequest, background_tasks: BackgroundTasks):
    """
    Створює задачу генерації 3D моделі
    """
    task_id = str(uuid.uuid4())
    task = GenerationTask(task_id=task_id, request=request)
    tasks[task_id] = task
    
    # Запускаємо генерацію в фоні
    background_tasks.add_task(generate_model_task, task_id, request)
    
    return GenerationResponse(task_id=task_id, status="processing")


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    """
    Отримує статус задачі генерації
    """
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


async def generate_model_task(task_id: str, request: GenerationRequest):
    """
    Фонова задача генерації 3D моделі
    """
    task = tasks[task_id]
    
    try:
        task.update_status("processing", 10, "Завантаження даних OSM...")
        
        # 1. Завантаження даних
        gdf_buildings, gdf_water, G_roads = fetch_city_data(
            request.north, request.south, request.east, request.west
        )
        
        task.update_status("processing", 20, "Генерація рельєфу...")

        # 2) Розрахунок bounds/scale_factor РАНО і ЗАВЖДИ (навіть коли рельєф вимкнено),
        # щоб "mm on model" параметри (дороги/фундамент/embedding) працювали завжди.
        # scale_factor: (мм на моделі) / (метри у світі)  =>  мм/м
        import osmnx as ox
        minx = miny = -500.0
        maxx = maxy = 500.0
        try:
            if gdf_buildings is not None and not gdf_buildings.empty:
                minx, miny, maxx, maxy = gdf_buildings.total_bounds
            else:
                gdf_edges = None
                if G_roads is not None:
                    if hasattr(G_roads, "total_bounds"):
                        gdf_edges = G_roads
                    else:
                        gdf_edges = ox.graph_to_gdfs(G_roads, nodes=False)
                if gdf_edges is not None and not gdf_edges.empty:
                    minx, miny, maxx, maxy = gdf_edges.total_bounds
        except Exception:
            pass

        bbox_meters = (float(minx), float(miny), float(maxx), float(maxy))
        scale_factor = None
        try:
            size_x = float(maxx - minx)
            size_y = float(maxy - miny)
            avg_xy = (size_x + size_y) / 2.0 if (size_x > 0 and size_y > 0) else max(size_x, size_y)
            if avg_xy and avg_xy > 0:
                scale_factor = float(request.model_size_mm) / float(avg_xy)
        except Exception:
            scale_factor = None

        # 2.1 Генерація рельєфу (якщо увімкнено) - СПОЧАТКУ, щоб мати TerrainProvider
        terrain_mesh = None
        terrain_provider = None
        if request.terrain_enabled:
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

            terrain_mesh, terrain_provider = create_terrain_mesh(
                bbox_meters,
                z_scale=request.terrain_z_scale,
                resolution=request.terrain_resolution,
                latlon_bbox=latlon_bbox,
                source_crs=source_crs,
                terrarium_zoom=request.terrarium_zoom,
                # base_thickness в метрах; конвертуємо з "мм на моделі" -> "метри у світі"
                base_thickness=(float(request.terrain_base_thickness_mm) / float(scale_factor)) if scale_factor else 5.0,
                flatten_buildings=bool(request.flatten_buildings_on_terrain),
                building_geometries=list(gdf_buildings.geometry.values) if (gdf_buildings is not None and not gdf_buildings.empty) else None,
                smoothing_sigma=float(request.terrain_smoothing_sigma) if request.terrain_smoothing_sigma is not None else 0.0,
                # water depression terrain-first
                water_geometries=list(gdf_water.geometry.values) if (gdf_water is not None and not gdf_water.empty) else None,
                water_depth_m=float(water_depth_m) if water_depth_m is not None else 0.0,
            )
        
        task.update_status("processing", 40, "Обробка доріг...")
        
        # 3. Обробка доріг (з урахуванням рельєфу, якщо доступний)
        # Print-safe товщини: якщо scale_factor відомий, конвертуємо mm->meters
        road_height_m = None
        road_embed_m = None
        if scale_factor and scale_factor > 0:
            road_height_m = float(request.road_height_mm) / float(scale_factor)
            road_embed_m = float(request.road_embed_mm) / float(scale_factor)

        road_mesh = process_roads(
            G_roads,
            request.road_width_multiplier,
            terrain_provider=terrain_provider,
            road_height=float(road_height_m) if road_height_m is not None else 1.0,
            road_embed=float(road_embed_m) if road_embed_m is not None else 0.0,
            merged_roads=locals().get("merged_roads_geom"),
        )
        
        task.update_status("processing", 50, "Обробка будівель...")
        
        # 4. Обробка будівель (з урахуванням рельєфу, якщо доступний)
        foundation_m = None
        embed_m = None
        max_foundation_m = None
        if scale_factor and scale_factor > 0:
            foundation_m = float(request.building_foundation_mm) / float(scale_factor)
            embed_m = float(request.building_embed_mm) / float(scale_factor)
            max_foundation_m = float(request.building_max_foundation_mm) / float(scale_factor)

        building_meshes = process_buildings(
            gdf_buildings,
            min_height=request.building_min_height,
            height_multiplier=request.building_height_multiplier,
            terrain_provider=terrain_provider,
            foundation_depth=float(foundation_m) if foundation_m is not None else 1.0,
            embed_depth=float(embed_m) if embed_m is not None else 0.0,
            max_foundation_depth=float(max_foundation_m) if max_foundation_m is not None else None,
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
                    gdf_water,
                    thickness_m=float(thickness_m),
                    depth_meters=float(water_depth_m),
                    terrain_provider=terrain_provider,
                )
            else:
                water_mesh = process_water(
                    gdf_water,
                    depth_mm=float(request.water_depth),
                    depth_meters=float(water_depth_m) if water_depth_m is not None else None,
                    terrain_provider=terrain_provider,
                )
        
        # 5.5 Extra layers: parks + POIs (benches)
        parks_mesh = None
        poi_mesh = None
        try:
            gdf_green, gdf_pois = fetch_extras(request.north, request.south, request.east, request.west)
            if scale_factor and scale_factor > 0 and terrain_provider is not None:
                if request.include_parks and gdf_green is not None and not gdf_green.empty:
                    parks_mesh = process_green_areas(
                        gdf_green,
                        height_m=float(request.parks_height_mm) / float(scale_factor),
                        embed_m=float(request.parks_embed_mm) / float(scale_factor),
                        terrain_provider=terrain_provider,
                    )
                if request.include_pois and gdf_pois is not None and not gdf_pois.empty:
                    poi_mesh = process_pois(
                        gdf_pois,
                        size_m=float(request.poi_size_mm) / float(scale_factor),
                        height_m=float(request.poi_height_mm) / float(scale_factor),
                        embed_m=float(request.poi_embed_mm) / float(scale_factor),
                        terrain_provider=terrain_provider,
                    )
        except Exception as e:
            print(f"[WARN] extras layers failed: {e}")

        task.update_status("processing", 80, "Експорт моделі...")
        
        # 6. Експорт сцени
        primary_format = request.export_format.lower()
        output_file = OUTPUT_DIR / f"{task_id}.{primary_format}"
        output_file_abs = output_file.resolve()
        
        export_scene(
            terrain_mesh=terrain_mesh,
            road_mesh=road_mesh,
            building_meshes=building_meshes,
            water_mesh=water_mesh,
            parks_mesh=parks_mesh,
            poi_mesh=poi_mesh,
            filename=str(output_file_abs),
            format=request.export_format,
            model_size_mm=request.model_size_mm,
            # Плоска база потрібна тільки коли рельєф вимкнено
            add_flat_base=not request.terrain_enabled,
            base_thickness_mm=2.0,
        )

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
                add_flat_base=not request.terrain_enabled,
                base_thickness_mm=2.0,
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
                    add_flat_base=not request.terrain_enabled,
                    base_thickness_mm=2.0,
                    rotate_to_ground=False,
                )
                # Зберігаємо в output_files
                for part_name, path in parts.items():
                    task.set_output(f"{part_name}_stl", str(Path(path).resolve()))
        except Exception as e:
            print(f"[WARN] Preview parts export failed: {e}")
        
        # Перевіряємо, що файл дійсно створено
        if not output_file_abs.exists():
            raise FileNotFoundError(f"Файл не було створено: {output_file_abs}")

        # Оновлюємо мапу output_files
        task.set_output(primary_format, str(output_file_abs))
        if stl_preview_abs and stl_preview_abs.exists():
            task.set_output("stl", str(stl_preview_abs))
        
        task.complete(str(output_file_abs))
        task.update_status("completed", 100, "Модель готова!")
        print(f"[OK] Задача {task_id} завершена. Файл: {output_file_abs}")
        
    except Exception as e:
        task.fail(str(e))
        raise


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

