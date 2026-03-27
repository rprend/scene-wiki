import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"

export default (() => {
  const SceneSearchSidebar: QuartzComponent = (_props: QuartzComponentProps) => {
    return (
      <div class="semantic-search-rail">
        <div class="semantic-search-app" data-scene-search-app=""></div>
        <script type="module" src="/static/scene-search-app.js"></script>
      </div>
    )
  }

  return SceneSearchSidebar
}) satisfies QuartzComponentConstructor
