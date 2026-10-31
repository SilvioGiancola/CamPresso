import AppKit
import Photos
import SwiftUI

struct LibraryView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.openSettings) private var openSettings
    @State private var details: VideoRow?
    @State private var inspector = false

    var body: some View {
        VStack(spacing: 0) {
            header
            if model.library.isEmpty { BrandHeading().frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 16).padding(.bottom, 10) }
            if let fatal = model.fatalError { banner(fatal, icon: "exclamationmark.octagon", color: .red) }
            if let error = model.catalogError { banner(error, icon: "externaldrive.badge.exclamationmark", color: .orange) }
            if !model.photoAuthorized {
                HStack {
                    Label("Allow Full Photos Access to prepare, compare and save videos.", systemImage: "photo.badge.checkmark")
                    Spacer(); Button("Allow Photos Access") { model.allowPhotos() }.disabled(model.authorizing)
                }.padding(12).background(Brand.cyan.opacity(0.08))
            } else if !model.setupComplete {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("Choose your personal System Photo Library and conversion settings.", systemImage: "gearshape")
                    }
                    Spacer(); Button("Open Settings…") { openSettings() }
                }.padding(12).background(Brand.cyan.opacity(0.08))
            }
            if model.rows.contains(where: { $0.state == "Needs attention" }) {
                HStack {
                    Label("Some Photos operations need inspection. Their recovery files have been kept.", systemImage: "exclamationmark.shield")
                    Spacer(); Button("Show") { model.section = .review }
                }.padding(10).background(.orange.opacity(0.1))
            }
            HStack(spacing: 0) {
                gallery
                if inspector {
                    Divider()
                    summary.frame(width: 245).padding(16).background(Brand.surface)
                }
            }
            Divider()
            footer
        }
        .frame(minWidth: 900, minHeight: 640)
        .toolbar {
            ToolbarItemGroup {
                Button { Task { await model.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }.disabled(model.scanning || model.closing)
                Button { inspector.toggle() } label: { Label("Summary", systemImage: "sidebar.right") }
                Button { openSettings() } label: { Label("Settings", systemImage: "gearshape") }
            }
        }
        .searchable(text: $model.search, placement: .toolbar, prompt: "Search videos")
        .sheet(item: $details) { row in DetailView(row: row).environmentObject(model).campressoTheme() }
        .alert("CamPresso", isPresented: Binding(get: { model.notice != nil }, set: { if !$0 { model.notice = nil } })) { Button("OK") { model.notice = nil } } message: { Text(model.notice ?? "") }
        .overlay { if model.closing { ZStack { Color.black.opacity(0.15); ProgressView("Stopping work and finishing Photos changes…").padding(25).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14)) } } }
        .onReceive(NotificationCenter.default.publisher(for: .init("ToggleSummary"))) { _ in inspector.toggle() }
    }
    private var header: some View {
        VStack(spacing: 10) {
            HStack {
                Text("Videos").font(.title2.bold())
                Text(model.library.isEmpty ? "Choose a Photos library" : URL(fileURLWithPath: model.library).lastPathComponent).foregroundStyle(.secondary).lineLimit(1)
                if model.scanning { ProgressView().controlSize(.small); Text("Reading library…").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Button(model.selection.isEmpty ? "Select visible" : "Clear selection") {
                    model.selection = model.selection.isEmpty ? Set(model.filtered.map(\.id)) : []
                }
                Picker("Sort", selection: $model.sort) {
                    ForEach(["Most savings", "Largest original", "Newest", "Filename"], id: \.self) { Text($0) }
                }.frame(width: 180)
            }
            HStack {
                Picker("View", selection: $model.section) {
                    ForEach(LibrarySection.allCases) { section in
                        Text(section.rawValue + count(section)).tag(section)
                    }
                }.pickerStyle(.segmented).frame(maxWidth: 610)
                Spacer()
                Toggle(isOn: $model.favoritesOnly) { Image(systemName: "heart.fill") }.toggleStyle(.button).help("Favorites only")
                Toggle("Local only", isOn: $model.localOnly).toggleStyle(.button)
            }
        }.padding(.horizontal, 16).padding(.vertical, 12).background(Brand.surface)
    }
    private func count(_ section: LibrarySection) -> String {
        switch section {
        case .library: return " · \(model.items.count)"
        case .processing: return " · \(model.processing.count)"
        case .review: return " · \(model.reviews.count)"
        case .completed: return " · \(model.rows.filter { $0.state == "Completed" }.count)"
        }
    }
    private var gallery: some View {
        ScrollView {
            if model.filtered.isEmpty {
                ContentUnavailableView(model.scanning ? "Reading your library" : "No videos here", systemImage: model.scanning ? "photo.stack" : "video", description: Text(model.scanning ? "Large libraries may take a little while." : "Refresh the library or change the current filters."))
                    .frame(maxWidth: .infinity, minHeight: 350)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 205, maximum: 290), spacing: 14)], spacing: 14) {
                    ForEach(model.filteredItems) { item in
                        let row = item.row
                        VideoCard(item: item, selected: model.selection.contains(row.id), thumbnailRevision: model.thumbnailRevision)
                            .onTapGesture { if model.selection.contains(row.id) { model.selection.remove(row.id) } else { model.selection.insert(row.id) } }
                            .onTapGesture(count: 2) { if row.state == "Ready to review" { model.review(row) } else { model.play(row) } }
                            .contextMenu {
                                Button(row.state == "Ready to review" ? "Compare versions" : "Play") { if row.state == "Ready to review" { model.review(row) } else { model.play(row) } }
                                Button("Show in Photos") { model.showPhotos(row) }
                                if !row.resultID.isEmpty && row.state != "Completed" {
                                    Button("Play compressed") { model.play(row, compressed: true) }
                                    Button("Show compressed in Photos") { model.action("photos.show", ["id": row.resultID]) }
                                }
                                if row.canCompress { Button("Compress") { model.action("jobs.enqueue", ["ids": [row.id]]) }.disabled(!model.setupComplete) }
                                if row.state == "Needs attention" { Button("Inspect Photos status") { model.action("recovery.inspect", ["job_id": row.jobID]) } }
                                if row.state == "Awaiting import" { Button("Add existing result for review…") { prepareLegacy(row) } }
                                Button("Details…") { details = row }
                            }
                    }
                }.padding(16)
            }
        }.background(Brand.background)
    }
    private var summary: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Library size reduction").font(.headline)
            let values = model.compressionTargets
            let reduction = values.compactMap(\.reduction).map { max(0, $0) }.reduce(0, +)
            Text(humanSize(reduction)).font(.system(size: 30, weight: .semibold, design: .rounded))
            Text("Estimated for \(values.count) eligible video(s). Actual results depend on the source.").font(.caption).foregroundStyle(.secondary)
            Divider()
            LabeledContent("Ready to review", value: "\(model.reviews.count)")
            LabeledContent("Processing", value: "\(model.processing.count)")
            let accepted = model.rows.filter { $0.state == "Completed" && string($0.raw["decision"]) == "keep" }.compactMap(\.reduction).reduce(0, +)
            LabeledContent("Accepted reduction", value: humanSize(accepted))
            Text("Originals in Recently Deleted may still consume space. Photos manages iCloud synchronization and its own local cache.").font(.caption).foregroundStyle(.secondary)
            Spacer()
        }
    }
    private var footer: some View {
        VStack(spacing: 8) {
            if model.batchTotal > 0 {
                HStack {
                    ProgressView(value: Double(model.batchProcessed), total: Double(max(1, model.batchTotal))).frame(maxWidth: 200)
                    Text("\(model.batchProcessed) of \(model.batchTotal) processed").font(.caption).monospacedDigit()
                    if let item = model.processingItems.first { ProcessingDetail(item: item) }
                    Spacer()
                    Button(model.paused ? "Resume" : "Pause") { model.action(model.paused ? "jobs.resume" : "jobs.pause") }
                    Button("Stop") { model.action("jobs.stop") }.disabled(model.processing.isEmpty)
                }
            }
            HStack {
                Text(model.selection.isEmpty ? "\(model.filtered.count) videos" : "\(model.selection.count) selected").font(.callout).foregroundStyle(.secondary)
                if model.rows.contains(where: { ["Waiting for connection", "Waiting for space", "Paused"].contains($0.state) }) {
                    Button("Retry / Resume") { model.action("jobs.resume") }
                }
                Spacer()
                if model.chosen.count == 1, let row = model.chosen.first {
                    Button(row.state == "Ready to review" ? "Compare" : "Play") { if row.state == "Ready to review" { model.review(row) } else { model.play(row) } }
                    Button("Show in Photos") { model.showPhotos(row) }
                    if row.state == "Needs attention" { Button("Inspect") { model.action("recovery.inspect", ["job_id": row.jobID]) } }
                    if row.state == "Awaiting import" { Button("Prepare Review…") { prepareLegacy(row) } }
                }
                if !model.chosen.isEmpty && model.chosen.allSatisfy({ $0.state == "Ready to review" }) {
                    Button("Revert Selected…") { model.decideSelected("revert") }
                    Button("Keep Compressed…") { model.decideSelected("keep") }.buttonStyle(.borderedProminent).foregroundStyle(Brand.background)
                } else {
                    Button("Compress \(model.compressionTargets.count) video(s)") { model.compress() }.buttonStyle(.borderedProminent).foregroundStyle(Brand.background)
                        .disabled(!model.setupComplete || !model.connected || model.compressionTargets.isEmpty || model.closing)
                }
            }
        }.padding(12).background(Brand.surface)
    }
    private func banner(_ text: String, icon: String, color: Color) -> some View {
        HStack { Label(text, systemImage: icon).font(.callout); Spacer(); Button("Settings…") { openSettings() } }.padding(12).background(color.opacity(0.1))
    }
    private func prepareLegacy(_ row: VideoRow) {
        let alert = NSAlert(); alert.messageText = "Add the existing compressed copy to Photos?"
        alert.informativeText = "This imports the saved result for comparison without converting again. Your original stays until you choose Keep Compressed."
        alert.addButton(withTitle: "Add for Review"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { model.action("legacy.import", ["job_id": row.jobID]) }
    }
}

struct VideoCard: View {
    @ObservedObject var item: VideoItem
    let selected: Bool
    let thumbnailRevision: Int
    private var row: VideoRow { item.row }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ZStack(alignment: .topLeading) {
                VideoThumbnail(identifier: row.thumbnailID, revision: thumbnailRevision,
                               downloaded: row.raw["preview_ready"] as? Bool == true)
                HStack(spacing: 7) {
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle").foregroundStyle(selected ? Brand.accent : .white)
                    if row.favorite { Image(systemName: "heart.fill").foregroundStyle(.pink) }
                    if row.shared { Image(systemName: "person.2.fill") }
                    Spacer()
                    Image(systemName: row.local ? "checkmark.circle" : "icloud").help(row.local ? "Local at the last library refresh" : "Cloud at the last library refresh; retrieved automatically when needed")
                }.font(.caption.bold()).padding(9).background(.black.opacity(0.35)).foregroundStyle(.white)
                VStack { Spacer(); HStack { Spacer(); Text(durationText(number(row.raw["duration"]))).font(.caption.monospacedDigit()).padding(5).background(.black.opacity(0.6), in: RoundedRectangle(cornerRadius: 5)).foregroundStyle(.white) }.padding(7) }
            }.frame(height: 135).clipShape(RoundedRectangle(cornerRadius: 9))
            Text(row.name).font(.callout.weight(.medium)).lineLimit(1).help(row.name)
            HStack {
                Text(row.shared ? "Shared · Browse only" : row.state).font(.caption.bold()).foregroundStyle(row.shared ? .secondary : row.color)
                Spacer()
                if row.processing && row.progress == nil { ProgressView().controlSize(.mini) }
                else if let percent = row.progress, row.processing { Text("\(Int(percent))%").font(.caption.monospacedDigit()).foregroundStyle(row.color) }
            }
            if row.processing, let percent = row.progress { ProgressView(value: percent, total: 100).tint(row.color) }
            Text(row.savingText).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            if row.state == "Needs attention" || row.state.hasPrefix("Waiting") || row.state == "Error" || row.state == "Skipped" {
                Text(row.detail).font(.caption2).foregroundStyle(.secondary).lineLimit(2).help(row.detail)
            }
        }
        .padding(9).background(selected ? Brand.accent.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 12))
        .background(Brand.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(selected ? Brand.accent : Color.secondary.opacity(0.15), lineWidth: selected ? 2 : 1))
        .accessibilityElement(children: .combine).accessibilityLabel("\(row.name), \(row.state), \(row.local ? "local" : "stored in iCloud")")
    }
}

private struct ProcessingDetail: View {
    @ObservedObject var item: VideoItem
    var body: some View {
        Text(item.row.detail.isEmpty ? item.row.state : item.row.detail)
            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
    }
}

struct DetailView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let row: VideoRow
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(row.name).font(.headline)
            LabeledContent("Status", value: row.state)
            LabeledContent("Stored original", value: humanSize(row.sourceSize))
            LabeledContent("Compressed", value: humanSize(row.resultSize))
            LabeledContent("Photos original", value: row.originalID)
            LabeledContent("Photos compressed", value: row.resultID.isEmpty ? "Not added" : row.resultID)
            LabeledContent("Recovery files retained", value: humanSize(number(row.raw["recovery_bytes"]) ?? 0))
            DisclosureGroup("Identifiers and journals") {
                LabeledContent("Generation", value: string(row.raw["generation"]))
                ForEach(row.raw["operations"] as? [JSONObject] ?? [], id: \.operationIdentity) { operation in
                    let identifier = string(operation["id"])
                    Text("\(string(operation["action"])) · \(identifier)").font(.caption)
                    Text(model.data.appendingPathComponent("NativeJournals/\(identifier).json").path).font(.caption2)
                }
                if !string(row.raw["legacy_journal"]).isEmpty { Text(string(row.raw["legacy_journal"])).font(.caption2) }
                ForEach(row.raw["generated_ids"] as? [String] ?? [], id: \.self) { identifier in
                    Text("Recorded generated asset: \(identifier)").font(.caption2)
                }
            }
            Text(row.detail).textSelection(.enabled)
            ForEach(row.raw["warnings"] as? [String] ?? [], id: \.self) { Text($0).font(.callout).foregroundStyle(.orange) }
            Text("Saved in Photos confirms local library presence, not completed iCloud upload. Removed items normally remain in Recently Deleted for 30 days.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Open logs / recovery folder") { NSWorkspace.shared.open(model.data) }
                Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }.padding(20).frame(width: 600).textSelection(.enabled)
    }
}

private extension Dictionary where Key == String, Value == Any {
    var operationIdentity: String { string(self["id"]) }
}
