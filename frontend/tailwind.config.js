/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        maroon: { DEFAULT: "#4a1942", light: "#6b2760", deep: "#33112e" },
        gold: { DEFAULT: "#c9a227", light: "#e8c547" },
        cream: "#faf8f3",
        ink: "#1a1520",
        muted: "#6b6070",
        border: "#e8e2ef",
      },
      fontFamily: {
        display: ["Syne", "sans-serif"],
        body: ["DM Sans", "sans-serif"],
        mono: ["IBM Plex Mono", "monospace"],
      },
      borderRadius: {
        chat: "18px",
      },
      boxShadow: {
        card: "0 2px 12px rgba(74, 25, 66, 0.12)",
      },
    },
  },
  plugins: [],
};
