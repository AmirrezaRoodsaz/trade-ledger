/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        surface2: "var(--surface-2)",
        line: "var(--border)",
        ink: "var(--text)",
        muted: "var(--muted)",
        accent: "var(--accent)",
        pos: "var(--pos)",
        neg: "var(--neg)",
        live: "var(--live)",
        paper: "var(--paper)",
        demo: "var(--demo)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      // 8-px grid: the spacing values used across the app are multiples of 8
      // (Tailwind's 2/4/6/8/... = 8/16/24/32 px), so no extra scale is needed.
      borderRadius: { DEFAULT: "4px", md: "6px" },
    },
  },
  plugins: [],
};
