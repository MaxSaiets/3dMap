# Інструкція з встановлення та запуску

## Передумови

- Python 3.9+ (для backend)
- Node.js 18+ (для frontend)
- npm або yarn

## Backend встановлення

### Windows

```powershell
cd H:\3dMap\backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Linux/Mac

```bash
cd H:\3dMap\backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Запуск backend

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Backend буде доступний на `http://localhost:8000`

## Frontend встановлення

```bash
cd H:\3dMap\frontend
npm install
```

### Налаштування змінних середовища

Створіть файл `.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Запуск frontend

```bash
npm run dev
```

Frontend буде доступний на `http://localhost:3000`

## Перевірка роботи

1. Відкрийте `http://localhost:3000`
2. Виберіть область на карті (прямокутник, коло або полігон)
3. Налаштуйте параметри генерації
4. Натисніть "Згенерувати модель"
5. Дочекайтеся завершення генерації
6. Завантажте згенерований файл

## Усунення проблем

### Помилка встановлення osmnx на Windows

OSMnx може вимагати додаткових залежностей. Спробуйте:

```bash
conda install -c conda-forge osmnx
```

Або встановіть через pip з попередньо встановленими залежностями:

```bash
pip install geopandas
pip install osmnx
```

### Помилка з rasterio

Якщо виникають проблеми з rasterio (для рельєфу), встановіть через conda:

```bash
conda install -c conda-forge rasterio
```

### Помилка CORS

Якщо frontend не може підключитися до backend, перевірте налаштування CORS в `backend/main.py`:

```python
allow_origins=["http://localhost:3000", "http://localhost:3001"]
```

## Розробка

### Структура проекту

```
3dMap/
├── backend/           # Python FastAPI
│   ├── main.py       # Точка входу API
│   └── services/     # Бізнес-логіка
├── frontend/         # Next.js React
│   ├── app/         # Next.js App Router
│   ├── components/  # React компоненти
│   └── store/       # Zustand store
└── README.md
```

### Додавання нових функцій

1. Backend: Додайте новий сервіс в `backend/services/`
2. Frontend: Додайте новий компонент в `frontend/components/`
3. API: Додайте новий endpoint в `backend/main.py`

## Тестування

### Backend тести

```bash
cd backend
pytest
```

### Frontend тести

```bash
cd frontend
npm test
```

## Продакшн деплой

### Backend

Використовуйте Gunicorn для продакшн:

```bash
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Frontend

```bash
cd frontend
npm run build
npm start
```

Або деплой на Vercel/Netlify для frontend.

