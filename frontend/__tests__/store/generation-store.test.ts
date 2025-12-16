/**
 * @jest-environment jsdom
 */
import { renderHook, act } from '@testing-library/react'
import { useGenerationStore } from '@/store/generation-store'

// Mock Leaflet
const mockLatLngBounds = jest.fn().mockImplementation(() => ({
  getNorth: () => 50.455,
  getSouth: () => 50.450,
  getEast: () => 30.530,
  getWest: () => 30.520,
}))

describe('GenerationStore', () => {
  beforeEach(() => {
    // Скидаємо store перед кожним тестом
    useGenerationStore.getState().reset()
  })

  it('should initialize with default values', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    expect(result.current.selectedArea).toBeNull()
    expect(result.current.isGenerating).toBe(false)
    expect(result.current.taskId).toBeNull()
    expect(result.current.progress).toBe(0)
    expect(result.current.roadWidthMultiplier).toBe(1.0)
    expect(result.current.exportFormat).toBe('3mf')
  })

  it('should set selected area', () => {
    const { result } = renderHook(() => useGenerationStore())
    const bounds = mockLatLngBounds() as any
    
    act(() => {
      result.current.setSelectedArea(bounds)
    })
    
    expect(result.current.selectedArea).toEqual(bounds)
  })

  it('should update generating status', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    act(() => {
      result.current.setGenerating(true)
    })
    
    expect(result.current.isGenerating).toBe(true)
  })

  it('should update task id', () => {
    const { result } = renderHook(() => useGenerationStore())
    const taskId = 'test-task-id'
    
    act(() => {
      result.current.setTaskId(taskId)
    })
    
    expect(result.current.taskId).toBe(taskId)
  })

  it('should update progress', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    act(() => {
      result.current.updateProgress(50, 'Обробка...')
    })
    
    expect(result.current.progress).toBe(50)
    expect(result.current.status).toBe('Обробка...')
  })

  it('should update road width multiplier', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    act(() => {
      result.current.setRoadWidthMultiplier(1.5)
    })
    
    expect(result.current.roadWidthMultiplier).toBe(1.5)
  })

  it('should update building parameters', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    act(() => {
      result.current.setBuildingMinHeight(3.0)
      result.current.setBuildingHeightMultiplier(1.5)
    })
    
    expect(result.current.buildingMinHeight).toBe(3.0)
    expect(result.current.buildingHeightMultiplier).toBe(1.5)
  })

  it('should update terrain settings', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    act(() => {
      result.current.setTerrainEnabled(false)
      result.current.setTerrainZScale(2.0)
    })
    
    expect(result.current.terrainEnabled).toBe(false)
    expect(result.current.terrainZScale).toBe(2.0)
  })

  it('should update export format', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    act(() => {
      result.current.setExportFormat('stl')
    })
    
    expect(result.current.exportFormat).toBe('stl')
  })

  it('should reset to initial state', () => {
    const { result } = renderHook(() => useGenerationStore())
    
    // Змінюємо стан
    act(() => {
      result.current.setGenerating(true)
      result.current.setTaskId('test-id')
      result.current.updateProgress(50, 'Test')
    })
    
    // Скидаємо
    act(() => {
      result.current.reset()
    })
    
    expect(result.current.isGenerating).toBe(false)
    expect(result.current.taskId).toBeNull()
    expect(result.current.progress).toBe(0)
  })
})

