import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "rgb(var(--surface-rgb) / <alpha-value>)", raised: "var(--raised)", line: "rgb(var(--line-rgb) / <alpha-value>)",
        ink: "var(--ink)", ink2: "var(--ink2)", muted: "var(--muted)",
        brand: "rgb(var(--brand-rgb) / <alpha-value>)", good: "rgb(var(--good-rgb) / <alpha-value>)", warn: "rgb(var(--warn-rgb) / <alpha-value>)", serious: "rgb(var(--serious-rgb) / <alpha-value>)", crit: "rgb(var(--crit-rgb) / <alpha-value>)",
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] },
    },
  },
  plugins: [],
};
export default config;
