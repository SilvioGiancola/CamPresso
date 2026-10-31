import AppKit
import AVFoundation
import AVKit
import Combine
import SwiftUI

@MainActor
final class ReviewSession: ObservableObject {
    unowned let model: AppModel
    @Published var review: JSONObject
    @Published var loading = true
    @Published var error: String?
    @Published var time = 0.0
    @Published var duration = 1.0
    @Published var playing = false
    @Published var ready = false
    @Published var deciding = false
    private var starting = false
    private var playbackGeneration = UUID()
    @Published var divider = 0.5
    @Published var zoom = 1.0
    @Published var originalAudio = false { didSet { original.isMuted = !originalAudio; compressed.isMuted = originalAudio } }
    let original = AVPlayer()
    let compressed = AVPlayer()
    private var observer: Any?
    private var statusObservers: [NSKeyValueObservation] = []
    private var task: Task<Void, Never>?
    private var decisionTask: Task<Void, Never>?
    private var synchronizing = false
    private var resumeAfterSeek = false
    private var loadGeneration = UUID()

    init(model: AppModel, initial: JSONObject) {
        self.model = model; review = initial
        original.isMuted = true; compressed.isMuted = false
        original.automaticallyWaitsToMinimizeStalling = false
        compressed.automaticallyWaitsToMinimizeStalling = false
        load(initial)
    }
    var jobID: String { string(review["job_id"]) }
    var name: String { string(review["name"]) }
    var otherIDs: [String] { model.reviews.map(\.jobID) }
    func load(_ value: JSONObject) {
        stop(); task?.cancel(); loadGeneration = UUID(); let generation = loadGeneration
        if let observer { original.removeTimeObserver(observer); self.observer = nil }
        statusObservers.removeAll(); synchronizing = false; review = value; loading = true; ready = false; error = nil; time = 0
        original.replaceCurrentItem(with: nil); compressed.replaceCurrentItem(with: nil)
        task = Task { [self] in
            do {
                async let lhs = AssetRepository.shared.player(string(value["original_id"]))
                async let rhs = AssetRepository.shared.player(string(value["result_id"]))
                let items = try await (lhs, rhs)
                guard !Task.isCancelled, loadGeneration == generation else { return }
                original.replaceCurrentItem(with: items.0); compressed.replaceCurrentItem(with: items.1)
                let seconds = number(object(value["original_version"])["duration"]) ?? 1
                duration = max(0.001, seconds); loading = false
                for item in [items.0, items.1] {
                    statusObservers.append(item.observe(\.status, options: [.initial, .new]) { [weak self] _, _ in
                        Task { @MainActor in
                            guard let self else { return }
                            self.ready = self.original.currentItem?.status == .readyToPlay && self.compressed.currentItem?.status == .readyToPlay
                            if let error = self.original.currentItem?.error ?? self.compressed.currentItem?.error { self.error = error.localizedDescription }
                        }
                    })
                }
                if let observer { original.removeTimeObserver(observer); self.observer = nil }
                observer = original.addPeriodicTimeObserver(forInterval: .init(seconds: 0.15, preferredTimescale: 600), queue: .main) { [weak self] value in
                    Task { @MainActor in
                        guard let self, !self.synchronizing else { return }
                        if value.seconds.isFinite { self.time = min(self.duration, max(0, value.seconds)) }
                        if self.playing && abs(self.original.currentTime().seconds-self.compressed.currentTime().seconds) > 0.15 { self.seek(self.time, resume: true) }
                        if self.time >= self.duration-0.05 { self.stop() }
                    }
                }
            } catch {
                guard loadGeneration == generation else { return }
                loading = false; self.error = error.localizedDescription
            }
        }
    }
    func stop() { playbackGeneration = UUID(); starting = false; original.cancelPendingPrerolls(); compressed.cancelPendingPrerolls(); original.pause(); compressed.pause(); playing = false }
    func play() {
        guard ready, !synchronizing, !starting, !deciding else { return }
        if playing { stop(); return }
        error = nil; starting = true
        let generation = playbackGeneration
        Task {
            let a = original, b = compressed
            async let lhs: Bool = withCheckedContinuation { continuation in a.preroll(atRate: 1) { continuation.resume(returning: $0) } }
            async let rhs: Bool = withCheckedContinuation { continuation in b.preroll(atRate: 1) { continuation.resume(returning: $0) } }
            let prepared = await (lhs, rhs)
            guard generation == playbackGeneration else { return }; starting = false
            guard prepared.0 && prepared.1 else { error = "Still preparing the iCloud videos. Try Play again."; return }
            let host = CMClockGetTime(CMClockGetHostTimeClock()) + CMTime(seconds: 0.15, preferredTimescale: 600)
            let position = CMTime(seconds: time >= duration-0.05 ? 0 : time, preferredTimescale: 60000)
            original.setRate(1, time: position, atHostTime: host)
            compressed.setRate(1, time: position, atHostTime: host)
            playing = true
        }
    }
    func seek(_ seconds: Double, resume: Bool) {
        guard !synchronizing else { return }
        stop(); synchronizing = true
        let generation = loadGeneration
        Task {
            let position = CMTime(seconds: seconds, preferredTimescale: 60000)
            async let a: Bool = withCheckedContinuation { continuation in original.seek(to: position, toleranceBefore: .zero, toleranceAfter: .zero) { continuation.resume(returning: $0) } }
            async let b: Bool = withCheckedContinuation { continuation in compressed.seek(to: position, toleranceBefore: .zero, toleranceAfter: .zero) { continuation.resume(returning: $0) } }
            _ = await (a, b)
            guard generation == loadGeneration else { return }
            time = seconds; synchronizing = false
            if resume { play() }
        }
    }
    func sliderChanged(_ editing: Bool) {
        if editing { resumeAfterSeek = playing; stop(); synchronizing = true }
        else { synchronizing = false; seek(time, resume: resumeAfterSeek) }
    }
    func stalled() { stop(); error = "Playback paused while Photos prepares the video. Both versions stay aligned; press Play when ready." }
    func navigate(_ offset: Int) {
        guard !deciding else { return }
        let ids = otherIDs
        guard !ids.isEmpty else { stop(); return }
        let current = ids.firstIndex(of: jobID) ?? 0
        let index = max(0, min(ids.count-1, current+offset))
        Task {
            do { let value = try await model.request("review.open", ["job_id": ids[index]]); load(value) }
            catch { self.error = error.localizedDescription }
        }
    }
    func decide(_ decision: String) {
        guard !deciding else { return }; deciding = true
        stop()
        let value = review, id = jobID
        decisionTask = Task {
            defer { deciding = false }
            do {
                guard try await model.decide([value], decision) else { return }
                // Decisions are asynchronous; wait for the confirmed workflow event.
                for _ in 0..<600 {
                    if let row = model.rows.first(where: { $0.jobID == id }), row.state == "Completed" {
                        let next = model.reviews.first { $0.jobID != id }
                        if let next {
                            let value = try await model.request("review.open", ["job_id": next.jobID]); load(value)
                        } else { ready = false; error = "Review complete. Your decision is saved." }
                        return
                    }
                    if let row = model.rows.first(where: { $0.jobID == id }), ["Needs attention", "Error"].contains(row.state) { error = row.detail; return }
                    try await Task.sleep(for: .milliseconds(250))
                }
                error = "The Photos decision is still pending. Check its status in the main window."
            } catch { self.error = error.localizedDescription }
        }
    }
    func dispose() {
        stop(); task?.cancel(); decisionTask?.cancel(); loadGeneration = UUID(); statusObservers.removeAll()
        if let observer { original.removeTimeObserver(observer); self.observer = nil }
        original.replaceCurrentItem(with: nil); compressed.replaceCurrentItem(with: nil)
    }
}

final class PlayerSurface: NSView {
    let playerLayer = AVPlayerLayer()
    override init(frame: NSRect) { super.init(frame: frame); wantsLayer = true; layer?.backgroundColor = NSColor.black.cgColor; layer?.addSublayer(playerLayer); playerLayer.videoGravity = .resizeAspect }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }
    override func layout() { super.layout(); playerLayer.frame = bounds }
}
struct PlayerLayerView: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> PlayerSurface { let view = PlayerSurface(); view.playerLayer.player = player; return view }
    func updateNSView(_ view: PlayerSurface, context: Context) { view.playerLayer.player = player }
    static func dismantleNSView(_ view: PlayerSurface, coordinator: ()) { view.playerLayer.player = nil }
}
struct ReviewView: View {
    @ObservedObject var session: ReviewSession
    var body: some View {
        VStack(spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(session.name).font(.headline).lineLimit(1)
                    Text("\(humanSize(number(session.review["source_size"]))) → \(humanSize(number(session.review["result_size"]))) · library size reduction").font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                Button { session.navigate(-1) } label: { Image(systemName: "chevron.left") }.help("Previous review")
                Button { session.navigate(1) } label: { Image(systemName: "chevron.right") }.help("Next review")
            }
            GeometryReader { geometry in
                ZStack {
                    Color.black
                    PlayerLayerView(player: session.compressed).scaleEffect(session.zoom)
                    PlayerLayerView(player: session.original).scaleEffect(session.zoom)
                        .mask(alignment: .leading) { Rectangle().frame(width: geometry.size.width*session.divider) }
                    if session.divider > 0 && session.divider < 1 {
                        Rectangle().fill(.white).frame(width: 2).position(x: geometry.size.width*session.divider, y: geometry.size.height/2)
                        Image(systemName: "arrow.left.and.right").padding(8).background(.regularMaterial, in: Circle())
                            .position(x: geometry.size.width*session.divider, y: geometry.size.height/2)
                            .gesture(DragGesture(coordinateSpace: .named("comparison")).onChanged { value in session.divider = min(0.98, max(0.02, value.location.x/geometry.size.width)) })
                    }
                    VStack { HStack { Text("Original"); Spacer(); Text("Compressed") }.font(.caption.bold()).padding(8).background(.black.opacity(0.5)); Spacer() }
                    if session.loading { ProgressView("Preparing comparison from Photos…").padding().background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12)) }
                }
                .coordinateSpace(name: "comparison").clipped().clipShape(RoundedRectangle(cornerRadius: 10))
                .gesture(MagnifyGesture().onChanged { value in session.zoom = min(4, max(1, value.magnification)) })
            }
            HStack {
                Button { session.play() } label: { Image(systemName: session.playing ? "pause.fill" : "play.fill") }.keyboardShortcut(.space, modifiers: []).disabled(!session.ready)
                Text(durationText(session.time)).monospacedDigit().frame(width: 55)
                Slider(value: $session.time, in: 0...max(0.001, session.duration), onEditingChanged: session.sliderChanged).disabled(!session.ready)
                Text(durationText(session.duration)).monospacedDigit().frame(width: 55)
                Toggle("Original audio", isOn: $session.originalAudio).toggleStyle(.button)
            }
            HStack {
                Button("Compare") { session.divider = 0.5; session.zoom = 1 }
                Button("Original") { session.divider = 1 }
                Button("Compressed") { session.divider = 0 }
                Spacer()
                Button("Revert · Keep Original") { session.decide("revert") }.disabled(!session.ready || session.deciding)
                Button("Keep Compressed") { session.decide("keep") }.buttonStyle(.borderedProminent).foregroundStyle(Brand.background).disabled(!session.ready || session.deciding)
            }
            if let error = session.error { Text(error).font(.callout).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading) }
            ForEach(session.review["warnings"] as? [String] ?? [], id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading) }
        }
        .padding(16).frame(minWidth: 760, minHeight: 520)
        .onDisappear { session.dispose() }
        .onReceive(NotificationCenter.default.publisher(for: .AVPlayerItemPlaybackStalled)) { notification in
            if let item = notification.object as? AVPlayerItem, item === session.original.currentItem || item === session.compressed.currentItem { session.stalled() }
        }
    }
}
