import js from "@eslint/js";
import tsEslint from "typescript-eslint";

/** @type {import("typescript-eslint").Config} */
export default tsEslint.config(
  // Base JavaScript recommended rules
  js.configs.recommended,

  // TypeScript-aware rules
  ...tsEslint.configs.recommended,

  {
    rules: {
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
      // Cloudflare Worker code legitimately uses `any` for untyped request/response fields
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },

  // Ignore build artefacts and config
  {
    ignores: ["dist/**", "node_modules/**", "*.config.*"],
  },
);
