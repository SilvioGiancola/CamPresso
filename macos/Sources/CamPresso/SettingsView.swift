import AppKit
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @State private var library = ""
    @State private var preset = "Standard"
    @State private var codec = "HEVC"
    @State private var container = "MP4"
    @State private var keepResolution = false
    @State private var resolution = 0
    @State private var personal = false
    @State private var accepted = false
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        Form {
            Section {
                BrandHeading()
            }
            Section("Photos library") {
                HStack {
                    TextField("Library path", text: $library)
                    Button("Choose…", action: chooseLibrary)
                }.disabled(!model.processing.isEmpty)
                Text("The saved library opens automatically. Compression requires the System Photo Library.").font(.caption).foregroundStyle(.secondary)
                Toggle("I checked Photos Settings: family Shared Photo Library is not configured for this library.", isOn: $personal)
                Text("Personal iCloud Photos is supported. Shared or uncertain libraries remain browse/play-only.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Conversion settings") {
                Picker("Preset", selection: $preset) {
                    Text("Gentle · best quality").tag("Gentle")
                    Text("Standard · similar look").tag("Standard")
                    Text("High · slightly softer").tag("High")
                    Text("Maximum · noticeably softer").tag("Maximum")
                }
                Toggle("Keep original resolution", isOn: $keepResolution)
                Text("Standard defaults to up to 1080p / 60 fps. Maximum uses up to 720p / 30 fps. Smaller videos are never upscaled; portrait orientation is preserved.").font(.caption).foregroundStyle(.secondary)
                DisclosureGroup("Advanced") {
                    Picker("Codec", selection: $codec) { Text("HEVC · supports HDR").tag("HEVC"); Text("H.264 · SDR only").tag("H.264") }
                    Picker("File format", selection: $container) { Text("MP4").tag("MP4"); Text("MOV").tag("MOV") }
                    Picker("Resolution override", selection: $resolution) {
                        Text("Use preset").tag(0)
                        ForEach([360, 480, 720, 1080, 1440, 2160], id: \.self) { Text("\($0)p").tag($0) }
                    }.disabled(keepResolution)
                    Text("HDR stays 10-bit HLG/HDR10 when supported. Dolby Vision dynamic metadata, Cinematic editability and some ancillary tracks may not transfer.").font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("Compress → Review → Decide") {
                Toggle("I understand compression adds a smaller copy to Photos before review.", isOn: $accepted)
                Text("Your original stays until Keep Compressed. Revert removes only the generated copy. Dates, location, favorites, hidden status and writable regular albums are copied. Faces, edit history and some Photos metadata cannot transfer. Finish review before closing: every launch starts fresh, while both Photos copies stay as they are.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Storage") {
                Button("Clear conversion history…") { model.clearHistory() }
                    .disabled(!model.connected || !model.processing.isEmpty || model.scanning || model.cleaning || model.closing)
                Text("Clear finished history and safe local temporary files. Photos videos, pending reviews and recovery journals are preserved.").font(.caption).foregroundStyle(.secondary)
                Text("Each launch starts with an empty queue, Review and Completed views. Your library and settings are remembered. Earlier jobs never resume; protected archive files may remain listed below.").font(.caption).foregroundStyle(.secondary)
                if !model.retainedFiles.isEmpty {
                    DisclosureGroup("Retained files (\(model.retainedFiles.count))") {
                        ForEach(model.retainedFiles, id: \.previousWorkIdentity) { item in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(string(item["path"])).font(.caption).textSelection(.enabled)
                                Text("\(humanSize(number(item["bytes"]))) · \(string(item["reason"]))").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            if let error { Text(error).foregroundStyle(.red) }
            HStack {
                Button("Cancel") { NSApp.keyWindow?.close() }
                Spacer()
                if saving { ProgressView().controlSize(.small) }
                Button("Save", action: save).buttonStyle(.borderedProminent).foregroundStyle(Brand.background).disabled(saving || !model.connected)
            }
        }
        .formStyle(.grouped).padding(8).frame(width: 640, height: 690)
        .scrollContentBackground(.hidden)
        .onAppear(perform: load)
        .onChange(of: library) { old, new in if old != new && new != model.library { personal = false } }
    }
    private func load() {
        library = model.library; preset = string(model.preferences["preset"])
        if preset.isEmpty { preset = "Standard" }
        codec = string(model.preferences["codec"]); if codec.isEmpty { codec = "HEVC" }
        container = string(model.preferences["container"]); if container.isEmpty { container = "MP4" }
        keepResolution = model.preferences["keep_original_resolution"] as? Bool == true
        resolution = Int(number(model.preferences["resolution_override"]) ?? 0)
        personal = string(model.preferences["personal_library_confirmed"]) == library && !library.isEmpty
        accepted = model.preferences["import_notice_accepted"] as? Bool == true
    }
    private func chooseLibrary() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.treatsFilePackagesAsDirectories = true; panel.allowsMultipleSelection = false
        panel.message = "Choose your System Photo Library (.photoslibrary)."
        if !library.isEmpty { panel.directoryURL = URL(fileURLWithPath: library).deletingLastPathComponent() }
        if panel.runModal() == .OK, let path = panel.url { library = path.path }
    }
    private func save() {
        saving = true; error = nil
        let window = NSApp.keyWindow
        let values: JSONObject = ["library": library, "preset": preset, "codec": codec, "container": container,
            "keep_original_resolution": keepResolution, "resolution_override": resolution == 0 ? NSNull() : resolution,
            "personal_library_confirmed": personal, "import_notice_accepted": accepted]
        Task {
            do { _ = try await model.request("preferences.update", ["values": values]); window?.close() }
            catch { self.error = error.localizedDescription }
            saving = false
        }
    }
}

private extension Dictionary where Key == String, Value == Any {
    var previousWorkIdentity: String { string(self["id"]) }
}
