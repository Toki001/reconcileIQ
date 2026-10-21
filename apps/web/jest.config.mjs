import nextJest from "next/jest.js";

const createJestConfig = nextJest({ dir: "./" });
export default createJestConfig({
  testEnvironment: "jsdom",
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  moduleNameMapper: { "^@/(.*)$": "<rootDir>/src/$1" },
  testMatch: ["**/*.test.ts", "**/*.test.tsx"],
  modulePathIgnorePatterns: ["<rootDir>/.next/"],
});
