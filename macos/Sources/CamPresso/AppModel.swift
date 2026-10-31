import AppKit
import AVFoundation
import Combine
import Foundation
import Photos
import SwiftUI

struct VideoRow: Identifiable {
    var raw: JSONObject
    var id: String { string(raw["id"]) }
    var name: String { string(raw["name"]) }
    var jobID: String { string(raw["job_id"]) }
    var state: String { string(raw["state"]) }
    var detail: String { string(raw["detail"]) }
    var resultID: String { string(raw["result_id"]) }
    var originalID: String { string(raw["original_id"]).isEmpty ? id : string(raw["original_id"]) }
    var thumbnailID: String { state == "Completed" && string(raw["decision"]) == "keep" ? resultID : originalID }
    var favorite: Bool { raw["favorite"] as? Bool == true }
    var local: Bool { raw["local"] as? Bool == true }
    var shared: Bool { raw["shared_library"] as? Bool == true || raw["shared_album"] as? Bool == true }
    var sourceSize: Double? { number(raw["source_size"]) }
    var resultSize: Double? { number(raw["result_size"]) }
    var estimate: Double? { resultSize ?? number(raw["estimated_size"]) }
    var progress: Double? { number(raw["progress"]) }
    var processing: Bool { ["Queued", "Downloading", "Converting", "Verifying", "Saving to Photos", "Applying decision"].contains(state) }
    var canCompress: Bool {
        ["Ready", "Error", "Cancelled", "Paused", "Waiting for connection", "Waiting for space", "Skipped"].contains(state)
        && resultID.isEmpty && !shared && raw["excluded"] as? Bool != true
    }
    var reduction: Double? {
        guard let sourceSize, let estimate else { return nil }
        return sourceSize-estimate
    }
    var color: Color {
        switch state {
        case "Ready to review": return Brand.cyan
        case "Completed": return .green
        case "Needs attention", "Error": return .red
        case "Waiting for connection", "Waiting for space", "Paused": return .orange
        default: return processing ? Brand.accent : .secondary
        }
    }
    var savingText: String {
        guard let original = sourceSize, let compressed = estimate, original > 0 else { return "Estimate unavailable" }
        return "\(humanSize(original)) → \(humanSize(compressed)) · \(Int((original-compressed)/original*100))% smaller"
    }
}

@MainActor
final class VideoItem: ObservableObject, Identifiable {
    let id: String
    @Published private(set) var row: VideoRow
    private var revision: Int

    init(_ row: VideoRow, revision: Int) { id = row.id; self.row = row; self.revision = revision }
    func replace(_ value: VideoRow, revision: Int) {
        guard revision >= self.revision else { return }
        self.revision = revision
        row = value
    }
    func progress(_ packet: JSONObject) {
        let revision = Int(number(packet["revision"]) ?? 0)
        guard revision >= self.revision, row.processing,
              string(packet["job_id"]) == row.jobID,
              string(packet["generation"]) == string(row.raw["generation"]) else { return }
        self.revision = revision
        let percent = number(packet["percent"]), detail = string(packet["detail"])
        guard percent != row.progress || detail != row.detail else { return }
        var updated = row
        updated.raw["progress"] = percent as Any? ?? NSNull()
        updated.raw["detail"] = detail
        row = updated
    }
}

enum LibrarySection: String, CaseIterable, Identifiable {
    case library = "Library", processing = "Processing", review = "Review", completed = "Completed"
    var id: String { rawValue }
}

final class PacketWriter: @unchecked Sendable {
    private let queue = DispatchQueue(label: "campresso.protocol.write", qos: .userInitiated)
    private var input: FileHandle?
    func connect(_ input: FileHandle) { queue.sync { self.input = input } }
    func send(_ value: JSONObject) {
        queue.async {
            guard let data = try? JSONSerialization.data(withJSONObject: ["protocol": 1].merging(value) { _, new in new }) else { return }
            try? self.input?.write(contentsOf: data + Data([10]))
        }
    }
    func close() { queue.async { try? self.input?.close(); self.input = nil } }
}

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var items: [VideoItem] = []
    @Published private(set) var thumbnailRevision = 0
    @Published var section: LibrarySection = .library
    @Published var selection = Set<String>()
    @Published var search = ""
    @Published var sort = "Most savings"
    @Published var favoritesOnly = false
    @Published var localOnly = false
    @Published var preferences: JSONObject = [:]
    @Published var library = ""
    @Published var scanning = false
    @Published var paused = false
    @Published var connected = false
    @Published var notice: String?
    @Published var catalogError: String?
    @Published var fatalError: String?
    @Published var closing = false
    @Published var authorizing = false
    @Published var photoAuthorized = PHPhotoLibrary.authorizationStatus(for: .readWrite) == .authorized
    @Published var runIDs = Set<String>()
    @Published var cleaning = false
    @Published var retainedFiles: [JSONObject] = []
    let data: URL
    let media: NativeMedia
    let writer = PacketWriter()
    private var process: Process?
    private var replies: [String: CheckedContinuation<JSONObject, Error>] = [:]
    private var commandTimeouts: [String: Task<Void, Never>] = [:]
    private var closeReplied = false
    private var started = false
    private let sessionStartedAt = (NSRunningApplication.current.launchDate ?? Date()).timeIntervalSince1970
    private let launchServiceRequest: String?
    private var reviewWindow: NSWindow?
    private var playbackWindows: [UUID: PlaybackWindowController] = [:]
    private let reader = PacketReader()
    private var pipe: Pipe?
    private var itemsByID: [String: VideoItem] = [:]
    private var listRevision = 0
    private var filteredCache: (key: GalleryFilter, items: [VideoItem])?
    private struct GalleryFilter: Equatable {
        let revision: Int
        let section: String
        let search: String
        let sort: String
        let favorites: Bool
        let local: Bool
    }

    init() {
        let arguments = CommandLine.arguments
        func option(_ name: String) -> String? {
            guard let index = arguments.firstIndex(of: name), arguments.indices.contains(index+1) else { return nil }
            return arguments[index+1]
        }
        data = URL(fileURLWithPath: option("--data-dir") ?? NSHomeDirectory()+"/Library/Application Support/CamPresso").resolvingSymlinksInPath()
        launchServiceRequest = option("--service-request")
        media = NativeMedia(data: data)
    }
    var rows: [VideoRow] { items.map(\.row) }
    var processingItems: [VideoItem] { items.filter { $0.row.processing } }
    var processing: [VideoRow] { processingItems.map(\.row) }
    var reviews: [VideoRow] { rows.filter { $0.state == "Ready to review" } }
    var chosen: [VideoRow] { rows.filter { selection.contains($0.id) } }
    var setupComplete: Bool {
        photoAuthorized && string(preferences["personal_library_confirmed"]) == library && !library.isEmpty
        && preferences["import_notice_accepted"] as? Bool == true
    }
    var filteredItems: [VideoItem] {
        let cacheKey = GalleryFilter(revision: listRevision, section: section.rawValue, search: search,
                                    sort: sort, favorites: favoritesOnly, local: localOnly)
        if let filteredCache, filteredCache.key == cacheKey { return filteredCache.items }
        let values = items.filter { item in
            let row = item.row
            let matches: Bool
            switch section {
            case .library: matches = true
            case .processing: matches = row.processing || ["Paused", "Waiting for connection", "Waiting for space", "Cancelled", "Error", "Needs attention", "Awaiting import"].contains(row.state)
            case .review: matches = row.state == "Ready to review" || row.state == "Awaiting import" || row.state == "Needs attention"
            case .completed: matches = row.state == "Completed"
            }
            return matches && (!favoritesOnly || row.favorite) && (!localOnly || row.local)
                && (search.isEmpty || row.name.localizedCaseInsensitiveContains(search) || row.id.localizedCaseInsensitiveContains(search))
        }
        let sorted = values.sorted { lhs, rhs in
            let a = lhs.row, b = rhs.row
            switch sort {
            case "Filename": return a.name.localizedStandardCompare(b.name) == .orderedAscending
            case "Newest": return string(a.raw["date"]) > string(b.raw["date"])
            case "Largest original": return (a.sourceSize ?? -1) > (b.sourceSize ?? -1)
            default:
                let lhs = a.reduction ?? a.sourceSize ?? -1, rhs = b.reduction ?? b.sourceSize ?? -1
                return lhs == rhs ? a.id < b.id : lhs > rhs
            }
        }
        filteredCache = (cacheKey, sorted)
        return sorted
    }
    var filtered: [VideoRow] { filteredItems.map(\.row) }
    var compressionTargets: [VideoRow] {
        let candidates = selection.isEmpty ? filtered : chosen
        return candidates.filter { row in
            guard row.canCompress else { return false }
            if !selection.isEmpty { return true }
            guard let size = row.sourceSize, let estimated = row.estimate else { return true }
            return estimated <= size*0.95
        }
    }
    var batchTotal: Int { rows.filter { runIDs.contains($0.jobID) }.count }
    var batchProcessed: Int { rows.filter { runIDs.contains($0.jobID) && !$0.processing && $0.state != "Paused" }.count }

    private func galleryOrderChanged(from old: VideoRow, to new: VideoRow) -> Bool {
        (section != .library && old.state != new.state) || old.name != new.name
            || old.favorite != new.favorite || old.local != new.local
            || old.sourceSize != new.sourceSize || old.estimate != new.estimate
            || string(old.raw["date"]) != string(new.raw["date"])
    }

    func start() {
        guard !started else { return }; started = true
        guard !data.pathComponents.contains(where: { $0.lowercased().hasSuffix(".photoslibrary") }) else {
            fatalError = "Application data must be outside the Photos library."; return
        }
        do {
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
            let task = Process()
            let args = CommandLine.arguments
            let custom = args.firstIndex(of: "--backend-python").flatMap { args.indices.contains($0+1) ? args[$0+1] : nil }
            let python = custom ?? NSHomeDirectory()+"/Library/Application Support/CamPresso/venv/bin/python"
            guard FileManager.default.isExecutableFile(atPath: python) else { throw WorkflowError(message: "Run Install.command to install the Python workflow backend.") }
            task.executableURL = URL(fileURLWithPath: python)
            var backendArguments = ["-m", "campresso.backend", "--stdio", "--data-dir", data.path,
                                    "--session-started-at", String(sessionStartedAt)]
            if let launchServiceRequest { backendArguments += ["--service-request", launchServiceRequest] }
            task.arguments = backendArguments
            var environment = ProcessInfo.processInfo.environment
            environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (environment["PATH"] ?? "/usr/bin:/bin")
            environment["PYTHONUNBUFFERED"] = "1"; task.environment = environment
            let input = Pipe(), output = Pipe(); pipe = output
            task.standardInput = input; task.standardOutput = output; writer.connect(input.fileHandleForWriting)
            let log = data.appendingPathComponent("native.log")
            if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
            let errors = try FileHandle(forWritingTo: log); try errors.seekToEnd(); task.standardError = errors
            reader.callback = { [weak self] packet in DispatchQueue.main.async { self?.receive(packet) } }
            let reader = reader
            output.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                guard !chunk.isEmpty else { handle.readabilityHandler = nil; return }
                reader.append(chunk)
            }
            task.terminationHandler = { [weak self] task in
                Task { @MainActor in
                    guard let self else { return }
                    self.connected = false
                    self.replies.values.forEach { $0.resume(throwing: WorkflowError(message: "The workflow backend stopped. Reopen the app to start a fresh session.")) }
                    self.replies.removeAll()
                    self.commandTimeouts.values.forEach { $0.cancel() }; self.commandTimeouts.removeAll()
                    self.media.cancel()
                    if self.closing { self.finishNativeClose() }
                    else if task.terminationStatus != 0 && self.fatalError == nil { self.fatalError = "The workflow backend stopped. Open native.log for details, then reopen CamPresso." }
                }
            }
            process = task; try task.run(); connected = true
            Task { await initialize() }
        } catch { fatalError = error.localizedDescription }
    }
    private func receive(_ packet: JSONObject) {
        guard number(packet["protocol"]) == 1 else { fatalError = "App/backend versions differ. Reinstall CamPresso."; return }
        let type = string(packet["type"])
        if type == "reply" {
            let id = string(packet["id"])
            commandTimeouts.removeValue(forKey: id)?.cancel()
            guard let continuation = replies.removeValue(forKey: id) else { return }
            if packet["error"] != nil { continuation.resume(throwing: WorkflowError(message: string(object(packet["error"])["message"]))) }
            else { continuation.resume(returning: object(packet["result"])) }
        } else if type == "native.request" {
            let id = string(packet["id"]), method = string(packet["method"]), params = object(packet["params"])
            let writer = writer, media = media
            Task {
                do {
                    let result = try await media.perform(method, params) { stage, percent in
                        writer.send(["type": "native.progress", "id": id, "stage": stage, "percent": percent as Any? ?? NSNull()])
                    }
                    writer.send(["type": "native.reply", "id": id, "result": result])
                } catch {
                    let problem = error as? WorkflowError
                    writer.send(["type": "native.reply", "id": id, "error": ["message": error.localizedDescription,
                        "code": error is CancellationError ? "cancelled" : problem?.code ?? "native_error", "uncertain": problem?.uncertain ?? false]])
                }
            }
        } else if type == "event" {
            switch string(packet["event"]) {
            case "snapshot":
                let value = object(packet["data"])
                let revision = Int(number(value["revision"]) ?? 0)
                var updated: [VideoItem] = []
                var indexed: [String: VideoItem] = [:]
                for raw in value["rows"] as? [JSONObject] ?? [] {
                    let row = VideoRow(raw: raw)
                    guard !row.id.isEmpty, indexed[row.id] == nil else { continue }
                    let item = itemsByID[row.id] ?? VideoItem(row, revision: revision)
                    item.replace(row, revision: revision)
                    updated.append(item); indexed[row.id] = item
                }
                itemsByID = indexed; items = updated
                listRevision += 1; filteredCache = nil
                if library != string(value["library"]) || scanning && value["scanning"] as? Bool != true {
                    thumbnailRevision += 1
                }
                preferences = object(value["preferences"]); library = string(value["library"])
                scanning = value["scanning"] as? Bool == true; paused = value["paused"] as? Bool == true
                catalogError = value["catalog_error"] as? String; fatalError = value["fatal_error"] as? String
                runIDs = Set(value["run_ids"] as? [String] ?? [])
                cleaning = value["cleaning"] as? Bool == true
                retainedFiles = value["retained_files"] as? [JSONObject] ?? []
            case "job":
                let row = VideoRow(raw: object(packet["row"]))
                let revision = Int(number(packet["revision"]) ?? 0)
                objectWillChange.send()
                if let item = itemsByID[row.id] {
                    if galleryOrderChanged(from: item.row, to: row) { listRevision += 1; filteredCache = nil }
                    item.replace(row, revision: revision)
                }
                else {
                    listRevision += 1; filteredCache = nil
                    let item = VideoItem(row, revision: revision)
                    itemsByID[row.id] = item; items.append(item)
                }
            case "progress":
                itemsByID[string(packet["row_id"])]?.progress(packet)
            case "notice": notice = string(packet["message"])
            case "fatal": fatalError = string(packet["message"])
            case "shutdownReady": finishNativeClose()
            default: break
            }
        }
    }
    func acceptPacket(_ packet: JSONObject) { receive(packet) }
    func request(_ method: String, _ params: JSONObject = [:]) async throws -> JSONObject {
        guard connected else { throw WorkflowError(message: "The workflow backend is not connected.") }
        let id = UUID().uuidString
        return try await withCheckedThrowingContinuation { continuation in
            replies[id] = continuation
            writer.send(["type": "request", "id": id, "method": method, "params": params])
            commandTimeouts[id] = Task { [weak self] in
                try? await Task.sleep(for: .seconds(1800))
                guard !Task.isCancelled, let self, let continuation = self.replies.removeValue(forKey: id) else { return }
                continuation.resume(throwing: WorkflowError(message: "This operation has not finished. Inspect Photos status before retrying."))
                self.commandTimeouts.removeValue(forKey: id)
            }
        }
    }
    func initialize() async {
        do { _ = try await request("initialize") } catch { notice = error.localizedDescription }
    }
    func allowPhotos() {
        authorizing = true
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { [weak self] status in
            Task { @MainActor in
                self?.authorizing = false; self?.photoAuthorized = status == .authorized
                if status == .authorized { AssetRepository.shared.register(); await self?.refresh() }
                else { self?.notice = "Allow Full Photos Access in System Settings → Privacy & Security → Photos." }
            }
        }
    }
    func refresh() async { do { _ = try await request("catalog.refresh") } catch { notice = error.localizedDescription } }
    func action(_ method: String, _ params: JSONObject = [:]) {
        Task { do { _ = try await request(method, params) } catch { notice = error.localizedDescription } }
    }
    func compress() { action("jobs.enqueue", ["ids": compressionTargets.map(\.id)]) }
    func clearHistory() {
        cleaning = true
        Task {
            defer { cleaning = false }
            do {
                let preview = try await request("history.previewCleanup")
                let dialog = NSAlert()
                dialog.messageText = "Clear conversion history?"
                dialog.informativeText = "Remove \(Int(number(preview["records"]) ?? 0)) finished records and up to \(humanSize(number(preview["bytes"]))) of safe temporary files.\n\n\(Int(number(preview["protected_count"]) ?? 0)) current/archive safety records and \(Int(number(preview["retained_count"]) ?? 0)) changed/external files are protected. Apple Photos videos, settings and journals stay unchanged."
                dialog.addButton(withTitle: "Clear History and Temporary Files")
                dialog.addButton(withTitle: "Cancel")
                guard dialog.runModal() == .alertFirstButtonReturn else { return }
                let result = try await request("history.clear", ["token": string(preview["token"])])
                let retained = result["retained"] as? [String] ?? []
                notice = "Cleared \(Int(number(result["records"]) ?? 0)) records and \(humanSize(number(result["removed_bytes"])))." +
                    (retained.isEmpty ? "" : "\n\nSome files were retained. Run cleanup again to retry:\n" + retained.joined(separator: "\n"))
            } catch { notice = error.localizedDescription }
        }
    }
    func review(_ row: VideoRow) {
        Task {
            do {
                let value = try await request("review.open", ["job_id": row.jobID])
                let session = ReviewSession(model: self, initial: value)
                let controller = NSHostingController(rootView: ReviewView(session: session).campressoTheme())
                let window = NSWindow(contentViewController: controller)
                window.title = "Review — \(row.name)"; window.setContentSize(.init(width: 1020, height: 720)); window.center()
                window.isReleasedWhenClosed = false; reviewWindow?.close(); reviewWindow = window
                window.makeKeyAndOrderFront(nil)
            } catch { notice = error.localizedDescription }
        }
    }
    func decide(_ values: [JSONObject], _ decision: String) async throws -> Bool {
        guard !values.isEmpty else { return false }
        let total = values.reduce(0.0) { $0 + max(0, (number($1["source_size"]) ?? 0)-(number($1["result_size"]) ?? 0)) }
        let dialog = NSAlert()
        dialog.messageText = decision == "keep" ? "Keep \(values.count) compressed video(s)?" : "Keep \(values.count) original video(s)?"
        dialog.informativeText = decision == "keep"
            ? "The matching originals will move to Recently Deleted. Library size reduction: \(humanSize(total)). This is not immediately reclaimed disk space. Changes affect devices using iCloud Photos."
            : "Only the generated compressed copies will move to Recently Deleted. The matching originals stay in Photos. Proposed library size reduction being discarded: \(humanSize(total))."
        dialog.informativeText += "\n\nRemoved videos normally remain recoverable for 30 days. Permanent deletion remains manual."
        dialog.addButton(withTitle: decision == "keep" ? "Keep Compressed" : "Revert")
        dialog.addButton(withTitle: "Cancel")
        guard dialog.runModal() == .alertFirstButtonReturn else { return false }
        _ = try await request("review.decide", ["decision": decision, "reviews": values])
        return true
    }
    func decideSelected(_ decision: String) {
        let ids = chosen.filter { $0.state == "Ready to review" }.map(\.jobID)
        Task {
            do {
                let response = try await request("review.batch", ["job_ids": ids])
                _ = try await decide(response["reviews"] as? [JSONObject] ?? [], decision)
            } catch { notice = error.localizedDescription }
        }
    }
    func showPhotos(_ row: VideoRow) {
        let identifier = row.state == "Completed" && string(row.raw["decision"]) == "keep" ? row.resultID : row.originalID
        action("photos.show", ["id": identifier])
    }
    func play(_ row: VideoRow, compressed: Bool = false) {
        guard !closing else { return }
        let useCompressed = compressed || row.state == "Completed" && string(row.raw["decision"]) == "keep"
        let identifier = useCompressed ? row.resultID : row.originalID
        guard !identifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            notice = useCompressed
                ? "No compressed copy is recorded in Photos. Inspect Photos status or finish adding the saved result before playing it."
                : "The original Photos identifier is unavailable. Refresh the library or inspect this video's status."
            return
        }
        let id = UUID()
        let controller = PlaybackWindowController(identifier: identifier,
            title: "\(useCompressed ? "Compressed" : "Original") — \(row.name)") { [weak self] in
                self?.playbackWindows.removeValue(forKey: id)
            }
        playbackWindows[id] = controller; controller.open()
    }
    func beginClose() -> Bool {
        if closing { return false }
        if !processing.isEmpty {
            let dialog = NSAlert(); dialog.messageText = "Stop and close CamPresso?"
            dialog.informativeText = "Downloads and conversions will stop. Next launch starts with a fresh queue and no previous reviews. Videos already added to Photos stay there. An active Photos change will finish safely first."
            dialog.addButton(withTitle: "Stop and Close"); dialog.addButton(withTitle: "Keep Working")
            guard dialog.runModal() == .alertFirstButtonReturn else { return false }
        }
        closing = true
        for controller in Array(playbackWindows.values) { controller.close() }
        if !connected {
            if media.hasWork { finishNativeClose(); return false }
            media.shutdown(); return true
        }
        Task { do { _ = try await request("shutdown") } catch { notice = error.localizedDescription } }
        return false
    }
    func finishNativeClose() {
        media.cancel()
        Task {
            while media.hasWork { try? await Task.sleep(for: .milliseconds(100)) }
            guard !closeReplied else { return }; closeReplied = true
            media.shutdown(); writer.close(); NSApp.reply(toApplicationShouldTerminate: true)
        }
    }
}

// The framing queue owns its buffer, avoiding work on SwiftUI's main thread.
final class PacketReader: @unchecked Sendable {
    private var buffer = Data()
    let queue = DispatchQueue(label: "campresso.decode")
    var callback: ((JSONObject) -> Void)?
    func append(_ data: Data) {
        queue.async {
            self.buffer.append(data)
            while let end = self.buffer.firstIndex(of: 10) {
                let line = self.buffer[..<end]; self.buffer.removeSubrange(...end)
                if let packet = try? JSONSerialization.jsonObject(with: Data(line)) as? JSONObject { self.callback?(packet) }
            }
        }
    }
}
