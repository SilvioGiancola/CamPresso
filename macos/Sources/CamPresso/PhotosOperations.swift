import AppKit
import AVFoundation
import Foundation
import Photos

actor PhotosTransactionGate {
    private var occupied = false
    private var waiting: [CheckedContinuation<Void, Never>] = []
    func enter() async {
        if !occupied { occupied = true; return }
        await withCheckedContinuation { waiting.append($0) }
    }
    func leave() {
        if waiting.isEmpty { occupied = false }
        else { waiting.removeFirst().resume() }
    }
}

// One encoder owns this immutable rendition; cancellation uses framework APIs.
final class PreparedVideo: @unchecked Sendable {
    let asset: AVAsset
    let source: PHAsset
    let session: AVAssetExportSession
    let range: CMTimeRange
    let composition: AVVideoComposition?
    let audioMix: AVAudioMix?
    let version: JSONObject
    let temporary: URL?
    init(asset: AVAsset, source: PHAsset, session: AVAssetExportSession, duration: CMTime, temporary: URL? = nil) {
        self.asset = asset; self.source = source; self.session = session; self.temporary = temporary
        let range = session.timeRange
        self.range = temporary != nil || !range.isValid || !range.duration.seconds.isFinite || range.duration.seconds <= 0
            ? CMTimeRange(start: .zero, duration: duration) : range
        self.composition = temporary == nil ? session.videoComposition : nil
        self.audioMix = temporary == nil ? session.audioMix : nil
        self.version = AssetRepository.shared.version(source)
    }
}

final class NativeMedia: @unchecked Sendable {
    let data: URL
    let repository = AssetRepository.shared
    private let lock = NSLock()
    private var prepared: [String: PreparedVideo] = [:]
    private var flags: [String: CancellationFlag] = [:]
    private var operationCount = 0
    private let transactions = PhotosTransactionGate()
    var hasWork: Bool { lock.lock(); defer { lock.unlock() }; return operationCount > 0 }

    init(data: URL) { self.data = data.resolvingSymlinksInPath() }

    func authorized() throws {
        guard PHPhotoLibrary.authorizationStatus(for: .readWrite) == .authorized else {
            throw WorkflowError(message: "Full Photos access is required. Click Allow Photos Access, or update Privacy & Security → Photos.", code: "permission")
        }
        repository.register()
    }
    func flag(_ id: String, reset: Bool = false) -> CancellationFlag {
        lock.lock(); defer { lock.unlock() }
        if reset || flags[id] == nil { flags[id] = CancellationFlag() }
        return flags[id]!
    }
    func item(_ id: String) throws -> PreparedVideo {
        lock.lock(); defer { lock.unlock() }
        guard let item = prepared[id] else { throw WorkflowError(message: "The prepared video expired. Resume to retrieve it again.") }
        return item
    }
    func release(_ id: String) {
        lock.lock(); let item = prepared.removeValue(forKey: id); flags.removeValue(forKey: id); lock.unlock()
        if let temporary = item?.temporary { try? FileManager.default.removeItem(at: temporary) }
    }
    func cancel() {
        lock.lock(); let current = Array(flags.values); lock.unlock()
        current.forEach { $0.cancel() }
    }
    func shutdown() {
        cancel()
        lock.lock(); let ids = Array(prepared.keys); lock.unlock()
        ids.forEach { release($0) }
    }
    func safeFile(_ path: String) throws -> URL {
        let url = URL(fileURLWithPath: path).standardizedFileURL
        let root = data.appendingPathComponent("NativeWork").resolvingSymlinksInPath()
        let resolved = url.resolvingSymlinksInPath()
        guard resolved.path.hasPrefix(root.path + "/"), url.path == resolved.path,
              !url.pathComponents.contains(where: { $0.lowercased().hasSuffix(".photoslibrary") }) else {
            throw WorkflowError(message: "Working files must remain inside CamPresso’s temporary folder.")
        }
        return url
    }
    func ensureSpace(_ bytes: Int64) throws {
        let values = try data.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        let free = values.volumeAvailableCapacityForImportantUsage ?? 0
        guard free > bytes + 2 * 1024 * 1024 * 1024 else {
            throw WorkflowError(message: "Not enough local space for this video. Smaller/local items can continue; free space, then Resume.", code: "storage")
        }
    }
    func perform(_ method: String, _ params: JSONObject, progress: @escaping (String, Double?) -> Void) async throws -> JSONObject {
        if method == "storage.status" { return ["busy": hasWork] }
        if method == "cancel" {
            if !string(params["job_id"]).isEmpty { flag(string(params["job_id"])).cancel() }
            else { cancel() }
            return [:]
        }
        lock.withLock { operationCount += 1 }
        defer { lock.withLock { operationCount -= 1 } }
        try authorized()
        switch method {
        case "prepare": return try await prepare(params, progress: progress)
        case "encode":
            let id = string(params["job_id"])
            let item = try item(id)
            let token = flag(id)
            defer { release(id) }
            return try await withCheckedThrowingContinuation { continuation in
                let encoding = Task.detached(priority: .utility) {
                    do { continuation.resume(returning: try await NativeEncoder.encode(item, params: params, media: self, cancel: token, progress: progress)) }
                    catch { continuation.resume(throwing: error) }
                }
                token.add { encoding.cancel() }
            }
        case "import", "decide", "inspect":
            await transactions.enter()
            do {
                let result: JSONObject
                if method == "import" { result = try await importVideo(params) }
                else if method == "decide" { result = try await decide(params) }
                else { result = try inspect(params) }
                await transactions.leave()
                return result
            } catch { await transactions.leave(); throw error }
        case "review": return try review(params)
        default: throw WorkflowError(message: "Unknown native operation: \(method)")
        }
    }

    private func prepare(_ params: JSONObject, progress: @escaping (String, Double?) -> Void) async throws -> JSONObject {
        let id = string(params["job_id"])
        release(id)
        let cancellation = flag(id, reset: true)
        guard let source = try repository.find(string(params["original_id"])) else { throw WorkflowError(message: "Original unavailable") }
        if source.sourceType.contains(.typeCloudShared) { throw WorkflowError(message: "Shared Albums are browse/play-only in this version.", code: "unsupported") }
        let before = repository.version(source)
        try ensureSpace(Int64(number(params["source_size"]) ?? 0) * 2)
        let session: AVAssetExportSession = try await withCheckedThrowingContinuation { continuation in
            let once = Once(continuation)
            let options = PHVideoRequestOptions(); options.version = .current
            options.deliveryMode = .highQualityFormat; options.isNetworkAccessAllowed = true
            options.progressHandler = { value, error, _, _ in
                if let error { progress("Downloading: \(error.localizedDescription)", nil) }
                else { progress("Downloading original", value * 100) }
            }
            let request = PHImageManager.default().requestExportSession(forVideo: source, options: options, exportPreset: AVAssetExportPresetHEVCHighestQuality) { session, info in
                if let session { once.finish(.success(session)) }
                else {
                    let error = info?[PHImageErrorKey] as? NSError
                    let description = error?.localizedDescription ?? "The iCloud video is unavailable. Local videos will continue; retry this video later."
                    once.finish(.failure(WorkflowError(message: description, code: "network")))
                }
            }
            cancellation.add { PHImageManager.default().cancelImageRequest(request); once.finish(.failure(CancellationError())) }
        }
        try cancellation.check()
        var asset = session.asset
        var temporary: URL?
        var retained = false
        defer { if !retained, let temporary { try? FileManager.default.removeItem(at: temporary) } }
        // Photos owns the rendering of custom/third-party edits. Never substitute
        // the unedited resource URL when the provided compositor cannot be reused.
        if session.videoComposition?.customVideoCompositorClass != nil {
            let folder = data.appendingPathComponent("NativeWork/\(id)/\(string(params["generation"]))")
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let rendered = try safeFile(folder.appendingPathComponent("photos-rendered.mov").path)
            temporary = rendered
            try ensureSpace(Int64(number(params["source_size"]) ?? 0) * 4)
            try? FileManager.default.removeItem(at: rendered)
            cancellation.add { session.cancelExport() }
            progress("Rendering Photos edits", nil)
            do {
                try await session.export(to: rendered, as: .mov)
            } catch {
                try cancellation.check()
                throw WorkflowError(message: "Photos could not render these edits. Original kept. \(error.localizedDescription)", code: "unsupported")
            }
            try cancellation.check()
            temporary = rendered; asset = AVURLAsset(url: rendered)
        }
        guard let fresh = try repository.find(source.localIdentifier), sameJSON(before, repository.version(fresh)) else {
            throw WorkflowError(message: "The Photos video changed during preparation. Refresh and retry.")
        }
        let (readable, protected, duration) = try await asset.load(.isReadable, .hasProtectedContent, .duration)
        let videoTracks = try await asset.loadTracks(withMediaType: .video)
        guard readable, !protected, !videoTracks.isEmpty else {
            throw WorkflowError(message: "This protected or unreadable video cannot be compressed.", code: "unsupported")
        }
        let item = PreparedVideo(asset: asset, source: fresh, session: session, duration: duration, temporary: temporary)
        try cancellation.check()
        lock.withLock { prepared[id] = item }
        retained = true
        return ["original_id": fresh.localIdentifier, "original_version": item.version]
    }

    private func journalURL(_ params: JSONObject) throws -> URL {
        let operation = string(params["operation_id"])
        guard operation.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil else { throw WorkflowError(message: "Invalid native operation identity") }
        return data.appendingPathComponent("NativeJournals/\(operation).json")
    }
    private func loadJournal(_ url: URL) throws -> JSONObject {
        object(try JSONSerialization.jsonObject(with: Data(contentsOf: url)))
    }
    private func checkedReceipt(_ params: JSONObject) throws -> (URL, JSONObject?) {
        let url = try journalURL(params)
        guard FileManager.default.fileExists(atPath: url.path) else { return (url, nil) }
        let receipt = try loadJournal(url)
        guard string(receipt["job_id"]) == string(params["job_id"]), string(receipt["generation"]) == string(params["generation"]) else {
            throw WorkflowError(message: "The journal belongs to another reviewed generation.", uncertain: true)
        }
        guard string(receipt["library"]) == string(params["library"]),
              key(string(receipt["original_id"])) == key(string(params["original_id"])) else {
            throw WorkflowError(message: "The recorded Photos operation belongs to a different library or original.", uncertain: true)
        }
        if !string(params["sha256"]).isEmpty && string(params["sha256"]) != string(receipt["sha256"]) {
            throw WorkflowError(message: "The recorded import belongs to a different output.", uncertain: true)
        }
        if !string(params["decision"]).isEmpty && (string(receipt["action"]) != string(params["decision"])
            || string(receipt["result_id"]) != string(params["result_id"])) {
            throw WorkflowError(message: "The recorded decision belongs to a different Photos pair.", uncertain: true)
        }
        if string(receipt["phase"]) != "Complete" {
            throw WorkflowError(message: "This Photos operation has an unresolved journal. Inspect it before another attempt.", uncertain: true)
        }
        return (url, object(receipt["result"]))
    }
    private func change(_ body: @escaping () -> Void) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            PHPhotoLibrary.shared().performChanges(body) { success, error in
                if success { continuation.resume() }
                else { continuation.resume(throwing: error ?? WorkflowError(message: "Photos rejected the change.", uncertain: true)) }
            }
        }
    }
    private func albums(_ asset: PHAsset) -> [PHAssetCollection] {
        let fetched = PHAssetCollection.fetchAssetCollectionsContaining(asset, with: .album, options: nil)
        var result: [PHAssetCollection] = []
        fetched.enumerateObjects { album, _, _ in
            if album.assetCollectionSubtype == .albumRegular && album.canPerform(.addContent) { result.append(album) }
        }
        return result
    }
    private func copyMetadata(_ original: PHAsset, to request: PHAssetChangeRequest) {
        request.creationDate = original.creationDate; request.location = original.location
        request.isFavorite = original.isFavorite; request.isHidden = original.isHidden
    }
    private func importVideo(_ params: JSONObject) async throws -> JSONObject {
        let (url, existing) = try checkedReceipt(params)
        if let existing { return existing }
        let output = try safeFile(string(params["output"]))
        guard try sha256(output) == string(params["sha256"]) else { throw WorkflowError(message: "Compressed output changed before import.") }
        guard let original = try repository.find(string(params["original_id"])) else { throw WorkflowError(message: "Original missing") }
        guard !original.sourceType.contains(.typeCloudShared) else { throw WorkflowError(message: "Shared Albums are browse/play-only.", code: "unsupported") }
        let version = repository.version(original)
        let expectedVersion = object(params["original_version"])
        if !expectedVersion.isEmpty && !sameJSON(version, expectedVersion) { throw WorkflowError(message: "The original changed after compression. Recompress before importing.") }
        guard let size = number(params["source_size"]), size > 0,
              Double(try output.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) < size else {
            throw WorkflowError(message: "The compressed output is not proven smaller. Original kept.")
        }
        let expected = object(params["expected"])
        let collections = albums(original)
        var receipt: JSONObject = ["operation_id": string(params["operation_id"]), "action": "import", "job_id": string(params["job_id"]), "library": string(params["library"]),
                                  "generation": string(params["generation"]), "original_id": original.localIdentifier,
                                  "original_version": version, "expected": expected, "sha256": string(params["sha256"]),
                                  "output": output.path, "phase": "Import pending"]
        try atomicJSON(receipt, to: url)
        var placeholder: String?
        var checkpointError: Error?
        do {
            try await change {
                let request = PHAssetCreationRequest.forAsset()
                let options = PHAssetResourceCreationOptions(); options.shouldMoveFile = false
                let requested = URL(fileURLWithPath: string(params["name"])).deletingPathExtension().lastPathComponent
                options.originalFilename = (requested.isEmpty ? "video" : requested) + "." + output.pathExtension
                request.addResource(with: .video, fileURL: output, options: options)
                self.copyMetadata(original, to: request)
                if let created = request.placeholderForCreatedAsset {
                    placeholder = created.localIdentifier
                    receipt["result_id"] = created.localIdentifier
                    do { try atomicJSON(receipt, to: url) } catch { checkpointError = error }
                    for album in collections { PHAssetCollectionChangeRequest(for: album)?.addAssets([created] as NSArray) }
                }
            }
            if let checkpointError { throw checkpointError }
            guard let identifier = placeholder, let result = try repository.find(identifier) else { throw WorkflowError(message: "Photos did not return a verifiable imported identifier.") }
            try repository.validate(result, expected: expected)
            let response: JSONObject = ["original_id": original.localIdentifier, "result_id": result.localIdentifier,
                                       "original_version": version, "result_version": repository.version(result)]
            receipt["phase"] = "Complete"; receipt["result"] = response
            try atomicJSON(receipt, to: url)
            return response
        } catch {
            receipt["error"] = error.localizedDescription; try? atomicJSON(receipt, to: url)
            throw WorkflowError(message: "Photos import needs inspection: \(error.localizedDescription)", uncertain: true)
        }
    }
    private func review(_ params: JSONObject) throws -> JSONObject {
        guard let original = try repository.find(string(params["original_id"])), let result = try repository.find(string(params["result_id"])) else {
            throw WorkflowError(message: "Both versions must exist before review.")
        }
        guard original.localIdentifier != result.localIdentifier else { throw WorkflowError(message: "Original and compressed identifiers must differ.") }
        try repository.validate(result, expected: object(params["expected"]))
        return ["original_id": original.localIdentifier, "result_id": result.localIdentifier,
                "original_version": repository.version(original), "result_version": repository.version(result)]
    }
    private func decide(_ params: JSONObject) async throws -> JSONObject {
        let (url, existing) = try checkedReceipt(params)
        if let existing { return existing }
        let current = try review(params)
        let approval = object(params["approval"])
        for field in ["original_id", "result_id"] {
            guard string(current[field]) == string(approval[field]) else { throw WorkflowError(message: "The selected review identifiers changed.") }
        }
        for field in ["original_version", "result_version"] {
            guard sameJSON(object(current[field]), object(approval[field])) else { throw WorkflowError(message: "A video changed in Photos. Inspect and review it again before deciding.") }
        }
        guard string(approval["generation"]) == string(params["generation"]), !string(approval["token"]).isEmpty else { throw WorkflowError(message: "Missing review approval") }
        let keep = string(params["decision"]) == "keep"
        guard keep || string(params["decision"]) == "revert" else { throw WorkflowError(message: "Unknown review decision") }
        guard let original = try repository.find(string(current["original_id"])),
              let result = try repository.find(string(current["result_id"])),
              !original.sourceType.contains(.typeCloudShared), !result.sourceType.contains(.typeCloudShared) else {
            throw WorkflowError(message: "Shared or missing videos cannot be changed.")
        }
        let target = keep ? original : result
        let collections = keep ? albums(original) : []
        var receipt = params
        receipt["action"] = keep ? "keep" : "revert"; receipt["phase"] = "Removal pending"
        receipt["original_id"] = original.localIdentifier; receipt["result_id"] = result.localIdentifier
        try atomicJSON(receipt, to: url)
        do {
            try await change {
                if keep {
                    let request = PHAssetChangeRequest(for: result)
                    self.copyMetadata(original, to: request)
                    for album in collections { PHAssetCollectionChangeRequest(for: album)?.addAssets([result] as NSArray) }
                }
                PHAssetChangeRequest.deleteAssets([target] as NSArray)
            }
            guard try repository.find(target.localIdentifier, missingOK: true) == nil else { throw WorkflowError(message: "Photos has not confirmed removal of the selected version.") }
            guard let survivor = try repository.find(keep ? result.localIdentifier : original.localIdentifier) else {
                throw WorkflowError(message: "The surviving Photos version is missing.")
            }
            if keep { try repository.validate(survivor, expected: object(params["expected"])) }
            let response: JSONObject = ["decision": keep ? "keep" : "revert", "removed_id": target.localIdentifier,
                                       "result_version": keep ? repository.version(survivor) : current["result_version"]!]
            receipt["phase"] = "Complete"; receipt["result"] = response
            try atomicJSON(receipt, to: url)
            return response
        } catch {
            receipt["error"] = error.localizedDescription; try? atomicJSON(receipt, to: url)
            throw WorkflowError(message: "The Photos decision needs inspection: \(error.localizedDescription)", uncertain: true)
        }
    }
    private func inspect(_ params: JSONObject) throws -> JSONObject {
        var resultID = string(params["result_id"])
        var receipt: JSONObject = [:]
        let operation = object(params["operation"])
        if !string(params["previous_work_id"]).isEmpty {
            let archive = try loadJournal(data.appendingPathComponent("PreviousWork.json"))
            let entries = archive["entries"] as? [JSONObject] ?? []
            guard let entry = entries.first(where: { string($0["id"]) == string(params["previous_work_id"]) }),
                  string(object(entry["job"])["id"]) == string(params["job_id"]),
                  string(object(entry["job"])["library"]) == string(params["library"]) else {
                throw WorkflowError(message: "Previous-work record identity does not match.")
            }
            if !string(entry["journal"]).isEmpty {
                receipt = try loadJournal(URL(fileURLWithPath: string(entry["journal"])))
                let nativeReceipt = !string(receipt["original_id"]).isEmpty
                guard key(string(receipt[nativeReceipt ? "original_id" : "uuid"])) == key(string(params["original_id"])),
                      string(receipt["library"]) == string(params["library"]) else {
                    throw WorkflowError(message: "The previous journal belongs to another video or library.")
                }
                if nativeReceipt {
                    guard string(receipt["job_id"]) == string(params["job_id"]),
                          string(receipt["generation"]) == string(params["generation"]) else {
                        throw WorkflowError(message: "Previous journal generation does not match.")
                    }
                }
                if resultID.isEmpty { resultID = string(receipt[nativeReceipt ? "result_id" : "new_uuid"]) }
            }
        } else if !string(operation["id"]).isEmpty {
            var request = params; request["operation_id"] = operation["id"]
            receipt = try loadJournal(journalURL(request))
            guard string(receipt["job_id"]) == string(params["job_id"]), string(receipt["generation"]) == string(params["generation"]),
                  string(receipt["library"]) == string(params["library"]),
                  key(string(receipt["original_id"])) == key(string(params["original_id"])) else {
                throw WorkflowError(message: "Recovery journal identity does not match this video.")
            }
            resultID = string(receipt["result_id"])
        } else if !string(params["legacy_journal"]).isEmpty {
            let candidate = try loadJournal(URL(fileURLWithPath: string(params["legacy_journal"])))
            if key(string(candidate["uuid"])) == key(string(params["original_id"])), string(candidate["library"]) == string(params["library"]) {
                receipt = candidate
                if resultID.isEmpty { resultID = string(candidate["new_uuid"]) }
            }
        }
        let original = try repository.find(string(params["original_id"]), missingOK: true)
        let result = resultID.isEmpty ? nil : try repository.find(resultID, missingOK: true)
        let expected = object(params["expected"])
        var response: JSONObject = ["original_present": original != nil, "result_present": result != nil, "verified": false]
        if let result {
            try repository.validate(result, expected: expected)
            response["result_id"] = result.localIdentifier; response["result_version"] = repository.version(result)
            response["verified"] = true
        }
        if let original { response["original_id"] = original.localIdentifier; response["original_version"] = repository.version(original) }
        let action = string(receipt["action"])
        if action == "keep", original == nil, result != nil { response["completed_decision"] = "keep" }
        else if action == "revert", original != nil, result == nil { response["completed_decision"] = "revert" }
        else if string(receipt["phase"]) == "Original moved to Recently Deleted", original == nil, result != nil { response["completed_decision"] = "keep" }
        if original == nil || result == nil { response["detail"] = "A recorded Photos version is missing. No import or removal was repeated. Check Photos and the journal." }
        return response
    }
}
