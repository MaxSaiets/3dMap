# 3D Map Generator - Backend

Python FastAPI backend для генерації 3D моделей з OpenStreetMap даних.

## Встановлення

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Запуск

```bash
python run.py
```

Або:

```bash
uvicorn main:app --reload
```

## API Endpoints

### POST /api/generate

Створює задачу генерації 3D моделі.

**Request Body:**
```json
{
  "north": 50.5,
  "south": 50.4,
  "east": 30.6,
  "west": 30.5,
  "road_width_multiplier": 1.0,
  "building_min_height": 2.0,
  "building_height_multiplier": 1.0,
  "water_depth": 2.0,
  "terrain_enabled": true,
  "terrain_z_scale": 1.5,
  "export_format": "3mf"
}
```

**Response:**
```json
{
  "task_id": "uuid",
  "status": "processing"
}
```

### GET /api/status/{task_id}

Отримує статус задачі генерації.

**Response:**
```json
{
  "task_id": "uuid",
  "status": "processing",
  "progress": 50,
  "message": "Обробка доріг...",
  "download_url": null
}
```

### GET /api/download/{task_id}

Завантажує згенерований файл.

## Структура

- `main.py` - FastAPI додаток
- `services/` - Бізнес-логіка:
  - `data_loader.py` - Завантаження даних OSM
  - `road_processor.py` - Обробка доріг
  - `building_processor.py` - Обробка будівель
  - `water_processor.py` - Обробка води
  - `terrain_generator.py` - Генерація рельєфу
  - `model_exporter.py` - Експорт моделей
  - `generation_task.py` - Управління задачами

## Покращення порівняно з Map2Model

1. ✅ Рельєф (Terrain) - інтеграція DEM даних
2. ✅ Покращена топологія - watertight геометрія
3. ✅ Буферизація доріг - фізична ширина
4. ✅ Об'єднання геометрії - уникнення перетинів
5. ✅ 3MF експорт - мультиколірний друк
6. ✅ Вода як об'єкт - булеве віднімання
7. ✅ Скатні дахи - алгоритм Straight Skeleton (TODO)

