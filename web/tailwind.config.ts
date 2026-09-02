import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: {
          DEFAULT: "#0a0e16",
          panel: "#0f1420",
          raised: "#141a28",
        },
        border: {
          DEFAULT: "rgba(148, 163, 184, 0.12)",
          strong: "rgba(148, 163, 184, 0.22)",
        },
        ink: {
          DEFAULT: "#e2e8f0",
          muted: "#7d8aa3",
          faint: "#4b5670",
        },
        accent: {
          cyan: "#22d3ee",
          teal: "#2dd4bf",
        },
        warn: "#f5a524",
        danger: "#f43f5e",
        altitude: {
          low: "#2dd4bf",
          mid: "#22d3ee",
          high: "#6366f1",
          extreme: "#a78bfa",
        },
      },
      fontFamily: {
        sans: ["var(--font-plex)", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      backdropBlur: {
        panel: "12px",
      },
      boxShadow: {
        panel: "0 4px 24px rgba(0, 0, 0, 0.35)",
      },
      keyframes: {
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(244, 63, 94, 0.55)" },
          "100%": { boxShadow: "0 0 0 10px rgba(244, 63, 94, 0)" },
        },
      },
      animation: {
        "pulse-ring": "pulse-ring 1.8s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [],
};

export default config;
