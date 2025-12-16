# Інтеграція 3dMap в MODIK

## Огляд

Проект 3dMap розроблений як окремий модуль, який можна інтегрувати в MODIK як частину 3D-конфігуратора.

## Архітектура інтеграції

### Варіант 1: Мікросервіс (рекомендовано)

3dMap працює як окремий сервіс, який MODIK викликає через API:

```
MODIK Frontend → MODIK Backend → 3dMap API → Генерація моделі
```

**Переваги:**
- Незалежне масштабування
- Можна використовувати окремий сервер для важких обчислень
- Легко оновлювати без впливу на MODIK

### Варіант 2: Вбудований модуль

3dMap інтегрується безпосередньо в структуру MODIK:

```
MODIK/app/3dmap/ → Компоненти 3dMap
MODIK/app/api/3dmap/ → API роути для генерації
```

## Кроки інтеграції

### 1. Backend інтеграція

#### Варіант A: Мікросервіс

Додайте в MODIK `lib/3dmap-client.ts`:

```typescript
import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_3DMAP_API_URL || 'http://localhost:8000';

export async function generate3DMap(request: {
  north: number;
  south: number;
  east: number;
  west: number;
  // ... інші параметри
}) {
  const response = await axios.post(`${API_URL}/api/generate`, request);
  return response.data;
}
```

#### Варіант B: Вбудований модуль

Скопіюйте `backend/services/` в `MODIK/lib/3dmap/` та створіть API route:

```typescript
// MODIK/app/api/3dmap/generate/route.ts
import { generateModel } from '@/lib/3dmap/generator';

export async function POST(request: Request) {
  const body = await request.json();
  const result = await generateModel(body);
  return Response.json(result);
}
```

### 2. Frontend інтеграція

#### Додайте компонент в MODIK

```typescript
// MODIK/components/3dmap/MapGenerator.tsx
'use client';

import { MapSelector } from './MapSelector';
import { Preview3D } from './Preview3D';
import { ControlPanel } from './ControlPanel';

export function MapGenerator() {
  return (
    <div className="h-screen">
      {/* Використовуйте компоненти з 3dMap */}
    </div>
  );
}
```

#### Додайте роут в MODIK

```typescript
// MODIK/app/3dmap/page.tsx
import { MapGenerator } from '@/components/3dmap/MapGenerator';

export default function MapPage() {
  return <MapGenerator />;
}
```

### 3. Інтеграція з існуючим 3D конфігуратором

Якщо в MODIK вже є 3D конфігуратор, можна додати опцію "Імпортувати з карти":

```typescript
// MODIK/components/configurator/ImportMapButton.tsx
'use client';

import { useState } from 'react';
import { MapGenerator } from '@/components/3dmap/MapGenerator';

export function ImportMapButton() {
  const [showMap, setShowMap] = useState(false);

  if (showMap) {
    return (
      <div className="fixed inset-0 z-50">
        <MapGenerator />
        <button onClick={() => setShowMap(false)}>Закрити</button>
      </div>
    );
  }

  return (
    <button onClick={() => setShowMap(true)}>
      Імпортувати з карти
    </button>
  );
}
```

## Налаштування середовища

### Backend (якщо мікросервіс)

1. Встановіть залежності:
```bash
cd H:\3dMap\backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Запустіть сервер:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (вбудований)

1. Скопіюйте залежності в `MODIK/package.json`:
```json
{
  "dependencies": {
    "leaflet": "^1.9.4",
    "react-leaflet": "^4.2.1",
    "leaflet-draw": "^1.0.4"
  }
}
```

2. Встановіть:
```bash
cd H:\MODIK
npm install
```

## Використання в MODIK

### Приклад інтеграції в сторінку продукту

```typescript
// MODIK/app/products/[id]/page.tsx
import { MapGenerator } from '@/components/3dmap/MapGenerator';

export default function ProductPage({ params }: { params: { id: string } }) {
  return (
    <div>
      {/* Інформація про продукт */}
      <MapGenerator />
    </div>
  );
}
```

### Збереження згенерованих моделей

Додайте в базу даних MODIK таблицю для збереження згенерованих моделей:

```sql
CREATE TABLE generated_3d_maps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  bounds JSONB,
  parameters JSONB,
  file_url TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);
```

## Покращення для інтеграції

1. **Автентифікація**: Додайте перевірку користувача перед генерацією
2. **Кешування**: Зберігайте згенеровані моделі для повторного використання
3. **Обмеження**: Додайте ліміти на розмір області та частоту генерації
4. **Історія**: Зберігайте історію генерацій користувача

## Тестування

1. Перевірте, що backend відповідає на запити
2. Перевірте інтеграцію з MODIK автентифікацією
3. Протестуйте генерацію моделей для різних областей
4. Перевірте завантаження файлів

