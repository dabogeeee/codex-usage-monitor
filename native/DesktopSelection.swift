import AppKit
import ApplicationServices
import Foundation
import SQLite3

struct DesktopSelection: Equatable, Sendable {
    let status: String
    let threadId: String?
    let hostKind: String?
    var source: String? = nil

    static func threadRoute(_ value: String) -> (id: String, host: String)? {
        guard let url = URLComponents(string: value), let scheme = url.scheme?.lowercased(),
              ["app", "codex", "file"].contains(scheme) else { return nil }
        let path = url.percentEncodedPath.removingPercentEncoding ?? url.path
        let parts = path.split(separator: "/").map(String.init)
        let uuidPattern = "^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"
        func uuid(_ id: String) -> Bool { id.range(of: uuidPattern, options: .regularExpression) != nil }
        if scheme == "codex", url.host == "threads", parts.count == 1, uuid(parts[0]) {
            return (parts[0].lowercased(), "local")
        }
        if parts.count == 2, ["local", "remote", "thread", "threads"].contains(parts[0]), uuid(parts[1]) {
            return (parts[1].lowercased(), parts[0] == "remote" ? "remote" : "local")
        }
        if parts.count == 3, parts[0] == "hotkey-window", parts[1] == "thread", uuid(parts[2]) {
            return (parts[2].lowercased(), "local")
        }
        return nil
    }

    static func resolve(_ candidates: [(url: String, current: String?, selected: Bool)]) -> DesktopSelection {
        let routes = candidates.compactMap { item -> (id: String, host: String)? in
            guard item.current == "page" || item.current == "true" || item.selected else { return nil }
            return threadRoute(item.url)
        }
        let ids = Set(routes.map(\.id))
        guard ids.count == 1, let route = routes.first else {
            return DesktopSelection(status: ids.isEmpty ? "no_selected_chat" : "ambiguous", threadId: nil, hostKind: nil)
        }
        return DesktopSelection(status: "selected", threadId: route.id, hostKind: route.host, source: "ax_current_route")
    }

    static func resolveLabels(_ labels: [String], names: [(id: String, name: String)]) -> DesktopSelection {
        let exact = Set(labels.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty })
        let matching = names.filter { exact.contains($0.name.trimmingCharacters(in: .whitespacesAndNewlines)) }
        let ids = Set(matching.map(\.id))
        guard ids.count == 1, let id = ids.first else {
            return DesktopSelection(status: ids.isEmpty ? "no_selected_chat" : "ambiguous", threadId: nil, hostKind: nil)
        }
        return DesktopSelection(status: "selected", threadId: id, hostKind: "local", source: "ax_current_label_unique_metadata")
    }
}

/// Selection metadata only. The user can enable Electron's documented metadata
/// exposure switch; no text/value attributes or navigation actions are requested.
final class DesktopSelectionReader: Sendable {
    private let targetIds = Set(["com.openai.codex", "com.openai.chat", "com.openai.ChatGPT"])
    private let maxNodes = 3500
    private let maxDepth = 24

    func read(enableAccessibility: Bool = false) -> DesktopSelection {
        guard AXIsProcessTrusted() else {
            return DesktopSelection(status: "permission_required", threadId: nil, hostKind: nil)
        }
        let apps = NSWorkspace.shared.runningApplications.filter { !$0.isTerminated && targetIds.contains($0.bundleIdentifier ?? "") }
        guard !apps.isEmpty else { return DesktopSelection(status: "codex_closed", threadId: nil, hostKind: nil) }
        let front = NSWorkspace.shared.frontmostApplication
        let active = apps.first { $0.processIdentifier == front?.processIdentifier } ?? (apps.count == 1 ? apps[0] : nil)
        guard let active else { return DesktopSelection(status: "ambiguous_windows", threadId: nil, hostKind: nil) }
        let application = AXUIElementCreateApplication(active.processIdentifier)
        AXUIElementSetMessagingTimeout(application, 0.15)
        // Enabled only after the user presses this companion's enable button.
        // https://github.com/electron/electron/blob/main/docs/tutorial/accessibility.md
        if enableAccessibility, attribute(application, "AXManualAccessibility") as? Bool != true {
            _ = AXUIElementSetAttributeValue(application, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        }
        guard let window = element(application, "AXFocusedWindow") ?? element(application, "AXMainWindow") else {
            return DesktopSelection(status: "window_unavailable", threadId: nil, hostKind: nil)
        }
        var pending: [(AXUIElement, Int)] = [(window, 0)]
        var cursor = 0
        var candidates: [(url: String, current: String?, selected: Bool)] = []
        var selectedLabels: [String] = []
        var visited: Set<AXUIElement> = []
        var depthLimited = false
        let deadline = Date().addingTimeInterval(0.7)
        while cursor < pending.count && cursor < maxNodes && Date() < deadline {
            let (node, depth) = pending[cursor]; cursor += 1
            if visited.contains(node) { continue }; visited.insert(node)
            let role = string(node, "AXRole") ?? ""
            // Chat bodies and editable text are not inspected.
            if ["AXStaticText", "AXTextArea", "AXTextField", "AXSecureTextField"].contains(role) { continue }
            let current = string(node, "AXARIACurrent")
            let selected = attribute(node, "AXSelected") as? Bool ?? false
            if current == "page" || current == "true" || selected {
                if let url = url(node) { candidates.append((url, current, selected)) }
                else if ["AXButton", "AXLink", "AXRow"].contains(role),
                        let label = [string(node, "AXTitle"), string(node, "AXDescription")].compactMap({ $0 }).first(where: { !$0.isEmpty }) {
                    selectedLabels.append(label)
                }
            }
            let descendants = children(node)
            if depth < maxDepth { for child in descendants { pending.append((child, depth + 1)) } }
            else if !descendants.isEmpty { depthLimited = true }
        }
        if cursor < pending.count || depthLimited {
            return DesktopSelection(status: "scan_incomplete", threadId: nil, hostKind: nil)
        }
        let route = DesktopSelection.resolve(candidates)
        if route.status != "no_selected_chat" { return route }
        // Current Codex releases render some sidebar entries as buttons rather
        // than links. Resolve only an exact, unique saved display name; duplicates
        // are rejected instead of using recency, token activity or fuzzy matching.
        return DesktopSelection.resolveLabels(selectedLabels, names: savedThreadNames())
    }

    private func attribute(_ node: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(node, name as CFString, &value) == .success ? value : nil
    }
    private func string(_ node: AXUIElement, _ name: String) -> String? { attribute(node, name) as? String }
    private func url(_ node: AXUIElement) -> String? {
        if let value = attribute(node, "AXURL") as? URL { return value.absoluteString }
        return string(node, "AXURL")
    }
    private func element(_ node: AXUIElement, _ name: String) -> AXUIElement? {
        guard let value = attribute(node, name), CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }
    private func children(_ node: AXUIElement) -> [AXUIElement] {
        var value: CFArray?
        guard AXUIElementCopyAttributeValues(node, "AXChildren" as CFString, 0, maxNodes, &value) == .success else { return [] }
        return value as? [AXUIElement] ?? []
    }
    private func savedThreadNames() -> [(id: String, name: String)] {
        let home = ProcessInfo.processInfo.environment["CODEX_HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex")
        let files = (try? FileManager.default.contentsOfDirectory(at: home, includingPropertiesForKeys: nil)) ?? []
        let database = files.filter { $0.lastPathComponent.hasPrefix("state_") && $0.pathExtension == "sqlite" }
            .sorted { version($0) > version($1) }.first
        guard let database else { return [] }
        var db: OpaquePointer?
        guard sqlite3_open_v2(database.path, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            if let db { sqlite3_close(db) }; return []
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 20)
        var statement: OpaquePointer?
        let queries = ["SELECT id, COALESCE(NULLIF(name, ''), title) FROM threads WHERE source NOT LIKE '%subagent%'",
                       "SELECT id, title FROM threads WHERE source NOT LIKE '%subagent%'"]
        for query in queries {
            if sqlite3_prepare_v2(db, query, -1, &statement, nil) == SQLITE_OK { break }
            if let statement { sqlite3_finalize(statement) }
            statement = nil
        }
        guard let statement else { return [] }
        defer { sqlite3_finalize(statement) }
        var names: [(id: String, name: String)] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            guard let id = sqlite3_column_text(statement, 0), let name = sqlite3_column_text(statement, 1) else { continue }
            let ident = String(cString: id), title = String(cString: name)
            if DesktopSelection.threadRoute("codex://threads/" + ident) != nil { names.append((ident, title)) }
        }
        return names
    }
    private func version(_ path: URL) -> Int {
        Int(path.deletingPathExtension().lastPathComponent.replacingOccurrences(of: "state_", with: "")) ?? -1
    }
}
