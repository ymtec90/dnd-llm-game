import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173
  },
  // @ts-ignore - Vitest types
  test: {
    environment: "happy-dom",
    globals: true
  }
});
