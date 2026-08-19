import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        amul: {
          red: "#ED1C24",
          "red-dark": "#C8151C",
          blue: "#00397A",
          "blue-dark": "#002655",
        },
      },
      keyframes: {
        "slide-up": {
          "0%": { transform: "translateY(0.75rem)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      animation: {
        "slide-up": "slide-up 0.2s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
