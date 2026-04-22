import { mkdir, readFile, writeFile } from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { build } from "esbuild"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(__dirname, "..")
const outputDir = path.resolve(process.argv[2] || path.join(projectRoot, "..", "dist", "wiki"))
const alphaloopBundlePath = path.join(projectRoot, "node_modules", "alphaloop", "dist", "chunk-O7BEO4SI.js")

async function patchAlphaloopSchema() {
  const source = await readFile(alphaloopBundlePath, "utf-8")
  const next = source
    .replace("rationale: z2.string().optional()", "rationale: z2.string()")
    .replace("rationale: z4.string().optional()", "rationale: z4.string()")

  if (next !== source) {
    await writeFile(alphaloopBundlePath, next, "utf-8")
  }
}

await mkdir(path.join(outputDir, "static"), { recursive: true })
await patchAlphaloopSchema()

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
