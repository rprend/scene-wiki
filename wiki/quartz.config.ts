import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

/**
 * Quartz 4 Configuration
 *
 * See https://quartz.jzhao.xyz/configuration for more information.
 */
const config: QuartzConfig = {
  configuration: {
    pageTitle: process.env.QUARTZ_PAGE_TITLE ?? "Scene Wiki",
    pageTitleSuffix: "",
    enableSPA: false,
    enablePopovers: true,
    analytics: null,
    locale: "en-US",
    baseUrl: process.env.QUARTZ_BASE_URL ?? "scene-wiki.pages.dev",
    ignorePatterns: ["private", "templates", ".obsidian", "_meta"],
    defaultDateType: "created",
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: {
        header: "Schibsted Grotesk",
        body: "Source Sans Pro",
        code: "IBM Plex Mono",
      },
      colors: {
        lightMode: {
          light: "#ffffff",
          lightgray: "#e6e6e6",
          gray: "#b6b6b6",
          darkgray: "#404040",
          dark: "#111111",
          secondary: "#3366cc",
          tertiary: "#6b7280",
          highlight: "rgba(51, 102, 204, 0.08)",
          textHighlight: "#fff2a8",
        },
        darkMode: {
          light: "#1a1a1a",
          lightgray: "#303030",
          gray: "#5a5a5a",
          darkgray: "#dddddd",
          dark: "#f5f5f5",
          secondary: "#7aa2ff",
          tertiary: "#9ca3af",
          highlight: "rgba(122, 162, 255, 0.12)",
          textHighlight: "#8d7a22",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "filesystem"],
      }),
      Plugin.SyntaxHighlighting({
        theme: {
          light: "github-light",
          dark: "github-dark",
        },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description(),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentIndex({
        enableRSS: false,
        enableSiteMap: true,
      }),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.Favicon(),
      Plugin.NotFoundPage(),
      // Disabled for this generated wiki because 5k+ pages makes OG rendering unnecessarily slow.
      // Plugin.CustomOgImages(),
    ],
  },
}

export default config
