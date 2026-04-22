import { createServer } from "node:http"
import { readFile } from "node:fs/promises"
import path from "node:path"
import { pathToFileURL } from "node:url"

const [, , assetDirArg, portArg] = process.argv

if (!assetDirArg) {
  console.error("Usage: node scripts/run_local_scene_search_sidecar.mjs <asset-dir> [port]")
  process.exit(1)
}

const assetDir = path.resolve(assetDirArg)
const port = Number(portArg || 8091)

const MIME_TYPES = new Map([
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".txt", "text/plain; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
])

function withCors(response) {
  const headers = new Headers(response.headers)
  headers.set("Access-Control-Allow-Origin", "*")
  headers.set("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
  headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization")
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  })
}

function guessContentType(filePath) {
  return MIME_TYPES.get(path.extname(filePath).toLowerCase()) || "application/octet-stream"
}

async function serveStaticFile(filePath) {
  try {
    const body = await readFile(filePath)
    return new Response(body, {
      status: 200,
      headers: {
        "content-type": guessContentType(filePath),
      },
    })
  } catch {
    return new Response("Not found", { status: 404 })
  }
}

const workerModulePath = path.join(assetDir, "_worker.js")
const workerModule = await import(pathToFileURL(workerModulePath).href)
const worker = workerModule.default

const env = {
  OPENAI_API_KEY: process.env.OPENAI_API_KEY,
  ASSETS: {
    async fetch(input) {
      const url = typeof input === "string" ? new URL(input) : input instanceof URL ? input : new URL(input.url)
      const pathname = decodeURIComponent(url.pathname.replace(/^\/+/, ""))
      const filePath = path.join(assetDir, pathname)
      return await serveStaticFile(filePath)
    },
  },
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://localhost:${port}`)

    if (req.method === "OPTIONS") {
      const response = withCors(new Response(null, { status: 204 }))
      res.writeHead(response.status, Object.fromEntries(response.headers.entries()))
      res.end()
      return
    }

    if (url.pathname === "/api/search") {
      const chunks = []
      for await (const chunk of req) {
        chunks.push(chunk)
      }
      const body = chunks.length > 0 ? Buffer.concat(chunks) : undefined
      const request = new Request(url, {
        method: req.method,
        headers: req.headers,
        body,
      })
      const response = withCors(await worker.fetch(request, env))
      res.writeHead(response.status, Object.fromEntries(response.headers.entries()))
      const responseBody = Buffer.from(await response.arrayBuffer())
      res.end(responseBody)
      return
    }

    const pathname = decodeURIComponent(url.pathname === "/" ? "/scene-search-app.js" : url.pathname)
    const response = withCors(await serveStaticFile(path.join(assetDir, pathname.replace(/^\/+/, ""))))
    res.writeHead(response.status, Object.fromEntries(response.headers.entries()))
    const responseBody = Buffer.from(await response.arrayBuffer())
    res.end(responseBody)
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error)
    res.writeHead(500, {
      "content-type": "text/plain; charset=utf-8",
      "access-control-allow-origin": "*",
    })
    res.end(message)
  }
})

server.listen(port, () => {
  console.log(`Local scene-search sidecar listening on http://localhost:${port}`)
  console.log(`Serving assets from ${assetDir}`)
})
