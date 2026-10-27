import AppKit
import SwiftUI

@MainActor
final class ApplicationDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model else { return .terminateNow }
        if model.closing { return .terminateCancel }
        return model.beginClose() ? .terminateNow : model.closing ? .terminateLater : .terminateCancel
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        sender.windows.first(where: { $0.identifier?.rawValue == "main" })?.makeKeyAndOrderFront(nil)
        return true
    }
}

struct MainWindowHook: NSViewRepresentable {
    let model: AppModel
    final class Coordinator: NSObject, NSWindowDelegate {
        weak var model: AppModel?
        func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
    }
    func makeCoordinator() -> Coordinator { let value = Coordinator(); value.model = model; return value }
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { view.window?.delegate = context.coordinator }
        return view
    }
    func updateNSView(_ view: NSView, context: Context) {}
}

@main
struct VideoQueueApp: App {
    @NSApplicationDelegateAdaptor(ApplicationDelegate.self) private var delegate
    @StateObject private var model = AppModel()
    @Environment(\.openSettings) private var openSettings
    var body: some Scene {
        Window("Video Queue", id: "main") {
            LibraryView().environmentObject(model).background(MainWindowHook(model: model))
                .onAppear { delegate.model = model; model.start() }
        }
        .defaultSize(width: 1160, height: 790)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .toolbar) {
                Button("Refresh Library") { Task { await model.refresh() } }.keyboardShortcut("r")
                Button("Compress Selected Videos") { model.compress() }.disabled(!model.setupComplete || model.compressionTargets.isEmpty)
                Button("Show Summary") { NotificationCenter.default.post(name: .init("ToggleSummary"), object: nil) }
            }
            CommandGroup(replacing: .help) {
                Button("Video Queue User Guide") { NSWorkspace.shared.open(URL(string: "https://github.com/SilvioGiancola/MacVideoLibraryConverter/blob/main/docs/USER_GUIDE.md")!) }
                Button("Open Logs and Recovery") { NSWorkspace.shared.open(model.data) }
            }
        }
        Settings { SettingsView().environmentObject(model) }
    }
}
