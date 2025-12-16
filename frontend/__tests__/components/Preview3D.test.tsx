/**
 * @jest-environment jsdom
 */
import { render, screen } from '@testing-library/react'
import { Preview3D } from '@/components/Preview3D'

// Mock @react-three/fiber
jest.mock('@react-three/fiber', () => ({
  Canvas: ({ children }: any) => <div data-testid="canvas">{children}</div>,
  useFrame: jest.fn(),
  useThree: jest.fn(),
}))

// Mock @react-three/drei
jest.mock('@react-three/drei', () => ({
  OrbitControls: () => null,
  PerspectiveCamera: () => null,
}))

// Suppress Three.js warnings in tests
const originalError = console.error
beforeAll(() => {
  console.error = (...args: any[]) => {
    if (
      typeof args[0] === 'string' &&
      (args[0].includes('Warning:') || args[0].includes('The tag'))
    ) {
      return
    }
    originalError.call(console, ...args)
  }
})

afterAll(() => {
  console.error = originalError
})

describe('Preview3D', () => {
  it('should render 3D preview', () => {
    render(<Preview3D />)
    
    expect(screen.getByTestId('canvas')).toBeInTheDocument()
  })

  it('should have correct styling', () => {
    const { container } = render(<Preview3D />)
    const preview = container.firstChild
    
    expect(preview).toHaveClass('w-full', 'h-full', 'bg-gray-900')
  })
})

