// Build/serve the real React app with synthetic public config; never read .env or live credentials.
import { build, preview } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
const config = {
  root, configFile: false, envDir: false, envPrefix: "__OFFLINE_DISABLED__", plugins: [react()],
  define: {
    "import.meta.env.VITE_APP_MODE": JSON.stringify("beta"),
    "import.meta.env.VITE_SUPABASE_URL": JSON.stringify("https://pursuit-ui-test.supabase.co"),
    "import.meta.env.VITE_SUPABASE_ANON_KEY": JSON.stringify("offline-public-key-not-a-secret"),
  },
  build: { outDir: "node_modules/.cache/launch-web-workflow", emptyOutDir: true },
  preview: { host: "127.0.0.1", port: 5187, strictPort: true },
};
if (process.argv.includes("--serve")) { const server = await preview(config); server.printUrls(); }
else await build(config);
