import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface)", raised: "var(--raised)", line: "var(--line)",
        ink: "var(--ink)", ink2: "var(--ink2)", muted: "var(--muted)",
        brand: "var(--s1)", good: "var(--good)", warn: "var(--warn)", serious: "var(--serious)", crit: "var(--crit)",
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] },
    },
  },
  plugins: [],
};
export default config;
