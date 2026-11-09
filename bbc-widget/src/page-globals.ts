// What the bundle leaves on the page. Pure — pinned by tests/remount.test.mjs.

/** The route map (PR-15: keeps TRANSCRIPT_EXPORT_V0 in the bundle; nothing
 *  reads it), defined once per page. After __BBC_WIDGET_TEARDOWN__ the
 *  bundle runs again on a remount, and defining the same non-configurable
 *  property a second time throws: the widget never mounted again and its
 *  loaded flag stayed true. The first definition stays; it holds the same
 *  routes. */
export function exposeRoutes(target: object, routes: Record<string, string>): void {
  if (Object.prototype.hasOwnProperty.call(target, '__bbcWidgetRoutes')) return
  Object.defineProperty(target, '__bbcWidgetRoutes', {
    value: Object.freeze({ ...routes }),
    enumerable: false,
  })
}
