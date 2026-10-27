import AppKit
import CoreGraphics
import Photos
import SwiftUI

struct ThumbnailRequest: Hashable, Sendable {
    let identifier: String
    let width: Int
    let height: Int
    let revision: Int
    let downloaded: Bool
}

private actor ThumbnailSlots {
    private var active = 0
    private var waiting: [(UUID, CheckedContinuation<Bool, Never>)] = []

    func enter(_ id: UUID) async -> Bool {
        if Task.isCancelled { return false }
        if active < 3 { active += 1; return true }
        return await withTaskCancellationHandler {
            await withCheckedContinuation { waiting.append((id, $0)) }
        } onCancel: { Task { await self.cancel(id) } }
    }
    private func cancel(_ id: UUID) {
        guard let index = waiting.firstIndex(where: { $0.0 == id }) else { return }
        waiting.remove(at: index).1.resume(returning: false)
    }
    func leave() {
        if waiting.isEmpty { active -= 1 }
        else { waiting.removeFirst().1.resume(returning: true) }
    }
}

// PhotoKit's image is read only and is decoded on a dedicated queue before
// publishing a new immutable bitmap to SwiftUI.
private struct ThumbnailSource: @unchecked Sendable { let image: NSImage }

final class ThumbnailService: @unchecked Sendable {
    static let shared = ThumbnailService()
    private let slots = ThumbnailSlots()
    private let cache = NSCache<NSString, NSImage>()
    private let manager = PHCachingImageManager()
    private let lookupQueue = DispatchQueue(label: "campresso.thumbnail.lookup", qos: .userInitiated)
    private let decodeQueue = DispatchQueue(label: "campresso.thumbnail.decode", qos: .userInitiated)
    private let lock = NSLock()
    private var epoch = 0

    private init() {
        cache.totalCostLimit = 48 * 1024 * 1024
        cache.countLimit = 80
    }
    func invalidate() {
        lock.withLock { epoch += 1 }
        cache.removeAllObjects()
    }
    func image(_ request: ThumbnailRequest) async -> NSImage? {
        let epoch = lock.withLock { self.epoch }
        let cacheKey = "\(epoch):\(request.revision):\(request.identifier):\(request.width)x\(request.height)"
        if let image = cache.object(forKey: cacheKey as NSString) { return image }
        guard await slots.enter(UUID()) else { return nil }
        do {
            try Task.checkCancellation()
            let image = try await fetch(request, cacheKey: cacheKey)
            await slots.leave()
            return image
        } catch {
            await slots.leave()
            return nil
        }
    }
    private func fetch(_ request: ThumbnailRequest, cacheKey: String) async throws -> NSImage? {
        let cancellation = CancellationFlag()
        let timeout = Task {
            do { try await Task.sleep(for: .seconds(20)); cancellation.cancel() }
            catch { /* A completed or cancelled image request needs no timeout. */ }
        }
        defer { timeout.cancel() }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let once = Once<NSImage?>(continuation)
                cancellation.add { once.finish(.failure(CancellationError())) }
                lookupQueue.async {
                    do {
                        try cancellation.check()
                        guard PHPhotoLibrary.authorizationStatus(for: .readWrite) == .authorized else {
                            once.finish(.success(nil)); return
                        }
                        AssetRepository.shared.register()
                        guard let asset = try AssetRepository.shared.find(request.identifier) else {
                            once.finish(.success(nil)); return
                        }
                        try cancellation.check()
                        let options = PHImageRequestOptions()
                        options.version = .current
                        options.deliveryMode = .highQualityFormat
                        options.resizeMode = .exact
                        // A gallery must not download full cloud originals.
                        options.isNetworkAccessAllowed = false
                        let id = self.manager.requestImage(for: asset,
                            targetSize: .init(width: request.width, height: request.height),
                            contentMode: .aspectFill, options: options) { image, info in
                            if info?[PHImageResultIsDegradedKey] as? Bool == true { return }
                            guard let image, !cancellation.isCancelled else {
                                once.finish(.success(nil)); return
                            }
                            let source = ThumbnailSource(image: image)
                            self.decodeQueue.async {
                                guard !cancellation.isCancelled else { return }
                                let bitmap = Self.decode(source.image, request: request)
                                if let bitmap, !cancellation.isCancelled {
                                    self.cache.setObject(bitmap, forKey: cacheKey as NSString, cost: request.width * request.height * 4)
                                }
                                once.finish(.success(bitmap))
                            }
                        }
                        cancellation.add { self.manager.cancelImageRequest(id) }
                    } catch { once.finish(.failure(error)) }
                }
            }
        } onCancel: { cancellation.cancel() }
    }
    private static func decode(_ image: NSImage, request: ThumbnailRequest) -> NSImage? {
        autoreleasepool {
            guard let source = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
                  let context = CGContext(data: nil, width: request.width, height: request.height,
                    bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
            let scale = max(CGFloat(request.width)/CGFloat(source.width), CGFloat(request.height)/CGFloat(source.height))
            let width = CGFloat(source.width)*scale, height = CGFloat(source.height)*scale
            context.interpolationQuality = .high
            context.draw(source, in: .init(x: (CGFloat(request.width)-width)/2, y: (CGFloat(request.height)-height)/2,
                                          width: width, height: height))
            guard let bitmap = context.makeImage() else { return nil }
            return NSImage(cgImage: bitmap, size: .init(width: request.width, height: request.height))
        }
    }
}

struct VideoThumbnail: View {
    let identifier: String
    let revision: Int
    let downloaded: Bool
    @Environment(\.displayScale) private var displayScale
    @State private var image: NSImage?

    var body: some View {
        GeometryReader { geometry in
            let width = min(1536, max(320, Int(ceil(geometry.size.width * displayScale / 64)) * 64))
            let aspect = max(0.25, (geometry.size.width / max(1, geometry.size.height) * 8).rounded() / 8)
            let height = min(1536, max(1, Int(ceil(CGFloat(width) / aspect))))
            let request = ThumbnailRequest(identifier: identifier, width: width, height: height, revision: revision, downloaded: downloaded)
            ZStack {
                Color.black.opacity(0.08)
                if let image {
                    Image(nsImage: image).resizable().interpolation(.high).scaledToFill()
                        .frame(width: geometry.size.width, height: geometry.size.height).clipped()
                } else {
                    Image(systemName: "video.fill").font(.largeTitle).foregroundStyle(.tertiary)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .task(id: request) {
                let loaded = await ThumbnailService.shared.image(request)
                guard !Task.isCancelled else { return }
                image = loaded
            }
        }
        .clipped()
        .onDisappear { image = nil }
    }
}
