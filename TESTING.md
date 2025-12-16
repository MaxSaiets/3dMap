# Керівництво з тестування

## Огляд

Проект має повний набір тестів для backend (Python) та frontend (TypeScript/React).

## Backend тести

### Встановлення

```bash
cd backend
pip install -r requirements-test.txt
```

### Запуск

```bash
# Всі тести
pytest

# З покриттям
pytest --cov=services --cov-report=html

# Конкретний файл
pytest tests/test_data_loader.py

# З виводом
pytest -v -s

# Тільки unit тести
pytest -m unit

# Тільки інтеграційні
pytest -m integration
```

### Структура тестів

- `test_data_loader.py` - Завантаження OSM даних
- `test_road_processor.py` - Обробка доріг
- `test_building_processor.py` - Обробка будівель
- `test_water_processor.py` - Обробка води
- `test_terrain_generator.py` - Генерація рельєфу
- `test_model_exporter.py` - Експорт моделей
- `test_api.py` - API endpoints
- `test_generation_task.py` - Управління задачами

## Frontend тести

### Встановлення

```bash
cd frontend
npm install
```

### Запуск

```bash
# Всі тести
npm test

# Watch mode
npm run test:watch

# З покриттям
npm run test:coverage
```

### Структура тестів

- `store/` - Тести Zustand store
- `lib/` - Тести API клієнта
- `components/` - Тести React компонентів
- `integration/` - Інтеграційні тести

## Покриття коду

### Backend

```bash
cd backend
pytest --cov=services --cov-report=html
open htmlcov/index.html
```

### Frontend

```bash
cd frontend
npm run test:coverage
open coverage/lcov-report/index.html
```

## Неперервна інтеграція

### GitHub Actions (приклад)

```yaml
name: Tests

on: [push, pull_request]

jobs:
  backend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
      - run: |
          cd backend
          pip install -r requirements.txt
          pip install -r requirements-test.txt
          pytest --cov=services

  frontend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
      - run: |
          cd frontend
          npm install
          npm test
```

## Написання нових тестів

### Backend (pytest)

```python
def test_my_function():
    """Тест опис"""
    # Arrange
    input_data = "test"
    
    # Act
    result = my_function(input_data)
    
    # Assert
    assert result == expected
```

### Frontend (Jest + RTL)

```typescript
describe('MyComponent', () => {
  it('should render correctly', () => {
    render(<MyComponent />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
  })
})
```

## Моки та фікстури

### Backend

Використовуйте `conftest.py` для спільних фікстур.

### Frontend

Моки знаходяться в `jest.setup.js` та `__mocks__/`.

## Примітки

- Деякі тести вимагають інтернет-з'єднання (OSM API)
- Інтеграційні тести вимагають запущеного backend
- Використовуйте моки для тестів без зовнішніх залежностей

