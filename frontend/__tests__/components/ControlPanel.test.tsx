/**
 * @jest-environment jsdom
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ControlPanel } from '@/components/ControlPanel'
import { useGenerationStore } from '@/store/generation-store'
import { api } from '@/lib/api'

jest.mock('@/store/generation-store')
jest.mock('@/lib/api')

const mockUseGenerationStore = useGenerationStore as jest.MockedFunction<typeof useGenerationStore>
const mockApi = api as jest.Mocked<typeof api>

describe('ControlPanel', () => {
  const mockStore = {
    selectedArea: null,
    isGenerating: false,
    taskId: null,
    progress: 0,
    status: '',
    downloadUrl: null,
    roadWidthMultiplier: 1.0,
    buildingMinHeight: 2.0,
    buildingHeightMultiplier: 1.0,
    waterDepth: 2.0,
    terrainEnabled: true,
    terrainZScale: 1.5,
    exportFormat: '3mf' as const,
    modelSizeMm: 100.0,
    setRoadWidthMultiplier: jest.fn(),
    setBuildingMinHeight: jest.fn(),
    setBuildingHeightMultiplier: jest.fn(),
    setWaterDepth: jest.fn(),
    setTerrainEnabled: jest.fn(),
    setTerrainZScale: jest.fn(),
    setExportFormat: jest.fn(),
    setModelSizeMm: jest.fn(),
    setGenerating: jest.fn(),
    setTaskId: jest.fn(),
    updateProgress: jest.fn(),
    setDownloadUrl: jest.fn(),
    reset: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
    mockUseGenerationStore.mockReturnValue(mockStore)
  })

  it('should render control panel', () => {
    render(<ControlPanel />)
    
    expect(screen.getByText('3D Map Generator')).toBeInTheDocument()
    expect(screen.getByText('Згенерувати модель')).toBeInTheDocument()
  })

  it('should disable generate button when no area selected', () => {
    render(<ControlPanel />)
    
    const generateButton = screen.getByText('Згенерувати модель').closest('button')
    expect(generateButton).toBeDisabled()
  })

  it('should enable generate button when area is selected', () => {
    mockUseGenerationStore.mockReturnValue({
      ...mockStore,
      selectedArea: {} as any, // Mock LatLngBounds
    })
    
    render(<ControlPanel />)
    
    const generateButton = screen.getByText('Згенерувати модель')
    expect(generateButton).not.toBeDisabled()
  })

  it('should call generate API when button clicked', async () => {
    const mockBounds = {
      getNorth: () => 50.455,
      getSouth: () => 50.450,
      getEast: () => 30.530,
      getWest: () => 30.520,
    }
    
    mockUseGenerationStore.mockReturnValue({
      ...mockStore,
      selectedArea: mockBounds as any,
    })
    
    mockApi.generateModel.mockResolvedValue({
      task_id: 'test-task-id',
      status: 'processing',
    })
    
    render(<ControlPanel />)
    
    const generateButton = screen.getByText('Згенерувати модель')
    fireEvent.click(generateButton)
    
    await waitFor(() => {
      expect(mockApi.generateModel).toHaveBeenCalled()
      expect(mockStore.setGenerating).toHaveBeenCalledWith(true)
      expect(mockStore.setTaskId).toHaveBeenCalledWith('test-task-id')
    })
  })

  it('should update parameters when sliders change', () => {
    render(<ControlPanel />)
    
    // Знаходимо input за типом та значенням
    const sliders = screen.getAllByRole('slider')
    const roadWidthSlider = sliders[0] // Перший слайдер - ширина доріг
    
    fireEvent.change(roadWidthSlider, { target: { value: '1.5' } })
    expect(mockStore.setRoadWidthMultiplier).toHaveBeenCalledWith(1.5)
  })

  it('should show progress when generating', () => {
    mockUseGenerationStore.mockReturnValue({
      ...mockStore,
      isGenerating: true,
      progress: 50,
      status: 'Обробка...',
    })
    
    render(<ControlPanel />)
    
    expect(screen.getByText('Обробка...')).toBeInTheDocument()
    expect(screen.getByText(/Генерація/i)).toBeInTheDocument()
  })

  it('should show download button when model is ready', () => {
    mockUseGenerationStore.mockReturnValue({
      ...mockStore,
      downloadUrl: '/api/download/test-id',
    })
    
    render(<ControlPanel />)
    
    expect(screen.getByText('Завантажити модель')).toBeInTheDocument()
  })
})

