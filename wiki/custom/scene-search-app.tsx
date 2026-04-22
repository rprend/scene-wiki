/** @jsxImportSource react */
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { AssistantRuntimeProvider } from "@assistant-ui/core/react"
import { AssistantChatTransport, useChatRuntime } from "@assistant-ui/react-ai-sdk"
import { Citations, SearchProgress } from "alphaloop/react"
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createRoot, Root } from "react-dom/client"

type SearchProgressEvent = {
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

type RelatedPage = {
  path: string
  title: string
  kind: string
  reason: string
}

type SearchChunk = {
  id: string
  text: string
  relevance: number
  metadata?: {
    issuePath: string
    issueTitle: string
    publishedAtHuman?: string
    sourceUrl?: string
    primaryText?: string
    relatedPages?: RelatedPage[]
  }
}

type StoredThread = {
  id: string
  title: string
  query: string
  repository: unknown | null
  progressEvents: SearchProgressEvent[]
  updatedAt: number
}

type PersistedThreads = {
  activeThreadId: string
  threads: StoredThread[]
}

const THREAD_STORAGE_KEY = "scene-wiki-search-threads-v1"
const SIDEBAR_STYLE_ID = "scene-wiki-search-sidebar-styles"
const SUGGESTIONS = [
  {
    title: "Founder dinners",
    prompt: "who keeps hosting founder dinners in Palo Alto",
  },
  {
    title: "AI labs",
    prompt: "which AI labs and studios show up most often",
  },
  {
    title: "Neighborhoods",
    prompt: "which neighborhoods around San Francisco keep appearing",
  },
  {
    title: "Investors",
    prompt: "which investors and venture firms are mentioned most",
  },
]

const MEDIAWIKI_SIDEBAR_STYLES = `
.semantic-search-sidebar-toggle {
  position: fixed;
  top: 6rem;
  right: 1rem;
  z-index: 1001;
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  border: 1px solid rgba(17, 24, 39, 0.12);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  color: #111827;
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.14);
  backdrop-filter: blur(16px);
  padding: 0.65rem 0.95rem;
  font: inherit;
  font-size: 0.88rem;
  font-weight: 600;
  cursor: pointer;
}

.semantic-search-sidebar-toggle:hover {
  transform: translateY(-1px);
}

.semantic-search-sidebar-toggle svg {
  width: 1rem;
  height: 1rem;
}

.semantic-search-backdrop {
  position: fixed;
  inset: 0;
  z-index: 999;
  background: rgba(15, 23, 42, 0.18);
  opacity: 0;
  pointer-events: none;
  transition: opacity 160ms ease;
}

.semantic-search-backdrop-open {
  opacity: 1;
  pointer-events: auto;
}

.semantic-search-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(28rem, calc(100vw - 1rem));
  z-index: 1000;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.98);
  border-left: 1px solid rgba(17, 24, 39, 0.08);
  box-shadow: -22px 0 60px rgba(15, 23, 42, 0.18);
  transform: translateX(100%);
  transition: transform 180ms ease;
  backdrop-filter: blur(12px);
}

.semantic-search-drawer-open {
  transform: translateX(0);
}

.semantic-search-drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.95rem 1rem 0.85rem;
  border-bottom: 1px solid rgba(17, 24, 39, 0.08);
}

.semantic-search-drawer-head-copy {
  display: grid;
  gap: 0.18rem;
}

.semantic-search-drawer-kicker {
  margin: 0;
  color: #6b7280;
  font-size: 0.7rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.semantic-search-drawer-title {
  margin: 0;
  color: #111827;
  font-size: 1rem;
  font-weight: 700;
}

.semantic-search-drawer-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.2rem;
  height: 2.2rem;
  border: 1px solid rgba(17, 24, 39, 0.08);
  border-radius: 999px;
  background: transparent;
  color: #111827;
  font: inherit;
  cursor: pointer;
}

.semantic-search-sidebar-shell {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
  background: transparent;
}

.semantic-search-thread-nav {
  display: grid;
  gap: 0.8rem;
  border-bottom: 1px solid rgba(17, 24, 39, 0.08);
  padding: 0.9rem 1rem 0.8rem;
  background: transparent;
}

.semantic-search-thread-nav-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
}

.semantic-search-thread-nav-head h2 {
  margin: 0;
  color: #111827;
  font-size: 0.95rem;
}

.semantic-search-new-chat,
.semantic-search-thread-pill,
.semantic-search-related-pill,
.semantic-search-suggestion,
.semantic-search-composer,
.semantic-search-composer-button,
.semantic-search-user-bubble,
.semantic-search-drawer-close {
  box-sizing: border-box;
}

.semantic-search-new-chat {
  border: 1px solid rgba(17, 24, 39, 0.1);
  border-radius: 999px;
  background: transparent;
  color: #111827;
  font: inherit;
  font-size: 0.84rem;
  padding: 0.35rem 0.72rem;
  cursor: pointer;
}

.semantic-search-thread-pills {
  display: flex;
  gap: 0.45rem;
  overflow-x: auto;
  padding-bottom: 0.05rem;
  scrollbar-width: none;
}

.semantic-search-thread-pills::-webkit-scrollbar {
  display: none;
}

.semantic-search-thread-pill {
  flex: 0 0 auto;
  border: 1px solid rgba(17, 24, 39, 0.1);
  border-radius: 999px;
  background: transparent;
  color: #4b5563;
  font: inherit;
  font-size: 0.82rem;
  padding: 0.45rem 0.78rem;
  cursor: pointer;
}

.semantic-search-thread-pill-active {
  background: rgba(17, 24, 39, 0.92);
  border-color: rgba(17, 24, 39, 0.92);
  color: white;
}

.semantic-search-thread {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
}

.semantic-search-thread-viewport {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
}

.semantic-search-thread-messages {
  display: grid;
  align-content: start;
  padding-bottom: 0.65rem;
}

.semantic-search-thread-footer {
  margin-top: auto;
  position: sticky;
  bottom: 0;
  padding: 0.75rem 1rem 1rem;
  background: rgba(255, 255, 255, 0.98);
  border-top: 1px solid rgba(17, 24, 39, 0.08);
}

.semantic-search-welcome {
  display: grid;
  gap: 0.55rem;
  padding: 0.85rem 1rem 0;
}

.semantic-search-suggestions {
  display: grid;
  gap: 0.35rem;
}

.semantic-search-suggestion {
  display: grid;
  gap: 0.12rem;
  border: 1px solid transparent;
  border-radius: 0.9rem;
  background: transparent;
  padding: 0.55rem 0;
  text-align: left;
  cursor: pointer;
}

.semantic-search-suggestion:hover {
  border-color: rgba(17, 24, 39, 0.12);
}

.semantic-search-suggestion span {
  font-size: 0.94rem;
  font-weight: 600;
  color: #111827;
}

.semantic-search-suggestion small {
  color: #6b7280;
  font-size: 0.85rem;
}

.semantic-search-message {
  padding: 0.85rem 1rem 0;
}

.semantic-search-message-user {
  display: flex;
  justify-content: flex-end;
}

.semantic-search-user-bubble {
  max-width: min(86%, 24rem);
  border-radius: 1.4rem 1.4rem 0.5rem 1.4rem;
  background: rgba(17, 24, 39, 0.96);
  color: white;
  padding: 0.8rem 1rem;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
}

.semantic-search-user-bubble p {
  margin: 0;
  color: inherit;
}

.semantic-search-assistant-column {
  display: grid;
  gap: 0.95rem;
}

.semantic-search-answer-body,
.semantic-search-answer-body p,
.semantic-search-answer-body h2,
.semantic-search-answer-body h3,
.semantic-search-answer-body h4,
.semantic-search-answer-body ol,
.semantic-search-answer-body ul,
.semantic-search-answer-body li {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.semantic-search-answer-body {
  color: #111827;
}

.semantic-search-answer-body a,
.semantic-search-related-pill {
  color: #1d4ed8;
}

.semantic-search-answer-body > :first-child {
  margin-top: 0;
}

.semantic-search-answer-body > :last-child {
  margin-bottom: 0;
}

.semantic-search-answer-body h2 {
  font-size: 1rem;
}

.semantic-search-answer-body h3,
.semantic-search-answer-body h4 {
  font-size: 0.95rem;
}

.semantic-search-answer-body blockquote {
  margin: 0.65rem 0;
  padding-left: 0.85rem;
  border-left: 2px solid rgba(17, 24, 39, 0.18);
}

.semantic-search-alphaloop {
  display: grid;
  gap: 0.8rem;
  padding: 0.2rem 0 0;
}

.semantic-search-related-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.55rem;
}

.semantic-search-related-pill {
  border-radius: 999px;
  background: transparent;
  border: 1px solid rgba(17, 24, 39, 0.1);
  padding: 0.32rem 0.7rem;
  font-size: 0.84rem;
  text-decoration: none;
}

.semantic-search-composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
  border: 1px solid rgba(17, 24, 39, 0.12);
  border-radius: 2rem;
  background: white;
  padding: 0.45rem 0.5rem 0.45rem 0.8rem;
}

.semantic-search-composer-input {
  width: 100%;
  min-height: 1.9rem;
  max-height: 6rem;
  resize: none;
  border: 0;
  background: transparent;
  padding: 0.1rem 0;
  font: inherit;
  font-size: 0.96rem;
  line-height: 1.35;
  box-sizing: border-box;
  outline: none;
  box-shadow: none;
  appearance: none;
}

.semantic-search-composer-input::placeholder {
  color: #9ca3af;
}

.semantic-search-composer-actions {
  display: flex;
  justify-content: flex-end;
}

.semantic-search-composer-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.35rem;
  height: 2.35rem;
  border: 1px solid transparent;
  border-radius: 999px;
  background: #111827;
  color: white;
  font: inherit;
  cursor: pointer;
  flex: 0 0 auto;
}

.semantic-search-composer-button svg {
  width: 0.95rem;
  height: 0.95rem;
}

@media (max-width: 900px) {
  .semantic-search-sidebar-toggle {
    top: auto;
    right: 1rem;
    bottom: 1rem;
  }

  .semantic-search-drawer {
    width: min(100vw, 32rem);
  }
}
`

function pageHref(path: string) {
  return `/${path.replace(/^\/+/, "")}`
}

function getSearchBasePath() {
  if (typeof document === "undefined") return ""
  const rootElement = document.querySelector("[data-scene-search-app]")
  const raw = rootElement?.getAttribute("data-scene-search-base")?.trim() ?? ""
  if (!raw || raw === "/") return ""
  return raw.replace(/\/+$/, "")
}

function ensureSidebarStyles() {
  if (typeof document === "undefined") return
  if (document.getElementById(SIDEBAR_STYLE_ID)) return
  const style = document.createElement("style")
  style.id = SIDEBAR_STYLE_ID
  style.textContent = MEDIAWIKI_SIDEBAR_STYLES
  document.head.appendChild(style)
}

function renderInlineMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  const pattern =
    /(\[([^\]]+)\]\(((?:https?:\/\/|\/)[^\s)]+)\))|(\*\*([^*]+)\*\*)|(\*([^*]+)\*)|(`([^`]+)`)/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }

    if (match[2] && match[3]) {
      const href = match[3]
      const isExternal = /^https?:\/\//.test(href)
      nodes.push(
        <a
          href={href}
          key={`${match.index}-link`}
          target={isExternal ? "_blank" : undefined}
          rel={isExternal ? "noreferrer" : undefined}
        >
          {match[2]}
        </a>,
      )
    } else if (match[5]) {
      nodes.push(<strong key={`${match.index}-strong`}>{match[5]}</strong>)
    } else if (match[7]) {
      nodes.push(<em key={`${match.index}-em`}>{match[7]}</em>)
    } else if (match[9]) {
      nodes.push(<code key={`${match.index}-code`}>{match[9]}</code>)
    }

    lastIndex = pattern.lastIndex
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }

  return nodes
}

function renderAnswerBlocks(answer: string): React.ReactNode[] {
  const lines = answer.replace(/\r\n/g, "\n").split("\n")
  const blocks: React.ReactNode[] = []
  let paragraphLines: string[] = []
  let listItems: string[] = []
  let orderedItems: string[] = []
  let blockquoteLines: string[] = []

  const flushParagraph = () => {
    const text = paragraphLines.join(" ").trim()
    if (!text) return
    blocks.push(<p key={`p-${blocks.length}`}>{renderInlineMarkdown(text)}</p>)
    paragraphLines = []
  }

  const flushList = () => {
    if (!listItems.length) return
    blocks.push(
      <ul key={`ul-${blocks.length}`}>
        {listItems.map((item, index) => (
          <li key={`li-${index}`}>{renderInlineMarkdown(item)}</li>
        ))}
      </ul>,
    )
    listItems = []
  }

  const flushOrderedList = () => {
    if (!orderedItems.length) return
    blocks.push(
      <ol key={`ol-${blocks.length}`}>
        {orderedItems.map((item, index) => (
          <li key={`oli-${index}`}>{renderInlineMarkdown(item)}</li>
        ))}
      </ol>,
    )
    orderedItems = []
  }

  const flushBlockquote = () => {
    if (!blockquoteLines.length) return
    blocks.push(
      <blockquote key={`bq-${blocks.length}`}>
        {blockquoteLines.map((line, index) => (
          <p key={`bqp-${index}`}>{renderInlineMarkdown(line)}</p>
        ))}
      </blockquote>,
    )
    blockquoteLines = []
  }

  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line) {
      flushParagraph()
      flushList()
      flushOrderedList()
      flushBlockquote()
      continue
    }

    const headingMatch = line.match(/^(#{2,4})\s+(.*)$/)
    if (headingMatch) {
      flushParagraph()
      flushList()
      flushOrderedList()
      flushBlockquote()
      const level = Math.min(4, headingMatch[1].length)
      const text = headingMatch[2].trim()
      if (level === 2) {
        blocks.push(<h2 key={`h2-${blocks.length}`}>{renderInlineMarkdown(text)}</h2>)
      } else if (level === 3) {
        blocks.push(<h3 key={`h3-${blocks.length}`}>{renderInlineMarkdown(text)}</h3>)
      } else {
        blocks.push(<h4 key={`h4-${blocks.length}`}>{renderInlineMarkdown(text)}</h4>)
      }
      continue
    }

    const blockquoteMatch = line.match(/^>\s?(.*)$/)
    if (blockquoteMatch) {
      flushParagraph()
      flushList()
      flushOrderedList()
      blockquoteLines.push(blockquoteMatch[1].trim())
      continue
    }

    const listMatch = line.match(/^[-*]\s+(.*)$/)
    if (listMatch) {
      flushParagraph()
      flushOrderedList()
      flushBlockquote()
      listItems.push(listMatch[1].trim())
      continue
    }

    const orderedMatch = line.match(/^\d+\.\s+(.*)$/)
    if (orderedMatch) {
      flushParagraph()
      flushList()
      flushBlockquote()
      orderedItems.push(orderedMatch[1].trim())
      continue
    }

    if (listItems.length) flushList()
    if (orderedItems.length) flushOrderedList()
    if (blockquoteLines.length) flushBlockquote()
    paragraphLines.push(line)
  }

  flushParagraph()
  flushList()
  flushOrderedList()
  flushBlockquote()

  return blocks
}

function makeThreadId() {
  return `thread-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function makeEmptyThread(): StoredThread {
  return {
    id: makeThreadId(),
    title: "New chat",
    query: "",
    repository: null,
    progressEvents: [],
    updatedAt: Date.now(),
  }
}

function loadPersistedThreads(): PersistedThreads {
  try {
    const raw = window.localStorage.getItem(THREAD_STORAGE_KEY)
    if (!raw) {
      const thread = makeEmptyThread()
      return { activeThreadId: thread.id, threads: [thread] }
    }
    const parsed = JSON.parse(raw) as PersistedThreads
    if (!parsed || !Array.isArray(parsed.threads) || !parsed.threads.length) {
      throw new Error("Invalid stored threads")
    }
    const normalizedThreads = parsed.threads
      .filter((thread) => thread && typeof thread.id === "string")
      .map((thread) => ({
        id: thread.id,
        title: typeof thread.title === "string" ? thread.title : "New chat",
        query: typeof thread.query === "string" ? thread.query : "",
        repository:
          thread.repository && typeof thread.repository === "object" ? thread.repository : null,
        progressEvents: Array.isArray(thread.progressEvents) ? thread.progressEvents : [],
        updatedAt: typeof thread.updatedAt === "number" ? thread.updatedAt : Date.now(),
      }))
    if (!normalizedThreads.length) {
      throw new Error("No valid stored threads")
    }
    const activeThreadId =
      typeof parsed.activeThreadId === "string" &&
      normalizedThreads.some((thread) => thread.id === parsed.activeThreadId)
        ? parsed.activeThreadId
        : normalizedThreads[0]!.id
    return { activeThreadId, threads: normalizedThreads }
  } catch {
    const thread = makeEmptyThread()
    return { activeThreadId: thread.id, threads: [thread] }
  }
}

function savePersistedThreads(payload: PersistedThreads) {
  try {
    window.localStorage.setItem(THREAD_STORAGE_KEY, JSON.stringify(payload))
  } catch {
    // Ignore localStorage failures.
  }
}

function getThreadTitle(query: string) {
  const trimmed = query.trim()
  if (!trimmed) return "New chat"
  return trimmed.length > 34 ? `${trimmed.slice(0, 34).trimEnd()}...` : trimmed
}

function getMessageParts(message: any): any[] {
  if (Array.isArray(message?.content)) return message.content
  if (Array.isArray(message?.parts)) return message.parts
  return []
}

function getMessageText(message: any) {
  return getMessageParts(message)
    .filter((part: any) => part.type === "text")
    .map((part: any) => part.text)
    .join("")
    .trim()
}

function getQueryFromRepository(repository: any, fallback = "") {
  const messages = Array.isArray(repository?.messages) ? repository.messages : []
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const entry = messages[index]
    const message = entry?.message
    if (message?.role !== "user") continue
    const text = getMessageText(message)
    if (text) return text
  }
  return fallback.trim()
}

function dedupeRelatedPages(chunks: SearchChunk[]): RelatedPage[] {
  const seen = new Set<string>()
  const pages: RelatedPage[] = []
  for (const chunk of chunks) {
    for (const page of chunk.metadata?.relatedPages ?? []) {
      if (seen.has(page.path)) continue
      seen.add(page.path)
      pages.push(page)
    }
  }
  return pages
}

function WelcomeScreen() {
  const aui = useAui()

  function runSuggestion(prompt: string) {
    aui.composer().setText(prompt)
    aui.composer().send()
  }

  return (
    <div className="semantic-search-welcome">
      <div className="semantic-search-suggestions">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion.prompt}
            type="button"
            className="semantic-search-suggestion"
            onClick={() => runSuggestion(suggestion.prompt)}
          >
            <span>{suggestion.title}</span>
            <small>{suggestion.prompt}</small>
          </button>
        ))}
      </div>
    </div>
  )
}

function AlphaLoopPanel({
  chunks,
  progressEvents,
  isRunning,
  showProgress,
}: {
  chunks: SearchChunk[]
  progressEvents: SearchProgressEvent[]
  isRunning: boolean
  showProgress: boolean
}) {
  const citationChunks = useMemo(
    () =>
      chunks.map((chunk) => ({
        ...chunk,
        text: chunk.metadata?.primaryText || chunk.text,
      })),
    [chunks],
  )
  const relatedPages = useMemo(() => dedupeRelatedPages(chunks), [chunks])

  if (!progressEvents.length && !chunks.length) return null

  return (
    <div className="semantic-search-alphaloop">
      {showProgress && progressEvents.length ? (
        <div className="semantic-search-alphaloop-progress">
          <SearchProgress events={progressEvents} isRunning={isRunning} />
        </div>
      ) : null}
      {chunks.length ? (
        <div className="semantic-search-alphaloop-citations">
          <Citations
            chunks={citationChunks}
            getSourceUrl={(chunk) => {
              const metadata = chunk.metadata as SearchChunk["metadata"] | undefined
              return metadata?.issuePath ? pageHref(metadata.issuePath) : undefined
            }}
          />
        </div>
      ) : null}
      {relatedPages.length ? (
        <div className="semantic-search-related-pills">
          {relatedPages.slice(0, 6).map((page) => (
            <a key={page.path} href={pageHref(page.path)} className="semantic-search-related-pill">
              {page.title}
            </a>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="semantic-search-message semantic-search-message-user">
      <div className="semantic-search-user-bubble">
        <MessagePrimitive.Parts>
          {({ part }: { part: any }) => {
            if (part.type !== "text") return null
            return <p>{part.text}</p>
          }}
        </MessagePrimitive.Parts>
      </div>
    </MessagePrimitive.Root>
  )
}

function AssistantMessage({
  progressEvents,
  isRunning,
  showProgress,
}: {
  progressEvents: SearchProgressEvent[]
  isRunning: boolean
  showProgress: boolean
}) {
  return (
    <MessagePrimitive.Root className="semantic-search-message semantic-search-message-assistant">
      <div className="semantic-search-assistant-column">
        <MessagePrimitive.Parts>
          {({ part }: { part: any }) => {
            if (part.type === "text") {
              return (
                <div className="semantic-search-answer-body">{renderAnswerBlocks(part.text)}</div>
              )
            }

            if (part.type === "tool-call" && part.toolName === "deep_search") {
              const chunks = Array.isArray(part.result?.chunks)
                ? (part.result.chunks as SearchChunk[])
                : []
              return (
                <AlphaLoopPanel
                  chunks={chunks}
                  progressEvents={progressEvents}
                  isRunning={isRunning}
                  showProgress={showProgress}
                />
              )
            }

            return null
          }}
        </MessagePrimitive.Parts>
      </div>
    </MessagePrimitive.Root>
  )
}

function SubmitIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 18V7.5M12 7.5l-4 4M12 7.5l4 4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="7.25" y="7.25" width="9.5" height="9.5" rx="1.8" fill="currentColor" />
    </svg>
  )
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M10.75 5.5a5.25 5.25 0 1 0 0 10.5 5.25 5.25 0 0 0 0-10.5ZM16 16l3.5 3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M7 7l10 10M17 7 7 17"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function ComposerBar() {
  const isRunning = useAuiState((s) => s.thread.isRunning)

  return (
    <ComposerPrimitive.Root className="semantic-search-composer">
      <ComposerPrimitive.Input
        rows={1}
        aria-label="Archive question"
        placeholder="Send a message..."
        className="semantic-search-composer-input"
      />
      <div className="semantic-search-composer-actions">
        {isRunning ? (
          <ComposerPrimitive.Cancel asChild>
            <button type="button" className="semantic-search-composer-button" aria-label="Stop">
              <StopIcon />
            </button>
          </ComposerPrimitive.Cancel>
        ) : (
          <ComposerPrimitive.Send asChild>
            <button type="button" className="semantic-search-composer-button" aria-label="Send">
              <SubmitIcon />
            </button>
          </ComposerPrimitive.Send>
        )}
      </div>
    </ComposerPrimitive.Root>
  )
}

function ThreadNav({
  threads,
  activeThreadId,
  onSelect,
  onNewChat,
}: {
  threads: StoredThread[]
  activeThreadId: string
  onSelect: (id: string) => void
  onNewChat: () => void
}) {
  return (
    <div className="semantic-search-thread-nav">
      <div className="semantic-search-thread-nav-head">
        <h2>Archive Assistant</h2>
        <button type="button" className="semantic-search-new-chat" onClick={onNewChat}>
          New chat
        </button>
      </div>
      {threads.length > 1 || threads.some((thread) => thread.query.trim()) ? (
        <div className="semantic-search-thread-pills">
          {threads
            .slice()
            .sort((a, b) => b.updatedAt - a.updatedAt)
            .map((thread) => (
              <button
                key={thread.id}
                type="button"
                className={
                  thread.id === activeThreadId
                    ? "semantic-search-thread-pill semantic-search-thread-pill-active"
                    : "semantic-search-thread-pill"
                }
                onClick={() => onSelect(thread.id)}
              >
                {thread.title}
              </button>
            ))}
        </div>
      ) : null}
    </div>
  )
}

function SearchThread({
  runtime,
  initialQuery,
  threads,
  activeThreadId,
  progressEvents,
  setProgressEvents,
  setThreads,
  setActiveThreadId,
}: {
  runtime: any
  initialQuery: string
  threads: StoredThread[]
  activeThreadId: string
  progressEvents: SearchProgressEvent[]
  setProgressEvents: React.Dispatch<React.SetStateAction<SearchProgressEvent[]>>
  setThreads: React.Dispatch<React.SetStateAction<StoredThread[]>>
  setActiveThreadId: React.Dispatch<React.SetStateAction<string>>
}) {
  const messages = useAuiState((s) => s.thread.messages)
  const isEmpty = useAuiState((s) => s.thread.isEmpty)
  const isRunning = useAuiState((s) => s.thread.isRunning)
  const bootstrapRef = useRef(false)
  const hydrateRef = useRef(true)

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeThreadId) ?? threads[0],
    [threads, activeThreadId],
  )

  const lastQuery = useMemo(() => {
    for (const message of [...messages].reverse()) {
      if (message.role !== "user") continue
      const text = getMessageText(message)
      if (text) return text
    }
    return ""
  }, [messages])

  const messageVersion = useMemo(
    () =>
      JSON.stringify(
        messages.map((message: any) => ({
          id: message.id,
          role: message.role,
          status: message.status?.type ?? null,
          parts: getMessageParts(message).map((part: any) => {
            if (part.type === "text") return ["text", part.text]
            if (part.type === "tool-call")
              return [
                "tool-call",
                part.toolName,
                part.toolCallId,
                part.state ?? null,
                Boolean(part.result),
              ]
            if (part.type === "data") return ["data", part.name]
            return [part.type]
          }),
        })),
      ),
    [messages],
  )

  useEffect(() => {
    hydrateRef.current = true
    try {
      if (activeThread?.repository) {
        runtime.thread.importExternalState(activeThread.repository)
      } else {
        runtime.thread.reset()
      }
      setProgressEvents(activeThread?.progressEvents ?? [])
    } catch {
      runtime.thread.reset()
      setProgressEvents([])
      if (activeThread) {
        setThreads((threadsState) =>
          threadsState.map((thread) =>
            thread.id === activeThread.id
              ? { ...thread, repository: null, progressEvents: [], query: "", title: "New chat" }
              : thread,
          ),
        )
      }
    }

    const url = new URL(window.location.href)
    if (activeThread?.query) {
      url.searchParams.set("q", activeThread.query)
    } else {
      url.searchParams.delete("q")
    }
    window.history.replaceState({}, "", url)

    const frame = window.requestAnimationFrame(() => {
      hydrateRef.current = false
    })
    return () => window.cancelAnimationFrame(frame)
  }, [activeThread, runtime, setProgressEvents])

  useEffect(() => {
    if (bootstrapRef.current) return
    if (!initialQuery) return
    if (activeThread?.repository || activeThread?.query) return
    bootstrapRef.current = true
    runtime.thread.composer().setText(initialQuery)
    runtime.thread.composer().send()
  }, [activeThread, initialQuery, runtime])

  useEffect(() => {
    if (hydrateRef.current || !activeThread) return
    const repository = runtime.thread.exportExternalState()
    const query = getQueryFromRepository(repository, lastQuery || activeThread.query)
    const nextThread: StoredThread = {
      ...activeThread,
      title: getThreadTitle(query),
      query,
      repository,
      progressEvents,
      updatedAt: Date.now(),
    }
    setThreads((threadsState) =>
      threadsState.map((thread) => (thread.id === nextThread.id ? nextThread : thread)),
    )
  }, [activeThread, lastQuery, messageVersion, progressEvents, runtime, setThreads])

  const persistActiveThread = useCallback(() => {
    if (!activeThread) return
    const repository = runtime.thread.exportExternalState()
    const query = getQueryFromRepository(repository, lastQuery || activeThread.query)
    const nextThread: StoredThread = {
      ...activeThread,
      title: getThreadTitle(query),
      query,
      repository,
      progressEvents,
      updatedAt: Date.now(),
    }
    setThreads((threadsState) =>
      threadsState.map((thread) => (thread.id === nextThread.id ? nextThread : thread)),
    )
  }, [activeThread, lastQuery, progressEvents, runtime, setThreads])

  function handleSelectThread(id: string) {
    if (id === activeThreadId) return
    persistActiveThread()
    setActiveThreadId(id)
  }

  function handleNewChat() {
    persistActiveThread()
    const nextThread = makeEmptyThread()
    setThreads((threadsState) => [nextThread, ...threadsState])
    setActiveThreadId(nextThread.id)
    setProgressEvents([])
  }

  return (
    <div className="semantic-search-sidebar-shell">
      <ThreadNav
        threads={threads}
        activeThreadId={activeThreadId}
        onSelect={handleSelectThread}
        onNewChat={handleNewChat}
      />
      <ThreadPrimitive.Root className="semantic-search-thread">
        <ThreadPrimitive.Viewport
          className="semantic-search-thread-viewport"
          autoScroll={false}
          scrollToBottomOnInitialize={false}
          scrollToBottomOnRunStart={false}
          scrollToBottomOnThreadSwitch={false}
        >
          {isEmpty ? <WelcomeScreen /> : null}
          <ThreadPrimitive.Messages className="semantic-search-thread-messages">
            {({ message }: { message: any }) =>
              message.role === "user" ? (
                <UserMessage />
              ) : (
                <AssistantMessage
                  progressEvents={progressEvents}
                  isRunning={isRunning}
                  showProgress={message.isLast}
                />
              )
            }
          </ThreadPrimitive.Messages>
          <ThreadPrimitive.ViewportFooter className="semantic-search-thread-footer">
            <ComposerBar />
          </ThreadPrimitive.ViewportFooter>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </div>
  )
}

function SearchApp() {
  useEffect(() => {
    ensureSidebarStyles()
  }, [])

  const searchBasePath = useMemo(() => getSearchBasePath(), [])
  const initialState = useMemo(() => loadPersistedThreads(), [])
  const [threads, setThreads] = useState<StoredThread[]>(initialState.threads)
  const [activeThreadId, setActiveThreadId] = useState(initialState.activeThreadId)
  const [isOpen, setIsOpen] = useState(false)
  const [progressEvents, setProgressEvents] = useState<SearchProgressEvent[]>(
    initialState.threads.find((thread) => thread.id === initialState.activeThreadId)
      ?.progressEvents ?? [],
  )
  const runtime = useChatRuntime({
    transport: new AssistantChatTransport({ api: `${searchBasePath}/api/search` || "/api/search" }),
    onData: (part: any) => {
      if (part.type === "data-search-progress") {
        setProgressEvents((events) => [...events, part.data as SearchProgressEvent])
      }
    },
  })
  const [initialQuery] = useState(
    () => new URL(window.location.href).searchParams.get("q")?.trim() ?? "",
  )
  const threadsRef = useRef(threads)
  const activeThreadIdRef = useRef(activeThreadId)

  const updateThreads = useCallback((updater: React.SetStateAction<StoredThread[]>) => {
    setThreads((current) => {
      const nextThreads = typeof updater === "function" ? updater(current) : updater
      threadsRef.current = nextThreads
      savePersistedThreads({
        activeThreadId: activeThreadIdRef.current,
        threads: nextThreads,
      })
      return nextThreads
    })
  }, [])

  const updateActiveThreadId = useCallback((nextActiveThreadId: string) => {
    activeThreadIdRef.current = nextActiveThreadId
    savePersistedThreads({
      activeThreadId: nextActiveThreadId,
      threads: threadsRef.current,
    })
    setActiveThreadId(nextActiveThreadId)
  }, [])

  useEffect(() => {
    threadsRef.current = threads
  }, [threads])

  useEffect(() => {
    activeThreadIdRef.current = activeThreadId
  }, [activeThreadId])

  return (
    <>
      <button
        type="button"
        className="semantic-search-sidebar-toggle"
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        aria-controls="scene-wiki-search-drawer"
      >
        <SearchIcon />
        <span>{isOpen ? "Hide search" : "Search archive"}</span>
      </button>
      <div
        className={isOpen ? "semantic-search-backdrop semantic-search-backdrop-open" : "semantic-search-backdrop"}
        onClick={() => setIsOpen(false)}
      />
      <aside
        id="scene-wiki-search-drawer"
        className={isOpen ? "semantic-search-drawer semantic-search-drawer-open" : "semantic-search-drawer"}
        aria-hidden={!isOpen}
      >
        <div className="semantic-search-drawer-head">
          <div className="semantic-search-drawer-head-copy">
            <p className="semantic-search-drawer-kicker">Semantic Search</p>
            <h2 className="semantic-search-drawer-title">Archive Assistant</h2>
          </div>
          <button
            type="button"
            className="semantic-search-drawer-close"
            aria-label="Close search"
            onClick={() => setIsOpen(false)}
          >
            <CloseIcon />
          </button>
        </div>
        <AssistantRuntimeProvider runtime={runtime}>
          <SearchThread
            runtime={runtime}
            initialQuery={initialQuery}
            threads={threads}
            activeThreadId={activeThreadId}
            progressEvents={progressEvents}
            setProgressEvents={setProgressEvents}
            setThreads={updateThreads}
            setActiveThreadId={updateActiveThreadId}
          />
        </AssistantRuntimeProvider>
      </aside>
    </>
  )
}

let appRoot: Root | null = null
let mountedElement: Element | null = null

function mountSearchApp() {
  const rootElement = document.querySelector("[data-scene-search-app]")
  if (!rootElement) {
    if (appRoot) {
      appRoot.unmount()
      appRoot = null
      mountedElement = null
    }
    return
  }

  if (mountedElement !== rootElement) {
    if (appRoot) {
      appRoot.unmount()
    }
    appRoot = createRoot(rootElement)
    mountedElement = rootElement
  }

  appRoot.render(<SearchApp />)
}

mountSearchApp()
document.addEventListener("nav", mountSearchApp as EventListener)

if (typeof window !== "undefined" && "addCleanup" in window) {
  window.addCleanup(() => {
    if (appRoot) {
      appRoot.unmount()
      appRoot = null
      mountedElement = null
    }
    document.removeEventListener("nav", mountSearchApp as EventListener)
  })
}
