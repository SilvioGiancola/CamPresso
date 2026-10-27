import AppKit
import AVFoundation
import CryptoKit
import Darwin
import Foundation
import Photos

typealias JSONObject = [String: Any]

struct WorkflowError: LocalizedError {
    let message: String
    var code: String = "native_error"
    var uncertain = false
    var errorDescription: String? { message }
}

func object(_ value: Any?) -> JSONObject { value as? JSONObject ?? [:] }
func string(_ value: Any?) -> String { value as? String ?? "" }
func number(_ value: Any?) -> Double? { (value as? NSNumber)?.doubleValue }
func key(_ identifier: String) -> String { identifier.components(separatedBy: "/")[0].uppercased() }
func sameJSON(_ lhs: Any, _ rhs: Any) -> Bool {
    guard JSONSerialization.isValidJSONObject(lhs), JSONSerialization.isValidJSONObject(rhs),
          let a = try? JSONSerialization.data(withJSONObject: lhs, options: .sortedKeys),
          let b = try? JSONSerialization.data(withJSONObject: rhs, options: .sortedKeys) else { return false }
    return a == b
}
func humanSize(_ value: Double?) -> String {
    guard let value else { return "Unknown size" }
    return ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .decimal)
}
func durationText(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    let seconds = Int(value)
    return seconds >= 3600 ? String(format: "%d:%02d:%02d", seconds/3600, seconds/60%60, seconds%60)
                          : String(format: "%d:%02d", seconds/60, seconds%60)
}
func sha256(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hash = SHA256()
    while let data = try handle.read(upToCount: 4 * 1024 * 1024), !data.isEmpty { hash.update(data: data) }
    return hash.finalize().map { String(format: "%02x", $0) }.joined()
}
func atomicJSON(_ value: JSONObject, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    try data.write(to: url, options: .atomic)
    let handle = try FileHandle(forWritingTo: url)
    try handle.synchronize()
    try handle.close()
    let descriptor = Darwin.open(url.deletingLastPathComponent().path, O_RDONLY)
    if descriptor >= 0 { _ = fsync(descriptor); _ = Darwin.close(descriptor) }
}

final class CancellationFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    private var callbacks: [() -> Void] = []
    var isCancelled: Bool { lock.lock(); defer { lock.unlock() }; return cancelled }
    func check() throws { if isCancelled { throw CancellationError() } }
    func add(_ callback: @escaping () -> Void) {
        lock.lock()
        if cancelled { lock.unlock(); callback() }
        else { callbacks.append(callback); lock.unlock() }
    }
    func cancel() {
        lock.lock(); cancelled = true; let current = callbacks; callbacks.removeAll(); lock.unlock()
        current.forEach { $0() }
    }
}

final class Once<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Value, Error>?
    init(_ continuation: CheckedContinuation<Value, Error>) { self.continuation = continuation }
    func finish(_ result: Result<Value, Error>) {
        lock.lock(); let value = continuation; continuation = nil; lock.unlock()
        value?.resume(with: result)
    }
}

final class AssetRepository: NSObject, PHPhotoLibraryChangeObserver, @unchecked Sendable {
    static let shared = AssetRepository()
    private let lock = NSLock()
    private var identifiers: [String: String] = [:]
    private var registered = false

    func register() {
        lock.lock(); let should = !registered; registered = true; lock.unlock()
        if should { PHPhotoLibrary.shared().register(self) }
    }
    func photoLibraryDidChange(_ changeInstance: PHChange) {
        lock.lock(); identifiers.removeAll(); lock.unlock()
        NotificationCenter.default.post(name: .init("PhotosChanged"), object: nil)
    }
    func find(_ identifier: String, missingOK: Bool = false) throws -> PHAsset? {
        let options = PHFetchOptions(); options.includeHiddenAssets = true
        var full = identifier
        if !identifier.contains("/") {
            lock.lock(); let cached = identifiers[key(identifier)]; lock.unlock()
            if let cached { full = cached }
            else {
                options.predicate = NSPredicate(format: "mediaType == %d", PHAssetMediaType.video.rawValue)
                let assets = PHAsset.fetchAssets(with: options)
                var mapping: [String: String] = [:]
                assets.enumerateObjects { asset, _, _ in mapping[key(asset.localIdentifier)] = asset.localIdentifier }
                lock.lock(); identifiers = mapping; lock.unlock()
                full = mapping[key(identifier)] ?? identifier
            }
        }
        let result = PHAsset.fetchAssets(withLocalIdentifiers: [full], options: options).firstObject
        if result == nil && !missingOK { throw WorkflowError(message: "The exact Photos video is unavailable. Refresh or inspect its status.") }
        if let result, result.mediaType != .video { throw WorkflowError(message: "The selected Photos item is not a video.") }
        return result
    }
    func version(_ asset: PHAsset) -> JSONObject {
        ["id": asset.localIdentifier, "modified": asset.modificationDate?.timeIntervalSince1970 ?? 0,
         "width": asset.pixelWidth, "height": asset.pixelHeight, "duration": asset.duration,
         "favorite": asset.isFavorite, "hidden": asset.isHidden]
    }
    func validate(_ asset: PHAsset, expected: JSONObject) throws {
        guard let duration = number(expected["duration"]), duration > 0,
              abs(asset.duration-duration) <= max(0.5, duration*0.005),
              asset.pixelWidth == Int(number(expected["width"]) ?? 0),
              asset.pixelHeight == Int(number(expected["height"]) ?? 0) else {
            throw WorkflowError(message: "The recorded compressed Photos video no longer matches its verified result.")
        }
    }
    func thumbnail(_ identifier: String) async -> NSImage? {
        guard let asset = try? find(identifier) else { return nil }
        return try? await withCheckedThrowingContinuation { continuation in
            let once = Once<NSImage?>(continuation)
            let options = PHImageRequestOptions(); options.isNetworkAccessAllowed = false
            options.deliveryMode = .fastFormat; options.resizeMode = .fast
            PHCachingImageManager.default().requestImage(for: asset, targetSize: .init(width: 400, height: 260), contentMode: .aspectFill, options: options) { image, _ in
                once.finish(.success(image))
            }
        }
    }
    func player(_ identifier: String) async throws -> AVPlayerItem {
        guard let asset = try find(identifier) else { throw WorkflowError(message: "Video unavailable") }
        let cancellation = CancellationFlag()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let once = Once(continuation)
                let options = PHVideoRequestOptions(); options.version = .current; options.isNetworkAccessAllowed = true
                options.deliveryMode = .highQualityFormat
                let request = PHImageManager.default().requestPlayerItem(forVideo: asset, options: options) { item, info in
                    if let item { once.finish(.success(item)) }
                    else { once.finish(.failure((info?[PHImageErrorKey] as? Error) ?? WorkflowError(message: "Cannot load this video. It may need an iCloud connection."))) }
                }
                cancellation.add { PHImageManager.default().cancelImageRequest(request); once.finish(.failure(CancellationError())) }
            }
        } onCancel: { cancellation.cancel() }
    }
}
