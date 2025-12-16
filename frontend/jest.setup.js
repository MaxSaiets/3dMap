// Learn more: https://github.com/testing-library/jest-dom
import '@testing-library/jest-dom'

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(), // deprecated
    removeListener: jest.fn(), // deprecated
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
})

// Mock Leaflet
jest.mock('leaflet', () => ({
  map: jest.fn(),
  tileLayer: jest.fn(),
  icon: jest.fn(),
  Marker: jest.fn(),
  Control: {
    Draw: jest.fn(),
  },
  Draw: {
    Event: {
      CREATED: 'draw:created',
      EDITED: 'draw:edited',
      DELETED: 'draw:deleted',
    },
  },
}))

// Mock react-leaflet
jest.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map-container">{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
  useMap: () => ({
    on: jest.fn(),
    off: jest.fn(),
    addLayer: jest.fn(),
    addControl: jest.fn(),
    removeControl: jest.fn(),
  }),
}))

