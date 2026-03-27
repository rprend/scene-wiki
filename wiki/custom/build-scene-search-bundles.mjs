import { mkdir } from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { build } from "esbuild"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(__dirname, "..")
const outputDir = path.resolve(process.argv[2] || path.join(projectRoot, "..", "dist", "wiki"))

await mkdir(path.join(outputDir, "static"), { recursive: true })

await build({
  entryPoints: [path.join(__dirname, "scene-search-worker.ts")],
  outfile: path.join(outputDir, "_worker.js"),
  bundle: true,
  format: "esm",
  platform: "browser",
  target: ["es2022"],
  sourcemap: false,
  minify: true,
  jsx: "automatic",
  logLevel: "info",
})

await build({
  entryPoints: [path.join(__dirname, "scene-search-app.tsx")],
  outfile: path.join(outputDir, "static", "scene-search-app.js"),
  bundle: true,
  format: "esm",
  platform: "browser",
  target: ["es2022"],
  sourcemap: false,
  minify: true,
  jsx: "automatic",
  logLevel: "info",
})
