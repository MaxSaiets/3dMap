const nextJest = require('next/jest')

const createJestConfig = nextJest({
  // Provide the path to your Next.js app to load next.config.js and .env files in your test environment
  dir: './',
})

// Add any custom config to be passed to Jest
const customJestConfig = {
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  testEnvironment: 'jest-environment-jsdom',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
    // three/examples is ESM and Jest (CJS) can choke on it; map loaders to simple stubs for unit tests
    '^three/examples/jsm/loaders/STLLoader(\\.js)?$': '<rootDir>/__tests__/stubs/STLLoader.js',
    '^three/examples/jsm/loaders/3MFLoader(\\.js)?$': '<rootDir>/__tests__/stubs/ThreeMFLoader.js',
  },
  // Do not treat helper/mocks as test suites
  testPathIgnorePatterns: ['<rootDir>/__tests__/__mocks__/', '<rootDir>/__tests__/stubs/'],
  collectCoverageFrom: [
    'components/**/*.{js,jsx,ts,tsx}',
    'lib/**/*.{js,jsx,ts,tsx}',
    'store/**/*.{js,jsx,ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
  ],
}

// createJestConfig is exported this way to ensure that next/jest can load the Next.js config which is async
module.exports = createJestConfig(customJestConfig)

