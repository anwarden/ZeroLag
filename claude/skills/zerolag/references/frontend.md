# Frontend: interaction, React, tables, forms, delivery

Verified against official docs on 2026-10-09 (React 19.3, React Compiler 1.0, web-vitals 6, Chrome 150+). Apply a technique only when a measured symptom points to it and the installed versions support it.

## Contents
1. Symptom → first measurement
2. Main thread and interaction latency
3. React rendering
4. Admin UI patterns: tabs, search, forms, navigation feedback
5. Large tables and long pages
6. Delivery: bundles, hydration, images, fonts, third parties
7. Field measurement
8. Reject unless evidence demands it

## 1. Symptom → first measurement

| Symptom | Measure first | Usual root causes |
|---|---|---|
| Click shows nothing for a moment | Interaction latency subparts, long animation frames | Long handlers, re-render cascades, forced layout, hydration still running |
| Tab or route takes seconds | Journey timing + network/RSC waterfall | Server waterfalls, blocking layout data, missing prefetch, slow queries |
| Typing or filtering lags | Interaction trace while typing, React Profiler | Synchronous filtering/sorting, broad state, unmemoized list rendering |
| Large table freezes | DOM size, layout time, rows rendered, payload size | Client-side all-rows rendering, no server pagination |
| Save feels slow | Pending feedback time vs confirmed-save time, Server Action count | Sequential actions, full-page refresh after mutation, duplicate writes |
| Mobile feels heavy | Mobile profile timing, JS size of the route, hydration time | Big client bundles, client components high in the tree, third parties |

## 2. Main thread and interaction latency

- Subparts: input delay (main thread busy before handlers), processing (handlers + render), presentation delay (style, layout, paint). Read them in the DevTools Interactions track or `web-vitals/attribution` (`inputDelay`, `processingDuration`, `presentationDelay`). Event Timing durations are rounded to 8 ms ([INP](https://web.dev/articles/inp)).
- Long Animation Frames (Chromium 123+): `blockingDuration`, `scripts[].sourceURL`, `invoker`, `forcedStyleAndLayoutDuration` attribute the blocking work ([LoAF](https://developer.chrome.com/docs/web-platform/long-animation-frames)).
- Paint first, then work: update the visible state, then defer non-visual work. `scheduler.yield()` is in Chrome/Edge 129+ and Firefox 142+, not Safari; fall back to `new Promise(r => setTimeout(r, 0))`, which loses continuation priority. Yield about every 50 ms in long loops. Do not use `isInputPending()` ([long tasks](https://web.dev/articles/optimize-long-tasks)).
- Move CPU-heavy parsing, sorting, exports or diffing of large data to a Web Worker; transfer large buffers instead of copying.
- Forced reflow: batch DOM reads before writes; avoid reading layout (`offsetHeight`, `getBoundingClientRect`) after style changes in loops ([layout thrashing](https://web.dev/articles/avoid-large-complex-layouts-and-layout-thrashing)).
- DOM size: Lighthouse warns above 800 nodes and flags above 1,400; large DOMs raise style and layout cost on every interaction ([DOM size](https://web.dev/articles/dom-size-and-interactivity)).
- Listeners: `touchstart`/`touchmove`/`wheel` are passive by default on window, document and body; mark others `{ passive: true }` when they never call `preventDefault()`. `scroll` is not cancelable. Throttle scroll/resize work to animation frames.
- Memory pressure: when an app slows down the longer it stays open, compare heap snapshots before and after repeating a journey (DevTools Memory panel, or Chrome DevTools MCP `take_heapsnapshot`). Usual causes: listeners, intervals and subscriptions not cleaned up, detached DOM kept by closures, unbounded client caches, and hidden routes or tabs kept mounted (`<Activity>`, Next.js route preservation) that keep large data alive.

## 3. React rendering

- Profile before changing code: React DevTools Profiler with "Record why each component rendered while profiling"; on React ≥ 19.2, React Performance Tracks in a Chrome trace (Scheduler and Components tracks; dev builds or profiling builds — Next.js `next build --profile`). `<Profiler>` is disabled in production builds ([tracks](https://react.dev/reference/dev-tools/react-performance-tracks)).
- Keep state close to where it is used; split broad contexts; derive values during render instead of effect-driven `setState` chains; give list items stable keys; never declare components inside render.
- React Compiler 1.0 (`babel-plugin-react-compiler`): in Next.js ≥ 16 it is the top-level `reactCompiler: true` (before 16: `experimental.reactCompiler`). Verify compilation with the DevTools "Memo ✨" badge or `react/compiler-runtime` in the output. Keep existing `useMemo`/`useCallback` unless tests cover their removal; `eslint-plugin-react-hooks` ≥ 7 reports components the compiler skips ([compiler](https://react.dev/learn/react-compiler/introduction)).
- Without the compiler, add `memo`/`useMemo`/`useCallback` only where the Profiler shows wasted renders or identity-driven effects.
- `startTransition`/`useTransition`: non-urgent updates stay interruptible. Text inputs must not be controlled by a transition. State updates after an `await` inside an Action need their own `startTransition`. React 19.3 renders independent transitions separately, so a slow one no longer holds up others ([useTransition](https://react.dev/reference/react/useTransition), [19.3](https://react.dev/blog/2026/09/09/react-19-3)).
- `useDeferredValue` keeps input responsive while a slow child re-renders, but only if that child receives the deferred value and is memoized (or compiled). It does not reduce network requests ([useDeferredValue](https://react.dev/reference/react/useDeferredValue)).

## 4. Admin UI patterns

- **Tabs and filters**: wrap the switch in a transition so already-visible content stays on screen instead of falling back to a skeleton; dim it with `isPending`. React ≥ 19.2 `<Activity mode="hidden">` keeps a tab's state and DOM, cleans up its Effects and can pre-render the likely next tab at low priority (only data read through Suspense is fetched). Reset user-scoped state on logout or account switch ([Activity](https://react.dev/reference/react/Activity)).
- **Search-as-you-type**: keep the input instant; debounce or throttle the request, cancel outdated requests (`AbortController`) and ignore out-of-order responses. Prefer server-side filtering for large data.
- **Forms**: `useActionState` queues submissions sequentially and exposes `isPending`; read `useFormStatus().pending` in a child of the `<form>` to disable the submit button and prevent double submits. `useOptimistic` gives current-frame feedback when its setter runs inside an Action or `startTransition`, and reverts automatically on failure. Never show "saved" before the server confirmed a durable write ([useActionState](https://react.dev/reference/react/useActionState), [useOptimistic](https://react.dev/reference/react/useOptimistic)).
- **Navigation feedback**: Next.js `useLinkStatus` shows an inline pending state on the clicked link; `loading.js` covers the route. Feedback is not speed: still fix the waterfall behind it.

## 5. Large tables and long pages

1. Paginate, filter and sort on the server first (fewer rows, smaller payloads, less SQL). Cursor pagination for deep lists (data.md).
2. If rendering cost remains high, virtualize rows. Keep the table accessible: `aria-rowcount` (or `-1` when unknown) on the grid and `aria-rowindex` on each rendered row; test keyboard navigation, focus retention, selection, sticky headers and screen readers ([aria-rowcount](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-rowcount)).
3. For long non-table pages (cards, logs, long forms), `content-visibility: auto` with `contain-intrinsic-size: auto <height>` skips off-screen rendering while keeping find-in-page and accessibility. It has no effect on `<tr>`/`<tbody>` (internal table boxes get no containment) ([content-visibility](https://web.dev/articles/content-visibility), [CSS containment](https://www.w3.org/TR/css-contain-2/)).
4. Collapsed sections that must stay findable: `hidden="until-found"` with the `beforematch` event.

## 6. Delivery: bundles, hydration, images, fonts, third parties

- Analyze the slow route only. Next.js ≥ 16 no longer prints First Load JS in `next build`; use `next analyze` (Turbopack; 16.1 `experimental-analyze`, renamed in 16.4) or `next build --analyze`. `@next/bundle-analyzer` is the webpack path ([package bundling](https://nextjs.org/docs/app/guides/package-bundling)).
- Push `"use client"` boundaries down; keep editors, charts, maps and heavy date or spreadsheet libraries out of shared layouts; `next/dynamic` for rarely used client components, but prefetch or preload what the next likely click needs.
- Hydration: a page can paint before it responds. Measure input during hydration on the mobile profile; reduce client JS and client-side work at startup.
- Images: Next.js 16 deprecates `priority` in favour of `preload`; the docs recommend `loading="eager"` or `fetchPriority="high"` in most cases. Set `sizes` for responsive images; defaults changed in 16 (`qualities: [75]`, 4-hour `minimumCacheTTL`) ([Image](https://nextjs.org/docs/app/api-reference/components/image), [fetch priority](https://web.dev/articles/fetch-priority)).
- Fonts: self-host with stable fallbacks to avoid layout shift; follow the project's own font rules.
- Third parties: load non-essential scripts after interaction-critical work; measure their main-thread cost in LoAF script attribution ([third parties](https://web.dev/articles/efficiently-load-third-party-javascript)).

## 7. Field measurement

- CrUX covers Chrome on public, indexable pages and attributes SPA routes to the landing page: authenticated admin apps usually need their own RUM ([CrUX methodology](https://developer.chrome.com/docs/crux/methodology)).
- web-vitals 6: `onINP` from `web-vitals/attribution` gives the target, subparts and `longestScript`; `reportSoftNavs: true` reports per App Router navigation in Chromium 151+ only. Use `generateTarget` to keep personal data out of selectors. INP and LCP are measurable in Chromium, Firefox and Safari 26.2+; CLS is Chromium-only ([web-vitals](https://github.com/GoogleChrome/web-vitals)).
- Adding RUM is an application change: propose it, do not add it during an audit.

## 8. Reject unless evidence demands it

Memoizing everything; deferring JavaScript the next likely click needs; hiding work behind a spinner and calling it faster; optimistic "saved" states before persistence; virtualizing a table that could be paginated on the server; disabling SSR or hydration to improve a score; animations or CSS tricks on every row without a measured layout cost.
