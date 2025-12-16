# Тести Frontend

## Запуск тестів

```bash
# Запустити всі тести
npm test

# Запустити в режимі watch
npm run test:watch

# Запустити з покриттям коду
npm run test:coverage
```

## Структура тестів

- `store/` - Тести для Zustand store
- `lib/` - Тести для утиліт та API клієнта
- `components/` - Тести для React компонентів
- `integration/` - Інтеграційні тести

## Налаштування

Тести використовують:
- **Jest** - тестовий runner
- **React Testing Library** - тестування React компонентів
- **jsdom** - DOM середовище для тестів

## Моки

- Leaflet та react-leaflet моковані в `jest.setup.js`
- Axios мокований в `__mocks__/axios.ts`

## Інтеграційні тести

Інтеграційні тести вимагають запущеного backend сервера. 
Встановіть змінну середовища `BACKEND_RUNNING=true` перед запуском:

```bash
BACKEND_RUNNING=true npm test
```

