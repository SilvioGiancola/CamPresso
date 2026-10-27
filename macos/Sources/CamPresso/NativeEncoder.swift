import AudioToolbox
import AVFoundation
import CoreImage
import CoreMedia
import CoreVideo
import Foundation
import Photos
import VideoToolbox

enum NativeEncoder {
    static func encode(_ source: PreparedVideo, params: JSONObject, media: NativeMedia,
                       cancel: CancellationFlag, progress: @escaping (String, Double?) -> Void) async throws -> JSONObject {
        let inputs = try await MediaEncodingInputs.load(source, cancel: cancel)
        let result: JSONObject = try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    continuation.resume(returning: try encodeLoaded(source, inputs: inputs, params: params,
                        media: media, cancel: cancel, progress: progress))
                } catch { continuation.resume(throwing: error) }
            }
        }
        try cancel.check()
        if object(result["expected"])["hdr"] as? Bool == true {
            let encoded = AVURLAsset(url: try media.safeFile(string(params["output"])))
            let encodedTracks = try await encoded.loadTracks(withMediaType: .video)
            guard let encodedTrack = encodedTracks.first,
                  let description = try await encodedTrack.load(.formatDescriptions).first,
                  let sourceTrack = inputs.videoTracks.first,
                  let sourceDescription = inputs.descriptions[sourceTrack.trackID]?.first else {
                throw WorkflowError(message: "Cannot verify the encoded HDR track.")
            }
            let original = (CMFormatDescriptionGetExtensions(sourceDescription) as NSDictionary?) ?? [:]
            let output = (CMFormatDescriptionGetExtensions(description) as NSDictionary?) ?? [:]
            for field in [kCMFormatDescriptionExtension_TransferFunction, kCMFormatDescriptionExtension_ColorPrimaries,
                          kCMFormatDescriptionExtension_MasteringDisplayColorVolume, kCMFormatDescriptionExtension_ContentLightLevelInfo,
                          kCMFormatDescriptionExtension_AmbientViewingEnvironment] {
                if let wanted = original[field] as? NSObject, output[field] as? NSObject != wanted {
                    throw WorkflowError(message: "HDR verification failed: color or static HDR metadata did not survive. Original kept.", code: "unsupported")
                }
            }
        }
        try cancel.check()
        return result
    }

    private static func encodeLoaded(_ source: PreparedVideo, inputs: MediaEncodingInputs, params: JSONObject,
                                     media: NativeMedia, cancel: CancellationFlag,
                                     progress: @escaping (String, Double?) -> Void) throws -> JSONObject {
        try cancel.check()
        let asset = source.asset
        let videoTracks = inputs.videoTracks
        guard let track = videoTracks.first else {
            throw WorkflowError(message: "The Photos rendition has no readable video track.", code: "unsupported")
        }
        let characteristics = inputs.characteristics
        let nominalRate = inputs.nominalRate
        let naturalSize = inputs.naturalSize
        let preferredTransform = inputs.preferredTransform
        guard let description = inputs.descriptions[track.trackID]?.first else {
            throw WorkflowError(message: "The Photos rendition has no readable video track.", code: "unsupported")
        }
        let extensions = (CMFormatDescriptionGetExtensions(description) as NSDictionary?) ?? [:]
        let text = extensions.description.lowercased()
        if source.source.mediaSubtypes.contains(.spatialMedia) || characteristics.contains(.containsAlphaChannel)
            || text.contains("applelog") || text.contains("stereoview") || text.contains("projectionkind") {
            throw WorkflowError(message: "Alpha, Apple Log, spatial and immersive video need specialized preservation and are not converted.", code: "unsupported")
        }
        let transfer = extensions[kCMFormatDescriptionExtension_TransferFunction] as? String ?? ""
        let hlg = transfer == (kCVImageBufferTransferFunction_ITU_R_2100_HLG as String)
        let pq = transfer == (kCVImageBufferTransferFunction_SMPTE_ST_2084_PQ as String)
        let hdr = hlg || pq
        if characteristics.contains(.containsHDRVideo) && !hdr {
            throw WorkflowError(message: "This HDR transfer function is not supported safely. Original kept.", code: "unsupported")
        }
        if let aspect = extensions[kCMFormatDescriptionExtension_PixelAspectRatio] as? NSDictionary,
           let horizontal = aspect[kCMFormatDescriptionKey_PixelAspectRatioHorizontalSpacing] as? NSNumber,
           let vertical = aspect[kCMFormatDescriptionKey_PixelAspectRatioVerticalSpacing] as? NSNumber,
           horizontal != vertical {
            throw WorkflowError(message: "Anamorphic video needs a specialized pixel-aspect conversion. Original kept.", code: "unsupported")
        }
        let dolby = text.contains("dolby") || text.contains("dovi") || text.contains("dvv")
        if dolby && !hlg {
            throw WorkflowError(message: "This Dolby Vision profile has no verified HLG base layer. Original kept.", code: "unsupported")
        }
        let profile = object(params["profile"])
        let codec = string(profile["codec"]) == "H.264" ? AVVideoCodecType.h264 : .hevc
        if hdr && codec != .hevc { throw WorkflowError(message: "HDR preservation requires HEVC. Change the codec in Settings.", code: "unsupported") }
        let preset = string(profile["preset"])
        let reference: Double = ["Gentle": 7_500_000.0, "Standard": 4_500_000.0, "High": 3_000_000.0, "Maximum": 1_500_000.0][preset] ?? 4_500_000
        let compositionFPS = source.composition.map { 1 / $0.frameDuration.seconds } ?? 0
        let nominal = compositionFPS.isFinite && compositionFPS > 0 ? compositionFPS : nominalRate > 0 ? Double(nominalRate) : 30
        let fps = min(nominal, preset == "Maximum" ? 30 : preset == "Gentle" ? nominal : 60)
        let displayed = source.composition?.renderSize ?? naturalSize.applying(preferredTransform)
        let sourceWidth = abs(displayed.width), sourceHeight = abs(displayed.height)
        guard sourceWidth > 0, sourceHeight > 0, source.range.duration.seconds > 0 else {
            throw WorkflowError(message: "Invalid rendered dimensions or duration.", code: "unsupported")
        }
        let cap = number(profile["resolution_override"]) ?? (preset == "Maximum" ? 720 : 1080)
        let keepResolution = profile["keep_original_resolution"] as? Bool == true
        let scale = keepResolution ? 1 : min(1, cap/min(sourceWidth, sourceHeight), cap*16/9/max(sourceWidth, sourceHeight))
        let width = max(2, Int(sourceWidth*scale)/2*2), height = max(2, Int(sourceHeight*scale)/2*2)
        let ratio = min(4, max(0.2, Double(width*height)*fps/(1920*1080*30)))
        let bitrate = Int(reference*ratio)
        let estimated = Int64(source.range.duration.seconds * (Double(bitrate)+192000)/8*1.02)
        try media.ensureSpace(estimated*2)
        let outputURL = try media.safeFile(string(params["output"]))
        guard !FileManager.default.fileExists(atPath: outputURL.path) else {
            throw WorkflowError(message: "A working output already exists. Reconcile it before overwriting anything.")
        }
        let reader = try AVAssetReader(asset: asset)
        reader.timeRange = source.range
        let writer = try AVAssetWriter(outputURL: outputURL, fileType: string(profile["container"]) == "MOV" ? .mov : .mp4)
        var completed = false
        defer { if !completed { reader.cancelReading(); writer.cancelWriting() } }
        writer.metadata = inputs.metadata
        if let date = source.source.creationDate {
            let item = AVMutableMetadataItem(); item.identifier = .commonIdentifierCreationDate
            item.value = ISO8601DateFormatter().string(from: date) as NSString
            writer.metadata = writer.metadata.filter { $0.identifier != .commonIdentifierCreationDate } + [item]
        }
        let compression: JSONObject = [
            AVVideoAverageBitRateKey: bitrate,
            AVVideoProfileLevelKey: codec == .h264 ? kVTProfileLevel_H264_High_AutoLevel : hdr ? kVTProfileLevel_HEVC_Main10_AutoLevel : kVTProfileLevel_HEVC_Main_AutoLevel,
            AVVideoExpectedSourceFrameRateKey: fps,
            kVTCompressionPropertyKey_HDRMetadataInsertionMode as String: hlg ? kVTHDRMetadataInsertionMode_None : kVTHDRMetadataInsertionMode_Auto,
            kVTCompressionPropertyKey_PreserveDynamicHDRMetadata as String: false,
        ]
        var outputSettings: JSONObject = [AVVideoCodecKey: codec, AVVideoWidthKey: width, AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: compression,
            AVVideoEncoderSpecificationKey: [kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder as String: true]]
        if hdr {
            outputSettings[AVVideoColorPropertiesKey] = [
                AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_2020,
                AVVideoTransferFunctionKey: hlg ? AVVideoTransferFunction_ITU_R_2100_HLG : AVVideoTransferFunction_SMPTE_ST_2084_PQ,
                AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_2020]
            var props = object(outputSettings[AVVideoCompressionPropertiesKey])
            if let value = extensions[kCMFormatDescriptionExtension_MasteringDisplayColorVolume] { props[kVTCompressionPropertyKey_MasteringDisplayColorVolume as String] = value }
            if let value = extensions[kCMFormatDescriptionExtension_ContentLightLevelInfo] { props[kVTCompressionPropertyKey_ContentLightLevelInfo as String] = value }
            outputSettings[AVVideoCompressionPropertiesKey] = props
        }
        guard writer.canApply(outputSettings: outputSettings, forMediaType: .video) else {
            throw WorkflowError(message: "This Mac cannot hardware-encode the requested HEVC/HDR format. No SDR downgrade was made.", code: "unsupported")
        }
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: outputSettings)
        input.expectsMediaDataInRealTime = false
        guard writer.canAdd(input) else { throw WorkflowError(message: "Cannot create the video encoder.", code: "unsupported") }
        writer.add(input)
        let pixelFormat = hdr ? kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange : kCVPixelFormatType_32BGRA
        let adapter = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: pixelFormat,
            kCVPixelBufferWidthKey as String: width, kCVPixelBufferHeightKey as String: height,
            kCVPixelBufferIOSurfacePropertiesKey as String: [:]])
        let videoOutput = AVAssetReaderVideoCompositionOutput(videoTracks: videoTracks, videoSettings: [kCVPixelBufferPixelFormatTypeKey as String: pixelFormat])
        var configuration = inputs.configuration
        // Apply Photos' crop/transforms at its own render size first. The final
        // Core Image scale below cannot accidentally crop an existing edit.
        configuration.frameDuration = CMTime(seconds: 1/max(fps, 1), preferredTimescale: 60000)
        if hdr {
            configuration.colorPrimaries = AVVideoColorPrimaries_ITU_R_2020
            configuration.colorTransferFunction = hlg ? AVVideoTransferFunction_ITU_R_2100_HLG : AVVideoTransferFunction_SMPTE_ST_2084_PQ
            configuration.colorYCbCrMatrix = AVVideoYCbCrMatrix_ITU_R_2020
        }
        videoOutput.videoComposition = AVVideoComposition(configuration: configuration)
        videoOutput.alwaysCopiesSampleData = false
        guard reader.canAdd(videoOutput) else { throw WorkflowError(message: "Photos' current edit cannot be rendered safely.", code: "unsupported") }
        reader.add(videoOutput)
        var warnings: [String] = []
        if dolby { warnings.append("HDR is kept as HLG; Dolby Vision dynamic metadata is not retained.") }
        if source.source.mediaSubtypes.contains(.videoCinematic) { warnings.append("The current Cinematic appearance is rendered; depth and focus editability are not retained.") }
        var streams: [(AVAssetReaderOutput, AVAssetWriterInput)] = []
        let audioTracks = inputs.audioTracks
        let trackGroups = inputs.trackGroups
        let audioDescriptions = inputs.descriptions
        let enabledAudioIDs = inputs.enabledAudioIDs
        let spatial = audioTracks.contains { audio in
            (audioDescriptions[audio.trackID] ?? []).contains { CMFormatDescriptionGetMediaSubType($0) == kAudioFormatAPAC }
        }
        let passthrough = spatial && source.audioMix == nil && asset is AVURLAsset && source.range.start == .zero && abs(source.range.duration.seconds-inputs.duration.seconds) < 0.01
        var audioCount = 0
        var mappings: [CMPersistentTrackID: AVAssetWriterInput] = [:]
        if passthrough {
            for track in audioTracks {
                let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
                guard let hint = audioDescriptions[track.trackID]?.first else {
                    throw WorkflowError(message: "An audio track has no readable format description.", code: "unsupported")
                }
                let audioInput = AVAssetWriterInput(mediaType: .audio, outputSettings: nil, sourceFormatHint: hint)
                guard reader.canAdd(output), writer.canAdd(audioInput) else {
                    throw WorkflowError(message: "This container cannot preserve the spatial audio. Choose MOV or use another video.", code: "unsupported")
                }
                audioInput.marksOutputTrackAsEnabled = enabledAudioIDs.contains(track.trackID)
                reader.add(output); writer.add(audioInput)
                streams.append((output, audioInput)); mappings[track.trackID] = audioInput
            }
            for group in trackGroups {
                let ids = group.trackIDs.map { $0.int32Value }
                let members = ids.compactMap { mappings[$0] }
                if members.count > 1 {
                    let enabled = audioTracks.first { ids.contains($0.trackID) && enabledAudioIDs.contains($0.trackID) }
                    let defaultInput = enabled.flatMap { mappings[$0.trackID] } ?? members.first
                    let inputGroup = AVAssetWriterInputGroup(inputs: members, defaultInput: defaultInput)
                    if writer.canAdd(inputGroup) { writer.add(inputGroup) }
                    else { throw WorkflowError(message: "Audio alternate-group preservation is unavailable.", code: "unsupported") }
                }
            }
            for track in audioTracks {
                guard let origin = mappings[track.trackID] else { continue }
                for fallback in inputs.audioFallbacks[track.trackID] ?? [] {
                    if let target = mappings[fallback.trackID], origin.canAddTrackAssociation(withTrackOf: target, type: AVAssetTrack.AssociationType.audioFallback.rawValue) {
                        origin.addTrackAssociation(withTrackOf: target, type: AVAssetTrack.AssociationType.audioFallback.rawValue)
                    }
                }
            }
            audioCount = mappings.count
        } else if !audioTracks.isEmpty {
            let included = Set(source.audioMix?.inputParameters.map { $0.trackID } ?? [])
            var selected = audioTracks.filter { included.isEmpty ? enabledAudioIDs.contains($0.trackID) : included.contains($0.trackID) }
            if selected.isEmpty { selected = [audioTracks[0]] }
            // Alternate tracks are alternative renderings of the same sound.
            // They must never be mixed together (e.g. Spatial Audio plus AAC).
            for group in trackGroups {
                let ids = Set(group.trackIDs.map { $0.int32Value })
                let alternatives = selected.filter { ids.contains($0.trackID) }
                if alternatives.count > 1 {
                    let keep = alternatives.first { enabledAudioIDs.contains($0.trackID) } ?? alternatives[0]
                    selected.removeAll { ids.contains($0.trackID) && $0.trackID != keep.trackID }
                }
            }
            let output = AVAssetReaderAudioMixOutput(audioTracks: selected, audioSettings: [
                AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMIsFloatKey: true,
                AVLinearPCMBitDepthKey: 32, AVLinearPCMIsNonInterleaved: false,
                AVSampleRateKey: 48000, AVNumberOfChannelsKey: 2])
            output.audioMix = source.audioMix
            output.audioTimePitchAlgorithm = source.session.audioTimePitchAlgorithm
            let audioInput = AVAssetWriterInput(mediaType: .audio, outputSettings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 48000,
                AVNumberOfChannelsKey: 2, AVEncoderBitRateKey: 192000])
            guard reader.canAdd(output), writer.canAdd(audioInput) else { throw WorkflowError(message: "The current Photos audio mix cannot be rendered.", code: "unsupported") }
            reader.add(output); writer.add(audioInput); streams.append((output, audioInput)); audioCount = 1
            if spatial { warnings.append("Edited spatial audio is rendered to stereo AAC; spatial editability is not retained.") }
        }
        for extra in inputs.ancillaryTracks {
            let formats = inputs.descriptions[extra.trackID] ?? []
            if source.audioMix == nil, source.composition == nil, asset is AVURLAsset,
               let hint = formats.first {
                let output = AVAssetReaderTrackOutput(track: extra, outputSettings: nil)
                let extraInput = AVAssetWriterInput(mediaType: extra.mediaType, outputSettings: nil, sourceFormatHint: hint)
                if reader.canAdd(output), writer.canAdd(extraInput) {
                    reader.add(output); writer.add(extraInput); streams.append((output, extraInput)); continue
                }
            }
            warnings.append("An ancillary \(extra.mediaType.rawValue) track could not be retained in this rendition/container.")
        }
        guard writer.startWriting() else { throw writer.error ?? WorkflowError(message: "Hardware encoder unavailable.", code: "unsupported") }
        writer.startSession(atSourceTime: source.range.start)
        guard reader.startReading() else { writer.cancelWriting(); throw reader.error ?? WorkflowError(message: "Cannot decode the Photos rendition.") }
        cancel.add { reader.cancelReading(); writer.cancelWriting() }
        let group = DispatchGroup()
        let failureLock = NSLock()
        var failure: Error?
        var lastActivity = ProcessInfo.processInfo.systemUptime
        func active() { failureLock.lock(); lastActivity = ProcessInfo.processInfo.systemUptime; failureLock.unlock() }
        func stalled() -> Bool { failureLock.lock(); defer { failureLock.unlock() }; return ProcessInfo.processInfo.systemUptime-lastActivity > 120 }
        func record(_ error: Error) { failureLock.lock(); if failure == nil { failure = error }; failureLock.unlock(); reader.cancelReading() }
        let working = CGColorSpace(name: hdr ? CGColorSpace.extendedLinearITUR_2020 : CGColorSpace.extendedLinearSRGB)!
        let outputColor = CGColorSpace(name: hdr ? (hlg ? CGColorSpace.itur_2100_HLG : CGColorSpace.itur_2100_PQ) : CGColorSpace.sRGB)!
        let context = CIContext(options: [.workingColorSpace: working, .workingFormat: CIFormat.RGBAh])
        group.enter()
        var videoFinished = false
        var lastProgress = 0.0
        input.requestMediaDataWhenReady(on: DispatchQueue(label: "campresso.frames", qos: .userInitiated)) {
            if videoFinished { return }
            while input.isReadyForMoreMediaData {
                if cancel.isCancelled || reader.status == .cancelled {
                    videoFinished = true; input.markAsFinished(); group.leave(); return
                }
                guard let sample = videoOutput.copyNextSampleBuffer() else {
                    videoFinished = true; input.markAsFinished(); group.leave(); return
                }
                active()
                autoreleasepool {
                    guard let imageBuffer = CMSampleBufferGetImageBuffer(sample), let pool = adapter.pixelBufferPool else {
                        record(WorkflowError(message: "Video frame buffers are unavailable.")); return
                    }
                    var buffer: CVPixelBuffer?
                    guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let buffer else {
                        record(WorkflowError(message: "Cannot allocate a video frame.")); return
                    }
                    let image = CIImage(cvPixelBuffer: imageBuffer)
                    let transformed = image.transformed(by: .init(scaleX: Double(width)/image.extent.width, y: Double(height)/image.extent.height))
                    context.render(transformed, to: buffer, bounds: .init(x: 0, y: 0, width: width, height: height), colorSpace: outputColor)
                    for (formatKey, bufferKey) in [
                        (kCMFormatDescriptionExtension_AmbientViewingEnvironment, kCVImageBufferAmbientViewingEnvironmentKey),
                        (kCMFormatDescriptionExtension_MasteringDisplayColorVolume, kCVImageBufferMasteringDisplayColorVolumeKey),
                        (kCMFormatDescriptionExtension_ContentLightLevelInfo, kCVImageBufferContentLightLevelInfoKey)] {
                        if let value = CVBufferCopyAttachment(imageBuffer, bufferKey, nil) ?? extensions[formatKey] as CFTypeRef? {
                            CVBufferSetAttachment(buffer, bufferKey, value, .shouldPropagate)
                        }
                    }
                    let time = CMSampleBufferGetPresentationTimeStamp(sample)
                    if !adapter.append(buffer, withPresentationTime: time) { record(writer.error ?? WorkflowError(message: "Video encoder rejected a frame.")) }
                    let percent = min(99, max(0, (time-source.range.start).seconds/source.range.duration.seconds*100))
                    let now = ProcessInfo.processInfo.systemUptime
                    if now-lastProgress > 0.25 { lastProgress = now; progress("Converting", percent) }
                }
            }
        }
        for (index, stream) in streams.enumerated() {
            group.enter()
            var finished = false
            stream.1.requestMediaDataWhenReady(on: DispatchQueue(label: "campresso.stream.\(index)", qos: .userInitiated)) {
                if finished { return }
                while stream.1.isReadyForMoreMediaData {
                    if cancel.isCancelled || reader.status == .cancelled {
                        finished = true; stream.1.markAsFinished(); group.leave(); return
                    }
                    guard let sample = stream.0.copyNextSampleBuffer() else {
                        finished = true; stream.1.markAsFinished(); group.leave(); return
                    }
                    active()
                    if !stream.1.append(sample) { record(writer.error ?? WorkflowError(message: "An audio/metadata stream could not be written.")) }
                }
            }
        }
        // Bounded waits keep cancellation effective even if a media callback stalls.
        while group.wait(timeout: .now() + .milliseconds(200)) == .timedOut {
            if cancel.isCancelled { reader.cancelReading(); writer.cancelWriting(); throw CancellationError() }
            if stalled() { throw WorkflowError(message: "Media processing stopped making progress. The original is unchanged; retry after checking storage.") }
            if reader.status == .failed || writer.status == .failed {
                throw reader.error ?? writer.error ?? WorkflowError(message: "Media processing failed.")
            }
        }
        try cancel.check()
        if let failure { writer.cancelWriting(); throw failure }
        if reader.status == .failed { writer.cancelWriting(); throw reader.error! }
        let done = DispatchSemaphore(value: 0)
        writer.finishWriting { done.signal() }
        let finishing = ProcessInfo.processInfo.systemUptime
        while done.wait(timeout: .now() + .milliseconds(200)) == .timedOut {
            try cancel.check()
            if ProcessInfo.processInfo.systemUptime-finishing > 120 { throw WorkflowError(message: "The encoder did not finish publishing its output.") }
        }
        guard writer.status == .completed else { throw writer.error ?? WorkflowError(message: "The hardware encoder did not finish.") }
        completed = true
        return ["expected": ["width": width, "height": height, "duration": source.range.duration.seconds,
                              "hdr": hdr, "transfer": hlg ? "HLG" : pq ? "PQ" : "SDR", "audio_count": audioCount,
                              "fps": fps], "warnings": warnings]
    }
}
