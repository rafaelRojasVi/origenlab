import js from "@eslint/js";
import tsEslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

/** @type {import("typescript-eslint").Config} */
export default tsEslint.config(
  // Base JavaScript recommended rules
  js.configs.recommended,

  // TypeScript-aware rules for all .ts/.tsx source files
  ...tsEslint.configs.recommended,

  // React Hooks rules (rules-of-hooks: error, exhaustive-deps: warn)
  {
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      // Allow intentional _-prefixed unused vars (standard convention in this codebase)
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          "varsIgnorePattern": "^_",
          "argsIgnorePattern": "^_",
          "destructuredArrayIgnorePattern": "^_",
          "caughtErrorsIgnorePattern": "^_",
        },
      ],
    },
  },

  // Ignore build artefacts and config
  {
    ignores: ["dist/**", "node_modules/**", "*.config.*"],
  },
);
