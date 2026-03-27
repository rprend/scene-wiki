import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"

// components shared across all pages
export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [],
  footer: Component.Footer({
    links: {
      "Semantic Search": "/indexes/semantic-search",
      [process.env.SCENE_WIKI_FOOTER_LINK_LABEL ?? "Source Archive"]:
        process.env.SCENE_WIKI_SOURCE_URL ?? "https://substack.com",
    },
  }),
}

// components for pages that display a single page (e.g. a single note)
export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    Component.ConditionalRender({
      component: Component.Breadcrumbs(),
      condition: (page) => page.fileData.slug !== "index",
    }),
    Component.ArticleTitle(),
    Component.ContentMeta(),
    Component.TagList(),
  ],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [{ Component: Component.Darkmode() }, { Component: Component.ReaderMode() }],
    }),
    Component.Search(),
    Component.DesktopOnly(Component.TableOfContents()),
    Component.Explorer({
      title: "Atlas",
      folderDefaultState: "open",
      folderClickBehavior: "collapse",
    }),
  ],
  right: [Component.SceneSearchSidebar()],
}

// components for pages that display lists of pages  (e.g. tags or folders)
export const defaultListPageLayout: PageLayout = {
  beforeBody: [Component.Breadcrumbs(), Component.ArticleTitle(), Component.ContentMeta()],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [{ Component: Component.Darkmode() }],
    }),
    Component.Search(),
    Component.Explorer({
      title: "Atlas",
      folderDefaultState: "open",
      folderClickBehavior: "collapse",
    }),
  ],
  right: [Component.SceneSearchSidebar()],
}
