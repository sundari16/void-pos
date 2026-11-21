// Expo's flat ESLint config. https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require("eslint/config");
const expoConfig = require("eslint-config-expo/flat");

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ["dist/*", "node_modules/*", ".expo/*"],
  },
  {
    // Jest's globals in the plain-JS test and setup files (the TypeScript ones get them from @types/jest).
    files: ["**/*.test.js", "jest.setup.js"],
    languageOptions: {
      globals: { jest: "readonly", describe: "readonly", it: "readonly", expect: "readonly" },
    },
  },
  {
    // The screens load their data (menu, closeout) on mount, which sets state from an effect.
    // That's the intended pattern here; keep it a warning rather than contort a simple fetch.
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "react-hooks/set-state-in-effect": "warn",
    },
  },
]);
