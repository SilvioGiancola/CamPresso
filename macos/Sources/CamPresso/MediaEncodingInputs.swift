import AudioToolbox
import AVFoundation
import CoreGraphics
import CoreMedia

// Immutable, preloaded media properties transferred to one encoding worker.
// Tracks and format descriptions are read-only; the worker creates its own
// reader/writer and confines each stream to its dedicated serial queue.
struct MediaEncodingInputs: @unchecked Sendable {
    let videoTracks: [AVAssetTrack]
    let characteristics: [AVMediaCharacteristic]
    let nominalRate: Float
    let naturalSize: CGSize
    let preferredTransform: CGAffineTransform
    let metadata: [AVMetadataItem]
    let duration: CMTime
    let configuration: AVVideoComposition.Configuration
    let audioTracks: [AVAssetTrack]
    let trackGroups: [AVAssetTrackGroup]
    let descriptions: [CMPersistentTrackID: [CMFormatDescription]]
    let enabledAudioIDs: Set<CMPersistentTrackID>
    let audioFallbacks: [CMPersistentTrackID: [AVAssetTrack]]
    let ancillaryTracks: [AVAssetTrack]

    static func load(_ source: PreparedVideo, cancel: CancellationFlag) async throws -> MediaEncodingInputs {
        try cancel.check()
        let asset = source.asset
        let videoTracks = try await asset.loadTracks(withMediaType: .video)
        guard let primary = videoTracks.first else {
            throw WorkflowError(message: "The Photos rendition has no readable video track.", code: "unsupported")
        }
        let (formats, characteristics, nominalRate, naturalSize, transform) = try await primary.load(
            .formatDescriptions, .mediaCharacteristics, .nominalFrameRate, .naturalSize, .preferredTransform)
        guard !formats.isEmpty else {
            throw WorkflowError(message: "The Photos rendition has no readable video track.", code: "unsupported")
        }
        try cancel.check()
        let (metadata, duration, groups) = try await asset.load(.metadata, .duration, .trackGroups)
        let audioTracks = try await asset.loadTracks(withMediaType: .audio)
        var descriptions = [primary.trackID: formats]
        var enabledIDs = Set<CMPersistentTrackID>()
        for audio in audioTracks {
            try cancel.check()
            let (formats, enabled) = try await audio.load(.formatDescriptions, .isEnabled)
            descriptions[audio.trackID] = formats
            if enabled { enabledIDs.insert(audio.trackID) }
        }
        let spatial = audioTracks.contains { audio in
            (descriptions[audio.trackID] ?? []).contains { CMFormatDescriptionGetMediaSubType($0) == kAudioFormatAPAC }
        }
        var fallbacks: [CMPersistentTrackID: [AVAssetTrack]] = [:]
        if spatial, source.audioMix == nil, asset is AVURLAsset, source.range.start == .zero,
           abs(source.range.duration.seconds-duration.seconds) < 0.01 {
            for audio in audioTracks {
                try cancel.check()
                fallbacks[audio.trackID] = try await audio.loadAssociatedTracks(ofType: .audioFallback)
            }
        }
        let ancillary = try await asset.load(.tracks).filter { [.timecode, .subtitle, .text, .closedCaption].contains($0.mediaType) }
        for track in ancillary {
            try cancel.check()
            // Optional ancillary streams keep the existing best-effort behavior;
            // a missing format becomes a visible review warning in the encoder.
            descriptions[track.trackID] = (try? await track.load(.formatDescriptions)) ?? []
        }
        let configuration: AVVideoComposition.Configuration
        if let existing = source.composition {
            configuration = AVVideoComposition.Configuration(
                animationTool: existing.animationTool, colorPrimaries: existing.colorPrimaries,
                colorTransferFunction: existing.colorTransferFunction, colorYCbCrMatrix: existing.colorYCbCrMatrix,
                customVideoCompositorClass: existing.customVideoCompositorClass, frameDuration: existing.frameDuration,
                instructions: existing.instructions, outputBufferDescription: existing.outputBufferDescription,
                perFrameHDRDisplayMetadataPolicy: existing.perFrameHDRDisplayMetadataPolicy,
                renderScale: existing.renderScale, renderSize: existing.renderSize,
                sourceSampleDataTrackIDs: existing.sourceSampleDataTrackIDs,
                sourceTrackIDForFrameTiming: existing.sourceTrackIDForFrameTiming,
                spatialVideoConfigurations: existing.spatialVideoConfigurations)
        } else {
            configuration = try await AVVideoComposition.Configuration(for: asset)
        }
        try cancel.check()
        return MediaEncodingInputs(videoTracks: videoTracks, characteristics: characteristics, nominalRate: nominalRate,
            naturalSize: naturalSize, preferredTransform: transform, metadata: metadata, duration: duration,
            configuration: configuration, audioTracks: audioTracks, trackGroups: groups, descriptions: descriptions,
            enabledAudioIDs: enabledIDs, audioFallbacks: fallbacks, ancillaryTracks: ancillary)
    }
}
