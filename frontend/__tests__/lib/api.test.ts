/**
 * @jest-environment jsdom
 */
import { api } from '@/lib/api'
import axios from 'axios'

jest.mock('axios')
const mockedAxios = axios as jest.Mocked<typeof axios>

describe('API Client', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe('generateModel', () => {
    it('should call generate endpoint with correct data', async () => {
      const mockResponse = {
        task_id: 'test-task-id',
        status: 'processing',
      }
      
      mockedAxios.post.mockResolvedValue({ data: mockResponse })

      const request = {
        north: 50.455,
        south: 50.450,
        east: 30.530,
        west: 30.520,
        road_width_multiplier: 1.0,
        building_min_height: 2.0,
        building_height_multiplier: 1.0,
        water_depth: 2.0,
        terrain_enabled: true,
        terrain_z_scale: 1.5,
        export_format: '3mf' as const,
      }

      const result = await api.generateModel(request)

      expect(mockedAxios.post).toHaveBeenCalledWith(
        expect.stringContaining('/api/generate'),
        request
      )
      expect(result).toEqual(mockResponse)
    })
  })

  describe('getStatus', () => {
    it('should call status endpoint with task id', async () => {
      const mockResponse = {
        task_id: 'test-task-id',
        status: 'processing',
        progress: 50,
        message: 'Обробка...',
        download_url: null,
      }
      
      mockedAxios.get.mockResolvedValue({ data: mockResponse })

      const result = await api.getStatus('test-task-id')

      expect(mockedAxios.get).toHaveBeenCalledWith(
        expect.stringContaining('/api/status/test-task-id')
      )
      expect(result).toEqual(mockResponse)
    })
  })

  describe('downloadModel', () => {
    it('should download model file', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/octet-stream' })
      
      mockedAxios.get.mockResolvedValue({ data: mockBlob })

      const result = await api.downloadModel('test-task-id')

      expect(mockedAxios.get).toHaveBeenCalledWith(
        expect.stringContaining('/api/download/test-task-id'),
        { responseType: 'blob' }
      )
      expect(result).toBeInstanceOf(Blob)
    })

    it('should download model file with format query', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/octet-stream' })
      mockedAxios.get.mockResolvedValue({ data: mockBlob })

      await api.downloadModel('test-task-id', 'stl')

      expect(mockedAxios.get).toHaveBeenCalledWith(
        expect.stringContaining('/api/download/test-task-id?format=stl'),
        { responseType: 'blob' }
      )
    })

    it('should download model file with format and part query', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/octet-stream' })
      mockedAxios.get.mockResolvedValue({ data: mockBlob })

      await api.downloadModel('test-task-id', 'stl', 'roads')

      expect(mockedAxios.get).toHaveBeenCalledWith(
        expect.stringContaining('/api/download/test-task-id?format=stl&part=roads'),
        { responseType: 'blob' }
      )
    })
  })
})

