/**
 * Інтеграційні тести для API
 * 
 * Ці тести вимагають запущеного backend сервера
 * Запустіть: cd backend && python run.py
 */
import { api } from '@/lib/api'

describe('API Integration Tests', () => {
  const testBbox = {
    north: 50.455,
    south: 50.450,
    east: 30.530,
    west: 30.520,
  }

  // Пропускаємо тести якщо backend не запущений
  const isBackendRunning = process.env.BACKEND_RUNNING === 'true'
  const maybeIt = isBackendRunning ? it : it.skip

  beforeAll(() => {
    if (!isBackendRunning) {
      console.warn('Backend не запущений. Пропускаємо інтеграційні тести.')
    }
  })

  maybeIt('should generate model successfully', async () => {
    const request = {
      ...testBbox,
      road_width_multiplier: 1.0,
      building_min_height: 2.0,
      building_height_multiplier: 1.0,
      water_depth: 2.0,
      terrain_enabled: true,
      terrain_z_scale: 1.5,
      export_format: '3mf' as const,
    }

    const response = await api.generateModel(request)

    expect(response).toHaveProperty('task_id')
    expect(response).toHaveProperty('status')
    expect(response.status).toBe('processing')
  }, 30000) // 30 секунд timeout

  maybeIt('should check task status', async () => {
    // Спочатку створюємо задачу
    const request = {
      ...testBbox,
      road_width_multiplier: 1.0,
      building_min_height: 2.0,
      building_height_multiplier: 1.0,
      water_depth: 2.0,
      terrain_enabled: false, // Вимикаємо рельєф для швидшої генерації
      terrain_z_scale: 1.5,
      export_format: 'stl' as const,
    }

    const generateResponse = await api.generateModel(request)
    const taskId = generateResponse.task_id

    // Чекаємо трохи
    await new Promise(resolve => setTimeout(resolve, 2000))

    // Перевіряємо статус
    const status = await api.getStatus(taskId)

    expect(status).toHaveProperty('task_id', taskId)
    expect(status).toHaveProperty('status')
    expect(status).toHaveProperty('progress')
  }, 30000)
})

