declare module "alphaloop/handler" {
  export function createAlphaloopHandler(config: any): (request: Request) => Promise<Response>
}

declare module "alphaloop/react" {
  import { FC } from "react"

  export type SearchProgressEvent = {
    type: string
    query?: string
    chunksFound?: number
    queries?: string[]
    newChunksFound?: number
    totalUnique?: number
    totalChunks?: number
    keptChunks?: number
    droppedChunks?: number
    iteration?: number
    newQueries?: string[]
  }

  export type SearchChunk = {
    id: string
    text: string
    relevance: number
    rationale?: string
    metadata?: Record<string, unknown>
  }

  export const SearchProgress: FC<{
    events: SearchProgressEvent[]
    isRunning: boolean
  }>

  export const Citations: FC<{
    chunks: SearchChunk[]
    getSourceUrl?: (chunk: SearchChunk) => string | undefined
  }>
}
