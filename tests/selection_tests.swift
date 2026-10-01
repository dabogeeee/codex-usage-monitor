import Foundation

@main struct SelectionTests {
    static func main() {
        let first = "11111111-1111-4111-8111-111111111111"
        let second = "22222222-2222-4222-8222-222222222222"
        let a = "app://-/local/" + first
        let b = "app://-/local/" + second
        assert(DesktopSelection.threadRoute(a)?.id == first)
        assert(DesktopSelection.threadRoute("codex://threads/" + first)?.id == first)
        assert(DesktopSelection.threadRoute("app://-/remote/" + first)?.host == "remote")
        assert(DesktopSelection.threadRoute("https://example.com/local/" + first) == nil)
        assert(DesktopSelection.threadRoute("app://-/local/not-a-uuid") == nil)
        assert(DesktopSelection.resolve([(a, "page", false), (b, "false", false)]).threadId == first)
        // A selected idle chat must win even when another chat is generating tokens.
        assert(DesktopSelection.resolve([(a, "false", false), (b, "page", false)]).threadId == second)
        assert(DesktopSelection.resolve([(a, "page", false), (a, "page", false)]).threadId == first)
        assert(DesktopSelection.resolve([(a, "page", false), (b, "page", false)]).status == "ambiguous")
        assert(DesktopSelection.resolve([(a, "false", false)]).threadId == nil)
        assert(DesktopSelection.resolve([]).status == "no_selected_chat")
        assert(DesktopSelection.resolveLabels(["Old idle chat"], names: [(first, "Currently generating"), (second, "Old idle chat")]).threadId == second)
        assert(DesktopSelection.resolveLabels(["Same title"], names: [(first, "Same title"), (second, "Same title")]).status == "ambiguous")
        assert(DesktopSelection.resolveLabels(["Partial"], names: [(first, "Partial match must not count")]).threadId == nil)
        print("Desktop selection route and ambiguity tests passed")
    }
}
