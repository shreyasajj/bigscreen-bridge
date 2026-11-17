// Plasma 6 window API. Report identity only, never captions or content.
function report(excludeId) {
    const active = workspace.activeWindow;
    const windows = workspace.windowList().filter(w => w.normalWindow && !w.deleted && String(w.internalId) !== excludeId).map(w => ({
        id: String(w.internalId), desktop: String(w.desktopFileName),
        resource: String(w.resourceName), class: String(w.resourceClass),
        active: w === active
    }));
    callDBus("org.bigscreen.Bridge", "/org/bigscreen/Bridge",
             "org.bigscreen.Bridge", "ReportWindows", JSON.stringify(windows));
}
function watch(w) {
    w.desktopFileNameChanged.connect(report);
    report();
}
workspace.windowAdded.connect(watch);
workspace.windowRemoved.connect(w => report(String(w.internalId)));
workspace.windowActivated.connect(report);
workspace.windowList().forEach(watch);
report();
