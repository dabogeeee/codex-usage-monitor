import AppKit
import SwiftUI
import Foundation
import ApplicationServices

struct Totals: Decodable {
    var today: Int64?; var week: Int64?; var month: Int64?; var all: Int64?
}
struct UsageWindow: Decodable {
    var label: String; var durationMinutes: Int?; var remainingPercent: Double?
    var resetsAt: Double?; var visibleByDefault: Bool
}
struct Credits: Decodable { var hasCredits: Bool?; var unlimited: Bool?; var balance: String? }
struct Bucket: Decodable {
    var id: String; var name: String; var windows: [UsageWindow]; var credits: Credits?
}
struct OfficialUsage: Decodable {
    var totals: Totals; var latestDate: String?; var scope: String
}
struct Account: Decodable {
    var status: String; var planType: String?; var buckets: [Bucket]; var usage: OfficialUsage?
    var updatedAt: String?; var error: String?; var usageError: String?
}
struct Chat: Decodable, Identifiable {
    var id: String; var title: String; var project: String; var model: String?; var updatedAt: String
    var cacheHitPercent: Double?; var sessionCacheHitPercent: Double?
    var inputTokens: Int64?; var cachedInputTokens: Int64?
    var contextTokens: Int64?; var contextWindow: Int64?; var contextUsedPercent: Double?
}
struct Local: Decodable {
    var totals: Totals; var threads: [Chat]; var selectedThread: Chat?
    var fileCount: Int; var timezone: String; var periodDate: String; var readErrors: Int; var skippedLines: Int
}
struct Snapshot: Decodable {
    var generatedAt: String; var local: Local; var account: Account; var requestId: Int?
}

func compactNumber(_ value: Int64?) -> String {
    guard let value else { return "—" }
    if value >= 1_000_000_000 { return String(format: "%.2fB", Double(value) / 1_000_000_000) }
    if value >= 1_000_000 { return String(format: "%.2fM", Double(value) / 1_000_000) }
    if value >= 1_000 { return String(format: "%.1fK", Double(value) / 1_000) }
    return String(value)
}
func fullNumber(_ value: Int64?) -> String {
    guard let value else { return "—" }
    return value.formatted(.number.locale(Locale(identifier: "zh_CN")))
}
func percentage(_ value: Double?) -> String {
    guard let value else { return "—" }
    return String(format: value.truncatingRemainder(dividingBy: 1) == 0 ? "%.0f%%" : "%.1f%%", value)
}
func displayTime(_ value: String?) -> String {
    guard let value else { return "—" }
    let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = format.date(from: value) ?? ISO8601DateFormatter().date(from: value) else { return "—" }
    return displayDate(date)
}
func displayDate(_ date: Date) -> String {
    let format = DateFormatter(); format.locale = Locale(identifier: "zh_CN")
    format.timeZone = TimeZone(identifier: "Asia/Shanghai"); format.dateFormat = "M/d HH:mm"
    return format.string(from: date)
}

@MainActor final class UsageModel: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var error: String?
    @Published var selected = "__desktop__"
    @Published var desktopSelection = DesktopSelection(status: "permission_required", threadId: nil, hostKind: nil)
    @Published var desktopAccessibilityEnabled = UserDefaults.standard.bool(forKey: "desktopAccessibilityEnabled")
    @Published var official = false
    @Published var compact = UserDefaults.standard.object(forKey: "compact") as? Bool ?? true
    @Published var pinned = UserDefaults.standard.object(forKey: "pinned") as? Bool ?? true
    @Published var theme = UserDefaults.standard.string(forKey: "theme") ?? "system"
    var onUpdate: (() -> Void)?
    private var process: Process?
    private var input: FileHandle?
    private var generation = 0
    private var epoch = UUID()
    private var desktopTimer: Timer?
    private var desktopBusy = false
    private let desktopReader = DesktopSelectionReader()
    private let desktopQueue = DispatchQueue(label: "com.codexusage.selection", qos: .utility)
    var desktopTrackingDisabled = false
    private(set) var desktopUpdatedAt = Date()
    var requestGeneration: Int { generation }

    var isRunning: Bool { process?.isRunning == true }
    func start() {
        guard !isRunning else { return }
        let root = Bundle.main.resourceURL!.appendingPathComponent("backend")
        let task = Process(); let output = Pipe(); let commands = Pipe()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        task.arguments = [root.appendingPathComponent("native_bridge.py").path]
        task.currentDirectoryURL = root; task.standardOutput = output; task.standardInput = commands
        task.standardError = FileHandle.nullDevice
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        task.environment = environment
        let token = UUID(); epoch = token
        do {
            try task.run(); process = task; input = commands.fileHandleForWriting; snapshot = nil; error = nil
            sendSelection()
            startDesktopTracking()
        } catch {
            self.error = "统计服务无法启动：请检查本机 Python 和 Codex CLI。"; onUpdate?(); return
        }
        task.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, self.epoch == token else { return }
                self.error = "统计服务已停止，可以点击刷新重新连接。"; self.onUpdate?()
            }
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            var buffer = Data()
            let handle = output.fileHandleForReading
            while true {
                let bytes = handle.availableData
                if bytes.isEmpty { break }
                buffer.append(bytes)
                while let end = buffer.firstIndex(of: 10) {
                    let line = buffer.prefix(upTo: end); buffer.removeSubrange(...end)
                    guard let result = try? JSONDecoder().decode(Snapshot.self, from: line) else { continue }
                    DispatchQueue.main.async {
                        guard let self, self.epoch == token, (result.requestId ?? 0) == self.generation else { return }
                        self.snapshot = result; self.error = nil; self.onUpdate?()
                    }
                }
            }
        }
    }
    func select(_ value: String) {
        selected = value; generation += 1; sendSelection()
        onUpdate?()
        if value == "__desktop__" { pollDesktopSelection() }
    }
    func refresh() {
        if !isRunning { start() } else { sendSelection() }
    }
    private func sendSelection() {
        let target: Any
        if selected == "__desktop__" {
            target = desktopSelection.status == "selected" ? desktopSelection.threadId ?? "__desktop_unavailable__" : "__desktop_unavailable__"
        } else { target = selected.isEmpty ? NSNull() : selected }
        let request: [String: Any] = ["thread_id": target, "request_id": generation]
        guard let data = try? JSONSerialization.data(withJSONObject: request) else { return }
        do { try input?.write(contentsOf: data + Data([10])) } catch { self.error = "统计服务连接已断开。" }
    }
    func stop() {
        epoch = UUID()
        desktopTimer?.invalidate(); desktopTimer = nil
        snapshot = nil; error = nil
        try? input?.close(); input = nil
        if let task = process, task.isRunning { task.terminate() }
        process = nil
    }
    private func startDesktopTracking() {
        desktopTimer?.invalidate()
        desktopTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            DispatchQueue.main.async { self?.pollDesktopSelection() }
        }
        pollDesktopSelection()
    }
    private func pollDesktopSelection() {
        guard isRunning, !desktopBusy, !desktopTrackingDisabled else { return }
        desktopBusy = true
        let token = epoch
        let exposeMetadata = desktopAccessibilityEnabled
        desktopQueue.async { [weak self, desktopReader] in
            let value = desktopReader.read(enableAccessibility: exposeMetadata)
            DispatchQueue.main.async {
                guard let self else { return }
                self.desktopBusy = false
                guard self.epoch == token else { return }
                let changed = self.desktopSelection != value
                self.desktopSelection = value; self.desktopUpdatedAt = Date()
                if changed, self.selected == "__desktop__" { self.generation += 1; self.sendSelection() }
                self.onUpdate?()
            }
        }
    }
    func requestDesktopPermission() {
        desktopAccessibilityEnabled = true
        UserDefaults.standard.set(true, forKey: "desktopAccessibilityEnabled")
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        let trusted = AXIsProcessTrustedWithOptions(options)
        if !trusted, let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
        pollDesktopSelection()
    }
    var selectionLabel: String {
        selected == "__desktop__" ? "桌面当前聊天" : selected.isEmpty ? "最近活动" : "手动选择"
    }
    var selectionMessage: String {
        switch desktopSelection.status {
        case "permission_required": return "需要辅助功能权限：仅读取 Codex 当前聊天链接的标识，不读取聊天正文。"
        case "selected": return desktopSelection.hostKind == "remote" ? "已读取远端聊天 UUID；本机可能没有该聊天的用量日志。" : "已跟随桌面端当前聊天。"
        case "codex_closed": return "Codex 尚未运行。"
        case "ambiguous", "ambiguous_windows": return "检测到多个候选聊天，暂不显示指标，避免选错。"
        case "scan_incomplete": return "界面元数据扫描尚未完成，可展开 Codex 侧栏后重试。"
        case "window_unavailable": return "Codex 主窗口暂时不可读取。"
        default: return desktopAccessibilityEnabled ? "没有读取到当前聊天链接。请展开 Codex 侧栏；设置页和新建聊天页可能没有聊天 UUID。" : "辅助功能已授权，请点击‘启用桌面聊天识别’以开启界面元数据。"
        }
    }
}

let accent = Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
    appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
        ? NSColor(srgbRed: 0.46, green: 0.81, blue: 0.63, alpha: 1)
        : NSColor(srgbRed: 0.13, green: 0.52, blue: 0.38, alpha: 1)
}))
struct Card<Content: View>: View {
    var compact = false
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 10 : 14) { content }
            .padding(compact ? 12 : 16).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(Color.primary.opacity(0.06)))
    }
}
struct Meter: View {
    var title: String; var hint: String; var value: Double?; var detail: String
    var warning: Bool = false
    var compact = false
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline) {
                if compact {
                    HStack(spacing: 4) {
                        Text(title).font(.system(size: 12, weight: .medium))
                        if !hint.isEmpty { Text(hint).font(.system(size: 8)).foregroundStyle(.secondary) }
                    }
                } else {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(.system(size: 12, weight: .medium))
                        if !hint.isEmpty { Text(hint).font(.system(size: 9)).foregroundStyle(.secondary) }
                    }
                }
                Spacer()
                Text(percentage(value)).font(.system(size: compact ? 22 : 25, weight: .semibold, design: .rounded))
                    .monospacedDigit().foregroundStyle(warning ? Color.orange : accent)
            }
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(accent.opacity(0.10))
                    Capsule().fill(warning ? .orange : accent).frame(width: geometry.size.width * max(0, min(value ?? 0, 100)) / 100)
                }
            }.frame(height: 5).accessibilityLabel(title).accessibilityValue(percentage(value))
            if !detail.isEmpty { Text(detail).font(.system(size: 10)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
        }
    }
}
struct Statistic: View {
    var title: String; var value: Int64?
    var compact = false
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 10)).foregroundStyle(.secondary)
            Text(compactNumber(value)).font(.system(size: compact ? 21 : 24, weight: .semibold, design: .rounded)).monospacedDigit()
            if !compact { Text("tokens").font(.system(size: 9)).foregroundStyle(.secondary) }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(compact ? 9 : 11)
            .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 9))
            .help(fullNumber(value) + " tokens")
    }
}
struct UsageView: View {
    @ObservedObject var model: UsageModel
    var bringToFront: () -> Void
    var pinChanged: () -> Void
    var themeChanged: () -> Void
    private var chat: Chat? {
        guard let snap = model.snapshot, (snap.requestId ?? 0) == model.requestGeneration else { return nil }
        return snap.local.selectedThread
    }
    private var totals: Totals? { model.official ? model.snapshot?.account.usage?.totals : model.snapshot?.local.totals }
    private var themePicker: some View {
        Picker("主题", selection: $model.theme) {
            Text("跟随系统").tag("system"); Text("浅色").tag("light"); Text("深色").tag("dark")
        }.labelsHidden().pickerStyle(.menu).frame(width: 104).help("面板主题：跟随系统、浅色或深色")
            .onChange(of: model.theme) { _, value in
                UserDefaults.standard.set(value, forKey: "theme"); themeChanged()
            }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: model.compact ? 10 : 12) {
                HStack {
                    Image(systemName: "chart.line.uptrend.xyaxis").font(.system(size: 23)).foregroundStyle(accent)
                        .frame(width: 36, height: 36).background(accent.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Codex 用量").font(.system(size: 21, weight: .semibold))
                        if !model.compact { Text("原生面板 · 随 Codex 启动").font(.system(size: 10)).foregroundStyle(.secondary) }
                    }
                    Spacer()
                    Text(model.snapshot?.account.planType?.uppercased() ?? "读取中")
                        .font(.system(size: 10, weight: .semibold)).foregroundStyle(accent)
                        .padding(6).background(accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
                }
                HStack {
                    Circle().fill(model.error == nil ? accent : Color.orange).frame(width: 5, height: 5)
                    Text(model.snapshot.map { "更新于 " + displayTime($0.generatedAt) } ?? "正在读取本机用量…")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                    Spacer()
                    themePicker.font(.system(size: 10))
                    Button(action: model.refresh) { Image(systemName: "arrow.clockwise") }.buttonStyle(.plain).help("刷新面板；官方额度每 60 秒读取")
                }
                if let error = model.error ?? model.snapshot?.account.error {
                    Text(error).font(.system(size: 11)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                }
                Card(compact: model.compact) {
                    HStack { Text("剩余额度").font(.system(size: 12, weight: .semibold)); Spacer()
                        if !model.compact { Text("官方 · 60 秒更新").font(.system(size: 9)).foregroundStyle(.secondary) }
                    }
                    if let snap = model.snapshot {
                        let visible = snap.account.buckets.flatMap { bucket in bucket.windows.filter(\.visibleByDefault) }
                        if visible.isEmpty { Text(snap.account.status == "loading" ? "正在读取官方额度…" : "当前账户没有返回额度窗口").font(.system(size: 11)).foregroundStyle(.secondary) }
                        ForEach(Array(visible.enumerated()), id: \.offset) { _, window in
                            Meter(title: window.label, hint: "", value: window.remainingPercent,
                                  detail: model.compact ? "" : quotaDetail(window, snap.account.status),
                                  warning: (window.remainingPercent ?? 100) < 20, compact: model.compact)
                        }
                        let extra = snap.account.buckets.flatMap { $0.windows.filter { !$0.visibleByDefault } }
                        if !extra.isEmpty {
                            DisclosureGroup("其他额度窗口") {
                                ForEach(Array(extra.enumerated()), id: \.offset) { _, window in
                                    Meter(title: window.label, hint: "", value: window.remainingPercent, detail: quotaDetail(window, snap.account.status))
                                }
                            }.font(.system(size: 10))
                        }
                    } else { Text("正在连接 Codex 账号…").font(.system(size: 11)).foregroundStyle(.secondary) }
                }
                Card(compact: model.compact) {
                    HStack { Text("Token 用量").font(.system(size: 12, weight: .semibold)); Spacer()
                        Picker("统计来源", selection: $model.official) { Text("本机实时").tag(false); Text("账号官方").tag(true) }
                            .labelsHidden().pickerStyle(.menu).frame(width: 105)
                    }
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                        Statistic(title: "今日", value: totals?.today, compact: model.compact); Statistic(title: "本周", value: totals?.week, compact: model.compact)
                        Statistic(title: "本月", value: totals?.month, compact: model.compact); Statistic(title: "累计", value: totals?.all, compact: model.compact)
                    }
                    if !model.compact {
                        Text(model.official ? "官方日桶截至 " + (model.snapshot?.account.usage?.latestDate ?? "未返回") + "，可能延迟；周/月仅合计已返回日桶。"
                             : "Asia/Shanghai · 周一开始。含本机归档和子代理；其他设备及已删除记录不包含在内。")
                            .font(.system(size: 10)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                Card(compact: model.compact) {
                    HStack { Text("聊天状态").font(.system(size: 12, weight: .semibold)); Spacer()
                        Text(model.selectionLabel).font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                    Picker("聊天", selection: Binding(get: { model.selected }, set: model.select)) {
                        Text("跟随桌面端当前聊天").tag("__desktop__")
                        Text("跟随最近有活动的聊天").tag("")
                        ForEach(model.snapshot?.local.threads ?? []) { item in
                            Text(String((item.project + " / " + item.title).prefix(70))).tag(item.id)
                        }
                    }.labelsHidden().pickerStyle(.menu).frame(maxWidth: .infinity)
                    if model.selected == "__desktop__" {
                        if let id = model.desktopSelection.threadId {
                            HStack {
                                Text(id).font(.system(size: 9, design: .monospaced)).textSelection(.enabled)
                                Spacer()
                                Button("复制 UUID") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(id, forType: .string) }.buttonStyle(.plain).foregroundStyle(accent)
                            }.font(.system(size: 9))
                        } else {
                            Text(model.selectionMessage).font(.system(size: 10)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                        if model.desktopSelection.status == "permission_required" || !model.desktopAccessibilityEnabled {
                            Button(model.desktopSelection.status == "permission_required" ? "授权辅助功能并启用跟随" : "启用桌面聊天识别", action: model.requestDesktopPermission).font(.system(size: 11))
                        }
                    }
                    if let thread = chat {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(thread.title).font(.system(size: 11, weight: .medium)).lineLimit(model.compact ? 1 : 3).help(thread.title)
                            Text((thread.model ?? "未知模型") + " · " + String(thread.id.prefix(8))).font(.system(size: 9)).foregroundStyle(.secondary)
                        }
                        Meter(title: "缓存命中率", hint: "最近请求", value: thread.cacheHitPercent,
                              detail: model.compact ? "" : fullNumber(thread.cachedInputTokens) + " / " + fullNumber(thread.inputTokens) + " 输入 tokens · 累计 " + percentage(thread.sessionCacheHitPercent), compact: model.compact)
                        Meter(title: "上下文占用", hint: "最近请求估算", value: thread.contextUsedPercent,
                              detail: model.compact ? "" : fullNumber(thread.contextTokens) + " / " + fullNumber(thread.contextWindow) + " tokens",
                              warning: (thread.contextUsedPercent ?? 0) >= 85, compact: model.compact)
                        if !model.compact { Text("请求记录：" + displayTime(thread.updatedAt)).font(.system(size: 9)).foregroundStyle(.secondary) }
                    } else { Text("等待所选聊天记录…").font(.system(size: 11)).foregroundStyle(.secondary) }
                    if !model.compact {
                        DisclosureGroup("切换聊天与指标说明") {
                            Text("桌面跟随通过辅助功能读取当前聊天链接，切换时无需产生新消息。权限未授予、链接不可见或结果不唯一时不猜测 UUID。最近活动是独立模式。上下文仍是最近请求估算。")
                                .font(.system(size: 10)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }.font(.system(size: 10))
                    }
                }
                HStack {
                    Toggle("置顶", isOn: $model.pinned).toggleStyle(.checkbox).onChange(of: model.pinned) { _, value in
                        UserDefaults.standard.set(value, forKey: "pinned"); pinChanged()
                    }
                    Spacer()
                    Button(model.compact ? "完整视图" : "精简视图") {
                        model.compact.toggle(); UserDefaults.standard.set(model.compact, forKey: "compact")
                    }.buttonStyle(.plain).foregroundStyle(accent)
                }.font(.system(size: 10))
            }.padding(model.compact ? 14 : 18)
        }.background(Color(nsColor: .windowBackgroundColor)).frame(minWidth: 340)
            .preferredColorScheme(model.theme == "dark" ? .dark : model.theme == "light" ? .light : nil)
    }
    private func quotaDetail(_ window: UsageWindow, _ status: String) -> String {
        if status == "stale" { return "上次成功读取的额度，等待重新连接" }
        guard let reset = window.resetsAt else { return "未提供重置时间" }
        if reset <= Date().timeIntervalSince1970 { return "已到重置时间，等待官方更新" }
        return "重置于 " + displayDate(Date(timeIntervalSince1970: reset))
    }
}

final class UsagePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = UsageModel()
    var panel: UsagePanel!
    var statusItem: NSStatusItem!
    var observation: NSKeyValueObservation?
    var running = false
    var dismissed = false
    var watching = false
    let targetIdentifiers: Set<String> = ["com.openai.codex", "com.openai.chat", "com.openai.ChatGPT"]
    let openNotification = Notification.Name("com.codexusage.monitor.show")
    var statusPath: String?
    var renderPath: String?
    var themeMenu: NSMenu?
    var displayMenu: NSMenu?
    var infoMenu: NSMenu?
    var menuDisplayMode = UserDefaults.standard.string(forKey: "menuDisplayMode") ?? "all"

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        watching = args.contains("--watch")
        model.desktopTrackingDisabled = args.contains("--no-desktop-read")
        if args.contains("--recent-activity") { model.selected = "" }
        if let i = args.firstIndex(of: "--status"), args.indices.contains(i + 1) { statusPath = args[i + 1] }
        if let i = args.firstIndex(of: "--render"), args.indices.contains(i + 1) { renderPath = args[i + 1] }
        if let i = args.firstIndex(of: "--theme"), args.indices.contains(i + 1), ["system", "light", "dark"].contains(args[i + 1]) {
            model.theme = args[i + 1]
        }
        panel = UsagePanel(contentRect: NSRect(x: 0, y: 0, width: 382, height: 780),
                           styleMask: [.titled, .closable, .resizable, .utilityWindow], backing: .buffered, defer: false)
        panel.title = "Codex 用量面板"; panel.delegate = self
        panel.isReleasedWhenClosed = false; panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.minSize = NSSize(width: 355, height: 500)
        panel.setFrameAutosaveName("CodexUsagePanel")
        panel.contentView = NSHostingView(rootView: UsageView(model: model, bringToFront: showPanel, pinChanged: applyPin, themeChanged: applyTheme))
        applyPin(); applyTheme()
        if let screen = NSScreen.main {
            let rect = screen.visibleFrame
            let height = min(panel.frame.height, rect.height - 35)
            panel.setFrame(NSRect(x: rect.maxX - panel.frame.width - 20, y: rect.maxY - height - 20,
                                  width: panel.frame.width, height: height), display: false)
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "chart.bar.xaxis", accessibilityDescription: "Codex 用量")
            button.imagePosition = .imageLeading; button.font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium)
        }
        let menu = NSMenu()
        let show = NSMenuItem(title: "显示用量面板", action: #selector(showFromMenu), keyEquivalent: "")
        show.target = self; menu.addItem(show)
        let refresh = NSMenuItem(title: "刷新用量", action: #selector(refreshFromMenu), keyEquivalent: "")
        refresh.target = self; menu.addItem(refresh)
        let information = NSMenuItem(title: "用量与当前聊天", action: nil, keyEquivalent: "")
        infoMenu = NSMenu(); information.submenu = infoMenu; menu.addItem(information)
        let copy = NSMenuItem(title: "复制当前聊天 UUID", action: #selector(copyCurrentUUID), keyEquivalent: "")
        copy.target = self; menu.addItem(copy)
        let enableDesktop = NSMenuItem(title: "启用桌面聊天识别 / 授权辅助功能", action: #selector(enableDesktopSelection), keyEquivalent: "")
        enableDesktop.target = self; menu.addItem(enableDesktop)
        let display = NSMenuItem(title: "菜单栏显示", action: nil, keyEquivalent: "")
        displayMenu = NSMenu()
        for (name, value) in [("余额", "quota"), ("缓存 / 上下文", "metrics"), ("全部", "all")] {
            let item = NSMenuItem(title: name, action: #selector(selectMenuDisplay(_:)), keyEquivalent: "")
            item.representedObject = value; item.target = self; displayMenu!.addItem(item)
        }
        display.submenu = displayMenu; menu.addItem(display)
        let themeItem = NSMenuItem(title: "主题", action: nil, keyEquivalent: "")
        themeMenu = NSMenu()
        for (name, value) in [("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")] {
            let item = NSMenuItem(title: name, action: #selector(selectTheme(_:)), keyEquivalent: "")
            item.representedObject = value; item.target = self; themeMenu!.addItem(item)
        }
        themeItem.submenu = themeMenu; menu.addItem(themeItem)
        menu.addItem(NSMenuItem.separator())
        let info = NSMenuItem(title: "随 Codex 启动 · 原生悬浮面板", action: nil, keyEquivalent: "")
        info.isEnabled = false; menu.addItem(info)
        let quit = NSMenuItem(title: "暂停本次面板（下次打开 Codex 自动恢复）", action: #selector(pauseUntilNextLaunch), keyEquivalent: "")
        quit.target = self; menu.addItem(quit); statusItem.menu = menu
        model.onUpdate = { [weak self] in self?.updateStatus() }
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(showFromMenu), name: openNotification, object: nil)
        observation = NSWorkspace.shared.observe(\.runningApplications, options: [.initial, .new]) { [weak self] workspace, _ in
            DispatchQueue.main.async { self?.handleApplications(workspace.runningApplications) }
        }
        if !watching { showPanel() }
        applyTheme(); updateStatus()
        if args.contains("--lifecycle-test") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.lifecycleTest() }
        }
    }
    func handleApplications(_ apps: [NSRunningApplication]) {
        let active = apps.contains { !$0.isTerminated && targetIdentifiers.contains($0.bundleIdentifier ?? "") }
        transition(active)
    }
    func transition(_ active: Bool) {
        if active == running { return }
        running = active
        if active {
            dismissed = false; model.start(); showPanel()
        } else if watching {
            panel.orderOut(nil); model.stop(); dismissed = false
        }
        updateStatus()
    }
    func applyPin() { panel.level = model.pinned ? .floating : .normal }
    func applyTheme() {
        panel?.appearance = model.theme == "dark" ? NSAppearance(named: .darkAqua) : model.theme == "light" ? NSAppearance(named: .aqua) : nil
        for item in themeMenu?.items ?? [] {
            item.state = (item.representedObject as? String) == model.theme ? .on : .off
        }
        writeStatus()
    }
    @objc func selectTheme(_ sender: NSMenuItem) {
        guard let value = sender.representedObject as? String else { return }
        model.theme = value; UserDefaults.standard.set(value, forKey: "theme"); applyTheme()
    }
    func showPanel() {
        dismissed = false; model.start()
        panel.orderFrontRegardless() // Display beside Codex without stealing its keyboard focus.
        updateStatus()
    }
    @objc func showFromMenu() { showPanel() }
    @objc func refreshFromMenu() { model.refresh() }
    @objc func enableDesktopSelection() { model.requestDesktopPermission() }
    @objc func selectMenuDisplay(_ sender: NSMenuItem) {
        guard let value = sender.representedObject as? String else { return }
        menuDisplayMode = value; UserDefaults.standard.set(value, forKey: "menuDisplayMode"); updateStatus()
    }
    private var currentChat: Chat? {
        guard let snapshot = model.snapshot, (snapshot.requestId ?? 0) == model.requestGeneration else { return nil }
        return snapshot.local.selectedThread
    }
    private var currentUUID: String? {
        guard model.isRunning else { return nil }
        return model.selected == "__desktop__" ? model.desktopSelection.threadId : currentChat?.id
    }
    @objc func copyCurrentUUID() {
        guard let id = currentUUID else { return }
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(id, forType: .string)
    }
    @objc func pauseUntilNextLaunch() {
        dismissed = true; panel.orderOut(nil); model.stop(); updateStatus()
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        dismissed = true; panel.orderOut(nil); updateStatus(); return false
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showPanel(); return true
    }
    func applicationWillTerminate(_ notification: Notification) { model.stop() }
    func updateStatus() {
        statusItem?.isVisible = running || !watching || panel?.isVisible == true
        var labels: [String] = []
        for bucket in model.snapshot?.account.buckets ?? [] {
            for window in bucket.windows where window.visibleByDefault {
                let name = window.durationMinutes == 300 ? "5h" : window.durationMinutes == 10080 ? "W" : window.label
                labels.append(name + " " + percentage(window.remainingPercent))
            }
        }
        let quotaLabels = labels
        let metrics = ["缓存 " + percentage(currentChat?.cacheHitPercent), "上下文 " + percentage(currentChat?.contextUsedPercent)]
        if menuDisplayMode == "metrics" { labels = metrics }
        else if menuDisplayMode == "all" { labels += metrics }
        statusItem?.button?.title = labels.isEmpty ? " Codex" : " " + labels.joined(separator: " · ")
        statusItem?.button?.toolTip = "Codex 用量\n" + model.selectionLabel + "\nUUID: " + (currentUUID ?? "未识别")
        for item in displayMenu?.items ?? [] { item.state = (item.representedObject as? String) == menuDisplayMode ? .on : .off }
        infoMenu?.removeAllItems()
        var info = [model.selectionLabel,
                    "UUID: " + (currentUUID ?? "未识别"),
                    "缓存命中率: " + percentage(currentChat?.cacheHitPercent),
                    "上下文占用估算: " + percentage(currentChat?.contextUsedPercent)]
        let total = model.official ? model.snapshot?.account.usage?.totals : model.snapshot?.local.totals
        info += [model.official ? "Token 来源：账号官方（可能延迟）" : "Token 来源：本机日志",
                 "今日: " + fullNumber(total?.today), "本周: " + fullNumber(total?.week),
                 "本月: " + fullNumber(total?.month), "累计: " + fullNumber(total?.all)]
        info += quotaLabels.map { "剩余额度: " + $0 }
        if model.selected == "__desktop__" { info.append(model.selectionMessage) }
        for text in info {
            let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
            item.isEnabled = false; infoMenu?.addItem(item)
        }
        writeStatus()
        if let path = renderPath, model.snapshot?.account.status == "ok" {
            renderPath = nil
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.render(path) }
        }
    }
    func writeStatus(extra: [String: Any] = [:]) {
        guard let path = statusPath else { return }
        var data: [String: Any] = ["native": true, "watching": watching, "codexRunning": running,
                                  "panelVisible": panel?.isVisible == true, "workerRunning": model.isRunning,
                                  "pid": ProcessInfo.processInfo.processIdentifier, "bundleId": Bundle.main.bundleIdentifier ?? "",
                                  "accountStatus": model.snapshot?.account.status ?? "loading", "theme": model.theme,
                                  "effectiveAppearance": panel?.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua])?.rawValue ?? "",
                                  "chatMetricsAvailable": currentChat != nil,
                                  "selectionMode": model.selectionLabel,
                                  "desktopSelectionStatus": model.desktopSelection.status,
                                  "desktopThreadId": model.desktopSelection.threadId as Any? ?? NSNull(),
                                  "desktopHostKind": model.desktopSelection.hostKind as Any? ?? NSNull(),
                                  "desktopSelectionSource": model.desktopSelection.source as Any? ?? NSNull(),
                                  "desktopSelectionUpdatedAt": ISO8601DateFormatter().string(from: model.desktopUpdatedAt)]
        data["menuDisplayMode"] = menuDisplayMode
        data["menuBarTitle"] = statusItem?.button?.title ?? ""
        data["menuInformationRows"] = infoMenu?.items.count ?? 0
        data.merge(extra) { _, new in new }
        if let bytes = try? JSONSerialization.data(withJSONObject: data, options: [.prettyPrinted, .sortedKeys]) {
            try? bytes.write(to: URL(fileURLWithPath: path), options: .atomic)
        }
    }
    func render(_ path: String) {
        guard let view = panel.contentView else { return }
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        if let bytes = bitmap.representation(using: .png, properties: [:]) { try? bytes.write(to: URL(fileURLWithPath: path)) }
    }
    func lifecycleTest() {
        watching = true
        running = true
        transition(false)
        let hidden = !panel.isVisible && !model.isRunning
        transition(true)
        let shown = panel.isVisible && model.isRunning
        _ = windowShouldClose(panel)
        let closeHides = !panel.isVisible && model.isRunning
        showPanel()
        let reopen = panel.isVisible && model.isRunning
        let previousDisplay = menuDisplayMode
        menuDisplayMode = "metrics"; updateStatus()
        let metricsMenu = statusItem.button?.title.contains("缓存") == true && statusItem.button?.title.contains("上下文") == true
        menuDisplayMode = "all"; updateStatus()
        let menuDetails = (infoMenu?.items.count ?? 0) >= 9 && (displayMenu?.items.count ?? 0) == 3
        menuDisplayMode = previousDisplay; updateStatus()
        writeStatus(extra: ["lifecyclePassed": hidden && shown && closeHides && reopen,
                            "menuMetricsPassed": metricsMenu, "menuDetailsPassed": menuDetails,
                            "hideOnCodexExit": hidden, "showOnCodexLaunch": shown,
                            "closeHidesPanel": closeHides, "menuReopensPanel": reopen])
        model.onUpdate = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { NSApplication.shared.terminate(nil) }
    }
}

@main struct CodexUsageApplication {
    static func main() {
        let args = CommandLine.arguments
        let diagnosticInstance = args.contains("--no-desktop-read") && (args.contains("--render") || args.contains("--lifecycle-test"))
        if !diagnosticInstance, let ident = Bundle.main.bundleIdentifier {
            let current = ProcessInfo.processInfo.processIdentifier
            if NSRunningApplication.runningApplications(withBundleIdentifier: ident).contains(where: { $0.processIdentifier != current }) {
                DistributedNotificationCenter.default().postNotificationName(Notification.Name("com.codexusage.monitor.show"), object: nil, userInfo: nil, deliverImmediately: true)
                return
            }
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = AppDelegate(); app.delegate = delegate
        app.run()
        withExtendedLifetime(delegate) {}
    }
}
