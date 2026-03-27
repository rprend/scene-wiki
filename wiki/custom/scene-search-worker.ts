import { createGoogleGenerativeAI } from "@ai-sdk/google"
import { createAlphaloopHandler } from "alphaloop/handler"

type SearchEntity = {
  title: string
  category: string
  category_title: string
  mention_count: number
  issue_count: number
  blurb: string
  external_links: string[]
  backlinks: SearchRelatedPage[]
}

type SearchRelatedPage = {
  path: string
  title: string
  kind: string
  reason: string
}

type SearchChunk = {
  chunk_id: string
  issue_path: string
  issue_title: string
  published_at?: string
  published_at_human?: string
  source_url?: string
  primary_text: string
  entity_paths: string[]
  embedding: number[]
}

type SearchIndex = {
  meta: {
    model: string
    dimensions: number
    query_task_type: string
  }
  entities: Record<string, SearchEntity>
  issues: Record<string, { title: string; published_at?: string; published_at_human?: string; url?: string }>
  chunks: SearchChunk[]
}

type Env = {
  ASSETS: {
    fetch(input: Request | URL | string): Promise<Response>
  }
  GEMINI_API_KEY?: string
  GOOGLE_API_KEY?: string
}

const SEARCH_MODEL = "gemini-2.5-flash"
const MAX_CONTEXT_ENTITIES = 4
const MAX_RELATED_PAGES = 12

let indexPromise: Promise<SearchIndex> | undefined

function getApiKey(env: Env): string {
  const apiKey = env.GOOGLE_API_KEY || env.GEMINI_API_KEY
  if (!apiKey) {
    throw new Error("GOOGLE_API_KEY or GEMINI_API_KEY is not configured for semantic search.")
  }
  return apiKey
}

async function getIndex(env: Env, request: Request): Promise<SearchIndex> {
  if (!indexPromise) {
    indexPromise = env.ASSETS.fetch(new URL("/scene-search-index.json", request.url)).then(
      async (response: Response) => {
        if (!response.ok) {
          throw new Error(`Search index unavailable (${response.status})`)
        }
        return (await response.json()) as SearchIndex
      },
    )
  }
  return await indexPromise
}

function cosineSimilarity(a: number[], b: number[]): number {
  let dot = 0
  let magA = 0
  let magB = 0
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i]
    magA += a[i] * a[i]
    magB += b[i] * b[i]
  }
  if (!magA || !magB) return 0
  return dot / (Math.sqrt(magA) * Math.sqrt(magB))
}

function normalizeVector(values: number[]): number[] {
  let mag = 0
  for (const value of values) {
    mag += value * value
  }
  const norm = Math.sqrt(mag) || 1
  return values.map((value) => value / norm)
}

async function embedQuery(query: string, env: Env, index: SearchIndex): Promise<number[]> {
  const apiKey = getApiKey(env)
  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${index.meta.model}:embedContent`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-goog-api-key": apiKey,
      },
      body: JSON.stringify({
        model: `models/${index.meta.model}`,
        content: {
          parts: [{ text: query }],
        },
        taskType: index.meta.query_task_type,
        outputDimensionality: index.meta.dimensions,
      }),
    },
  )

  if (!response.ok) {
    throw new Error(`Query embedding failed: ${response.status} ${await response.text()}`)
  }

  const payload = await response.json()
  const values = payload?.embedding?.values
  if (!Array.isArray(values) || values.length === 0) {
    throw new Error("Query embedding response was missing vector values.")
  }

  return normalizeVector(values.map((value: number) => Number(value)))
}

function buildRelatedPages(issuePath: string, issueTitle: string, entityCards: Array<SearchEntity & { path: string }>) {
  const relatedPages: SearchRelatedPage[] = [
    {
      path: issuePath,
      title: issueTitle,
      kind: "issue",
      reason: "Primary issue page",
    },
  ]
  const seen = new Set(relatedPages.map((page) => page.path))

  for (const entity of entityCards) {
    if (!seen.has(entity.path)) {
      relatedPages.push({
        path: entity.path,
        title: entity.title,
        kind: "entity",
        reason: `${entity.category_title} article`,
      })
      seen.add(entity.path)
    }
    for (const backlink of entity.backlinks) {
      if (seen.has(backlink.path)) continue
      relatedPages.push(backlink)
      seen.add(backlink.path)
      if (relatedPages.length >= MAX_RELATED_PAGES) {
        return relatedPages
      }
    }
    if (relatedPages.length >= MAX_RELATED_PAGES) {
      return relatedPages
    }
  }

  return relatedPages
}

function buildChunkText(chunk: SearchChunk, entityCards: Array<SearchEntity & { path: string }>): string {
  const lines = [
    `Issue: ${chunk.issue_title}`,
    `Issue page: [${chunk.issue_title}](/${chunk.issue_path.replace(/^\/+/, "")})`,
  ]
  if (chunk.published_at_human) {
    lines.push(`Published: ${chunk.published_at_human}`)
  }
  if (chunk.source_url) {
    lines.push(`Original source: [Open original source](${chunk.source_url})`)
  }
  lines.push("", "Primary passage:", chunk.primary_text)

  if (entityCards.length > 0) {
    lines.push("", "Referenced pages:")
    for (const entity of entityCards) {
      lines.push(
        `- [${entity.title}](/${entity.path.replace(/^\/+/, "")}) [${entity.category_title}] — ${entity.blurb}`,
      )
      if (entity.external_links.length > 0) {
        lines.push(
          `  External links: ${entity.external_links
            .slice(0, 3)
            .map((link, index) => `[External ${index + 1}](${link})`)
            .join(", ")}`,
        )
      }
      const backlinks = entity.backlinks
        .slice(0, 4)
        .map((item) => `[${item.title}](/${item.path.replace(/^\/+/, "")})`)
      if (backlinks.length > 0) {
        lines.push(`  Related pages: ${backlinks.join(", ")}`)
      }
    }
  }

  lines.push(
    "",
    "Answer requirements:",
    "- Use markdown headings and bullets.",
    "- For every substantive point, include at least one short verbatim quote from the primary passage using markdown blockquote syntax.",
    "- Link issue pages, entity pages, and original sources with markdown links when available.",
    "- Prefer issue/entity pages explicitly present in this retrieved context. Do not invent links.",
  )

  return lines.join("\n").trim()
}

function buildEntityCards(index: SearchIndex, chunk: SearchChunk): Array<SearchEntity & { path: string }> {
  return chunk.entity_paths
    .slice(0, MAX_CONTEXT_ENTITIES)
    .map((path) => {
      const entity = index.entities[path]
      return entity ? { path, ...entity } : null
    })
    .filter((entity): entity is SearchEntity & { path: string } => entity !== null)
}

export default {
  async fetch(request: Request, env: Env) {
    const url = new URL(request.url)
    if (url.pathname !== "/api/search") {
      return env.ASSETS.fetch(request)
    }

    const provider = createGoogleGenerativeAI({ apiKey: getApiKey(env) })
    const handler = createAlphaloopHandler({
      model: provider(SEARCH_MODEL),
      rerankModel: provider(SEARCH_MODEL),
      initialTopK: 24,
      maxExpandedQueries: 6,
      maxIterations: 2,
      relevanceThreshold: 0.35,
      systemPrompt:
        "You are analyzing a Substack scene archive. Always use deep_search first. Answer in clean markdown with short sections and bullets. Every important claim must be grounded with a short verbatim quotation from the retrieved primary text using blockquote syntax. Include markdown links to the relevant issue pages, entity/article pages, and original source URLs whenever they are present in the retrieved context. Prefer concise synthesis over repetition, but preserve concrete source wording in the quotations. Do not invent links or citations.",
      search: async (query: string, { topK }: { topK: number }) => {
        const index = await getIndex(env, request)
        const queryEmbedding = await embedQuery(query, env, index)

        return index.chunks
          .map((chunk) => {
            const entityCards = buildEntityCards(index, chunk)
            return {
              id: chunk.chunk_id,
              text: buildChunkText(chunk, entityCards),
              score: cosineSimilarity(queryEmbedding, chunk.embedding),
              metadata: {
                issuePath: chunk.issue_path,
                issueTitle: chunk.issue_title,
                publishedAt: chunk.published_at,
                publishedAtHuman: chunk.published_at_human,
                sourceUrl: chunk.source_url,
                primaryText: chunk.primary_text,
                entityCards,
                relatedPages: buildRelatedPages(chunk.issue_path, chunk.issue_title, entityCards),
              },
            }
          })
          .sort((left, right) => right.score - left.score)
          .slice(0, topK)
      },
    })

    return handler(request)
  },
}
