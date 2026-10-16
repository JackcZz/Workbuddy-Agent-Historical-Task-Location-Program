import Cocoa
import WebKit
import Foundation

// MARK: - 配置

let DEFAULT_PORT = 8901
let PANEL_WIDTH: CGFloat = 392
let DRAG_HEIGHT: CGFloat = 30      // 顶部可拖动区高度
let DRAG_RIGHT_MARGIN: CGFloat = 108 // 右侧留给页面按钮，避免挡住交互
let BAR_HEIGHT: CGFloat = 38       // 底部原生工具条

func configuredPort() -> Int {
    let env = ProcessInfo.processInfo.environment
    if let s = env["WB_HISTORY_PORT"], let p = Int(s), p > 0 { return p }
    let ud = UserDefaults.standard.integer(forKey: "WBHistoryPort")
    return ud > 0 ? ud : DEFAULT_PORT
}

func baseURL(_ port: Int) -> String { "http://127.0.0.1:\(port)" }

// MARK: - 顶部拖动区（只占左侧，右侧留给页面按钮）

class DragHandle: NSView {
    var downPoint: NSPoint = .zero
    var downOrigin: NSPoint = .zero

    override var acceptsFirstResponder: Bool { false }

    override func mouseDown(with event: NSEvent) {
        downPoint = NSEvent.mouseLocation
        downOrigin = window?.frame.origin ?? .zero
    }

    override func mouseDragged(with event: NSEvent) {
        guard let win = window else { return }
        let now = NSEvent.mouseLocation
        win.setFrameOrigin(NSPoint(x: downOrigin.x + (now.x - downPoint.x),
                                   y: downOrigin.y + (now.y - downPoint.y)))
    }

    override func mouseUp(with event: NSEvent) {
        // 记住位置，下次启动还在原地
        if let f = window?.frame {
            UserDefaults.standard.set(Double(f.origin.x), forKey: "WBPanelX")
            UserDefaults.standard.set(Double(f.origin.y), forKey: "WBPanelY")
        }
    }
}

// MARK: - 悬浮面板

class FloatPanel: NSPanel {

    var webView: WKWebView!
    var port: Int = DEFAULT_PORT
    var onToggleConsole: (() -> Void)?

    convenience init(port: Int) {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let h = min(screen.height - 60, screen.height * 0.82)
        var x = screen.maxX - PANEL_WIDTH - 14
        var y = screen.midY - h / 2

        // 恢复上次位置（若还在屏幕内）
        let ud = UserDefaults.standard
        let sx = ud.double(forKey: "WBPanelX"), sy = ud.double(forKey: "WBPanelY")
        if sx != 0 && sy != 0 && sx > screen.minX - 200 && sx < screen.maxX {
            x = CGFloat(sx); y = CGFloat(sy)
        }

        self.init(contentRect: NSRect(x: x, y: y, width: PANEL_WIDTH, height: h),
                  styleMask: [.borderless, .resizable, .fullSizeContentView],
                  backing: .buffered, defer: false)

        self.port = port
        setup()
    }

    override var canBecomeKey: Bool { true }   // 允许输入（搜索框）
    override var canBecomeMain: Bool { true }

    private func setup() {
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        level = .floating
        isMovableByWindowBackground = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        titleVisibility = .hidden
        titlebarAppearsTransparent = true

        // 圆角容器
        let root = NSView(frame: contentView!.bounds)
        root.autoresizingMask = [.width, .height]
        root.wantsLayer = true
        root.layer?.cornerRadius = 12
        root.layer?.masksToBounds = true
        root.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        contentView?.addSubview(root)

        // WebView
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        webView = WKWebView(frame: NSRect(x: 0, y: BAR_HEIGHT,
                                          width: PANEL_WIDTH,
                                          height: root.bounds.height - BAR_HEIGHT),
                            configuration: cfg)
        webView.autoresizingMask = [.width, .height]
        webView.setValue(false, forKey: "drawsBackground")
        root.addSubview(webView)

        // 顶部拖动区
        let drag = DragHandle(frame: NSRect(x: 0,
                                            y: root.bounds.height - DRAG_HEIGHT,
                                            width: PANEL_WIDTH - DRAG_RIGHT_MARGIN,
                                            height: DRAG_HEIGHT))
        drag.autoresizingMask = [.width, .minYMargin]
        root.addSubview(drag)

        // 底部原生工具条
        let bar = NSView(frame: NSRect(x: 0, y: 0, width: PANEL_WIDTH, height: BAR_HEIGHT))
        bar.autoresizingMask = [.width]
        bar.wantsLayer = true
        bar.layer?.backgroundColor = NSColor(calibratedWhite: 0.97, alpha: 1).cgColor
        root.addSubview(bar)

        let b1 = makeButton("控制台", #selector(openConsole))
        b1.frame = NSRect(x: 10, y: 6, width: 74, height: 26)
        bar.addSubview(b1)

        let b2 = makeButton("刷新", #selector(reload))
        b2.frame = NSRect(x: 92, y: 6, width: 58, height: 26)
        bar.addSubview(b2)

        let b3 = makeButton("复位", #selector(resetPosition))
        b3.frame = NSRect(x: 158, y: 6, width: 58, height: 26)
        bar.addSubview(b3)

        let b4 = makeButton("隐藏", #selector(hidePanel))
        b4.frame = NSRect(x: PANEL_WIDTH - 74, y: 6, width: 64, height: 26)
        b4.autoresizingMask = [.minXMargin]
        bar.addSubview(b4)
    }

    private func makeButton(_ title: String, _ sel: Selector) -> NSButton {
        let b = NSButton(title: title, target: self, action: sel)
        b.bezelStyle = .rounded
        b.font = NSFont.systemFont(ofSize: 12)
        return b
    }

    // MARK: 动作

    @objc func openConsole() {
        NSWorkspace.shared.open(URL(string: baseURL(port))!)
    }

    @objc func reload() {
        if !ensureServer() { return }
        webView.reload()
    }

    @objc func hidePanel() { orderOut(nil) }

    @objc func resetPosition() {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let h = min(screen.height - 60, screen.height * 0.82)
        setFrame(NSRect(x: screen.maxX - PANEL_WIDTH - 14,
                        y: screen.midY - h / 2,
                        width: PANEL_WIDTH, height: h), display: true, animate: true)
        UserDefaults.standard.removeObject(forKey: "WBPanelX")
        UserDefaults.standard.removeObject(forKey: "WBPanelY")
    }

    // MARK: 加载

    func load() {
        guard ensureServer() else {
            showMessage("服务启动失败，请检查 server.py 是否可用")
            return
        }
        webView.load(URLRequest(url: URL(string: baseURL(port) + "/float")!))
    }

    private func showMessage(_ text: String) {
        let html = "<html><body style='font:13px -apple-system;padding:20px;color:#666'>"
            + text + "</body></html>"
        webView.loadHTMLString(html, baseURL: nil)
    }

    /// 确保本地服务在跑；返回是否可用
    private func ensureServer() -> Bool {
        if healthOK() { return true }
        startServer()
        for _ in 0..<40 {   // 最多等 ~10 秒
            Thread.sleep(forTimeInterval: 0.25)
            if healthOK() { return true }
        }
        return false
    }

    private func healthOK() -> Bool {
        guard let url = URL(string: baseURL(port) + "/api/health") else { return false }
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        let task = URLSession.shared.dataTask(with: url) { _, resp, _ in
            if let r = resp as? HTTPURLResponse, r.statusCode == 200 { ok = true }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + 2)
        return ok
    }

    private func startServer() {
        guard let py = findPython(), let script = serverScriptPath() else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = [script, "--port", String(port), "--no-open"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
    }

    private func findPython() -> String? {
        let cands = ["/usr/bin/python3", "/opt/homebrew/bin/python3",
                     "/usr/local/bin/python3", "/bin/python3"]
        for c in cands where FileManager.default.isExecutableFile(atPath: c) { return c }
        // 兜底：which
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = ["which", "python3"]
        let pipe = Pipe()
        task.standardOutput = pipe
        try? task.run(); task.waitUntilExit()
        let s = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return s.isEmpty ? nil : s
    }

    private func serverScriptPath() -> String? {
        // 1) .app 内 Resources
        if let r = Bundle.main.resourceURL {
            let p = r.appendingPathComponent("server.py").path
            if FileManager.default.fileExists(atPath: p) { return p }
        }
        // 2) 与可执行文件同级（开发态）
        let exe = Bundle.main.executablePath ?? ""
        let dir = (exe as NSString).deletingLastPathComponent
        let p = (dir as NSString).appendingPathComponent("server.py")
        if FileManager.default.fileExists(atPath: p) { return p }
        // 3) 上级目录（源码仓库布局：build/WBHistoryPanel.app）
        let up = (dir as NSString).deletingLastPathComponent
        for rel in ["server.py", "../server.py", "../../server.py"] {
            let q = (up as NSString).appendingPathComponent(rel)
            if FileManager.default.fileExists(atPath: q) { return q }
        }
        return nil
    }
}

// MARK: - AppDelegate（菜单栏常驻 + 面板管理）

class AppDelegate: NSObject, NSApplicationDelegate {
    var panel: FloatPanel!
    var statusItem: NSStatusItem!

    func applicationDidFinishLaunching(_ a: Notification) {
        NSApp.setActivationPolicy(.accessory)   // 不显示 Dock 图标

        let port = configuredPort()
        panel = FloatPanel(port: port)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = statusItem.button {
            b.title = "会话"
            b.toolTip = "WorkBuddy 会话面板（点击显示/隐藏）"
            b.target = self
            b.action = #selector(toggle)
        }

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "显示/隐藏面板", action: #selector(toggle), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "打开完整控制台", action: #selector(openConsole), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "刷新", action: #selector(reload), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "复位到屏幕右侧", action: #selector(resetPosition), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "退出", action: #selector(quit), keyEquivalent: "q"))
        statusItem.menu = menu

        panel.load()
        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func toggle() {
        if panel.isVisible {
            panel.orderOut(nil)
        } else {
            panel.load()
            panel.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    @objc func openConsole() { panel.openConsole() }
    @objc func reload() { panel.reload() }
    @objc func resetPosition() { panel.resetPosition() }

    @objc func quit() {
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }
}

// MARK: - main

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
