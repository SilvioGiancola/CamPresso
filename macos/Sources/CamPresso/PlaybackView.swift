import AppKit
import AVFoundation
import AVKit
import SwiftUI

@MainActor
final class PlaybackSession: ObservableObject {
    let player = AVPlayer()
    @Published private(set) var loading = true
    @Published private(set) var error: String?
    private let identifier: String
    private var loadTask: Task<Void, Never>?
    private var statusObserver: NSKeyValueObservation?
    private var failureObserver: NSObjectProtocol?
    private var generation = UUID()
    private var closed = false
    private var autoplay = false

    init(identifier: String) { self.identifier = identifier }

    func load() {
        guard !closed else { return }
        reset()
        let generation = self.generation
        loading = true; error = nil; autoplay = true
        loadTask = Task { [weak self, identifier] in
            do {
                let item = try await AssetRepository.shared.player(identifier)
                guard let self, !Task.isCancelled, !self.closed, self.generation == generation else { return }
                self.player.replaceCurrentItem(with: item)
                self.statusObserver = item.observe(\.status, options: [.initial, .new]) { [weak self] item, _ in
                    Task { @MainActor in
                        guard let self, !self.closed, self.generation == generation,
                              self.player.currentItem === item else { return }
                        switch item.status {
                        case .readyToPlay:
                            self.loading = false
                            if self.autoplay { self.autoplay = false; self.player.play() }
                        case .failed:
                            self.fail(item.error?.localizedDescription ?? "Photos could not prepare this video for playback.")
                        default: break
                        }
                    }
                }
                self.failureObserver = NotificationCenter.default.addObserver(
                    forName: .AVPlayerItemFailedToPlayToEndTime, object: item, queue: .main
                ) { [weak self] notification in
                    let message = (notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error)?.localizedDescription
                    Task { @MainActor in
                        guard let self, !self.closed, self.generation == generation else { return }
                        self.fail(message ?? "Playback stopped because this video could not be decoded.")
                    }
                }
            } catch {
                guard let self, !self.closed, self.generation == generation, !Task.isCancelled else { return }
                self.fail(error.localizedDescription)
            }
        }
    }
    private func fail(_ message: String) {
        player.pause(); autoplay = false; loading = false; error = message
    }
    private func reset() {
        generation = UUID(); loadTask?.cancel(); loadTask = nil
        statusObserver = nil
        if let failureObserver { NotificationCenter.default.removeObserver(failureObserver); self.failureObserver = nil }
        player.pause(); player.replaceCurrentItem(with: nil)
    }
    func close() {
        guard !closed else { return }
        closed = true; reset(); loading = false
    }
}

// Use AppKit's player directly. SwiftUI VideoPlayer's generic AVKit overlay
// aborts during superclass metadata initialization in the reported macOS crash.
private struct NativePlaybackView: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .inline
        view.videoGravity = .resizeAspect
        view.showsFullScreenToggleButton = true
        view.player = player
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) {
        if view.player !== player { view.player = player }
    }
    static func dismantleNSView(_ view: AVPlayerView, coordinator: ()) { view.player = nil }
}

struct PlaybackView: View {
    @ObservedObject var session: PlaybackSession
    var body: some View {
        ZStack {
            Color.black
            NativePlaybackView(player: session.player)
            if session.loading {
                ProgressView("Preparing video from Photos…").padding(20)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
            if let error = session.error {
                VStack(spacing: 14) {
                    Image(systemName: "exclamationmark.triangle").font(.largeTitle)
                    Text("Video unavailable").font(.headline)
                    Text(error).multilineTextAlignment(.center).textSelection(.enabled)
                    Text("For a video that needs attention, use Inspect Photos status in the main window.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Retry Playback") { session.load() }
                }.padding(24).frame(maxWidth: 520)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }.frame(minWidth: 640, minHeight: 360)
    }
}

@MainActor
final class PlaybackWindowController: NSWindowController, NSWindowDelegate {
    private let session: PlaybackSession
    private var onClose: (() -> Void)?

    init(identifier: String, title: String, onClose: @escaping () -> Void) {
        let session = PlaybackSession(identifier: identifier)
        self.session = session; self.onClose = onClose
        let controller = NSHostingController(rootView: PlaybackView(session: session).campressoTheme())
        let window = NSWindow(contentViewController: controller)
        window.title = title; window.setContentSize(.init(width: 900, height: 600)); window.center()
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.delegate = self
    }
    required init?(coder: NSCoder) { return nil }
    func open() {
        showWindow(nil); window?.makeKeyAndOrderFront(nil); session.load()
    }
    func windowWillClose(_ notification: Notification) {
        session.close()
        let callback = onClose; onClose = nil
        // Release the owning controller after AppKit finishes its close event.
        DispatchQueue.main.async { callback?() }
    }
}
