// TraeBar —— TraeWork 账号轮转的原生菜单栏（托盘）App
// 图标：菜单栏常驻；点开 = 账号切换子菜单（来自档案仓）+ 全部签到 + 打开助手。
// 底层全部复用 scripts/ 里的 twa_* 工具，本文件不含任何业务逻辑。
import AppKit

let SCRIPTS_DIR = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/20_TraeWork多账号/scripts"
let PYTHON = "/Users/imac/.workbuddy/binaries/python/envs/default/bin/python"

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()

    private var busy = false          // 有后台任务在跑（切换/签到）
    private var busyText = ""
    private var accounts: [[String: Any]] = []
    private var currentUid: String? = nil

    // MARK: 生命周期

    func applicationDidFinishLaunching(_ notification: Notification) {
        let icon = NSImage(systemSymbolName: "arrow.left.arrow.right.circle",
                           accessibilityDescription: "TraeWork 账号切换")
        icon?.isTemplate = true
        statusItem.button?.image = icon
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        refreshSync()
        rebuild()
    }

    // MARK: 菜单构建

    func menuNeedsUpdate(_ menu: NSMenu) {
        // 同步取最新状态（本地解密，~100ms，无网络请求，菜单展开无感）
        refreshSync()
        rebuild()
    }

    private func rebuild() {
        menu.removeAllItems()

        if busy {
            let it = NSMenuItem(title: "⏳ \(busyText)", action: nil, keyEquivalent: "")
            it.isEnabled = false
            menu.addItem(it)
            addCommonItems()
            return
        }

        // 当前登录行
        let curName = displayName(uid: currentUid) ?? "未登录 / 解析失败"
        let head = NSMenuItem(title: "当前登录：\(curName)", action: nil, keyEquivalent: "")
        head.isEnabled = false
        menu.addItem(head)

        // 账号清单
        let withProfile = accounts.filter { $0["has_profile"] as? Bool == true }
        if !withProfile.isEmpty { menu.addItem(.separator()) }
        for a in withProfile {
            let uid = a["uid"] as? String ?? ""
            let title = displayName(of: a) ?? uid
            let it = NSMenuItem(title: title, action: #selector(switchTo(_:)), keyEquivalent: "")
            it.target = self
            it.representedObject = (a["phone"] as? String) ?? ""
            it.isEnabled = (uid != currentUid)      // 当前账号置灰打勾
            if uid == currentUid { it.state = .on }
            menu.addItem(it)
        }
        let noProfile = accounts.filter { $0["has_profile"] as? Bool != true }
        for a in noProfile {
            let it = NSMenuItem(title: "◇ \(displayName(of: a) ?? "?")（无档案，守护会自动合成）",
                                action: nil, keyEquivalent: "")
            it.isEnabled = false
            menu.addItem(it)
        }

        addCommonItems()
    }

    private func addCommonItems() {
        menu.addItem(.separator())

        let checkin = NSMenuItem(title: "全部签到 + 刷新余额", action: #selector(checkinAll(_:)), keyEquivalent: "")
        checkin.target = self
        checkin.isEnabled = !busy
        menu.addItem(checkin)

        let open = NSMenuItem(title: "打开 TraeWorkAssistant", action: #selector(openAssistant(_:)), keyEquivalent: "")
        open.target = self
        menu.addItem(open)

        menu.addItem(.separator())
        let quit = NSMenuItem(title: "退出 TraeBar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quit)
    }

    private func displayName(of a: [String: Any]) -> String? {
        guard let name = (a["name"] as? String) ?? (a["phone"] as? String) else { return nil }
        if let c = a["credits"] as? Int { return "\(name)   （额度 \(c)）" }
        return name
    }

    private func displayName(uid: String?) -> String? {
        guard let uid else { return nil }
        if let a = accounts.first(where: { $0["uid"] as? String == uid }) {
            return a["phone"] as? String ?? a["name"] as? String
        }
        return uid
    }

    // MARK: 状态获取

    private func refreshSync() {
        guard !busy else { return }
        let out = runSync([PYTHON, SCRIPTS_DIR + "/twa_status_json.py"])
        if let data = out.data(using: .utf8),
           let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            currentUid = j["current_uid"] as? String
            accounts = j["accounts"] as? [[String: Any]] ?? []
        }
    }

    // MARK: 动作

    @objc private func switchTo(_ sender: NSMenuItem) {
        guard let phone = sender.representedObject as? String, !phone.isEmpty else { return }
        startBusy("切换到 \(phone)…")
        run([PYTHON, SCRIPTS_DIR + "/twa_switch_account.py", "switch", phone]) { [weak self] out in
            let ok = out.contains("已切换") || out.contains("完成") || out.contains("✅")
            self?.endBusy(ok ? "已切换" : "切换失败（点开菜单看输出）", output: out)
        }
    }

    @objc private func checkinAll(_ sender: NSMenuItem) {
        startBusy("全部签到中…")
        run([PYTHON, SCRIPTS_DIR + "/twa_checkin.py", "all"]) { [weak self] out in
            let ok = !out.contains("失败") || out.contains("签到成功")
            self?.endBusy(ok ? "签到完成" : "签到有失败项", output: out)
        }
    }

    @objc private func openAssistant(_ sender: NSMenuItem) {
        NSWorkspace.shared.openApplication(
            at: URL(fileURLWithPath: "/Applications/TraeWorkAssistant.app"),
            configuration: NSWorkspace.OpenConfiguration(),
            completionHandler: nil)
    }

    // MARK: 后台执行

    private func startBusy(_ text: String) {
        busy = true
        busyText = text
        statusItem.button?.appearsDisabled = true
        rebuild()
    }

    private func endBusy(_ summary: String, output: String) {
        busy = false
        statusItem.button?.appearsDisabled = false
        notify(summary, detail: lastLines(output, 3))
        refreshSync()
        rebuild()
    }

    private func notify(_ title: String, detail: String) {
        let esc = detail.replacingOccurrences(of: "\\", with: "\\\\")
                        .replacingOccurrences(of: "\"", with: "\\\"")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", "display notification \"\(esc)\" with title \"TraeBar · \(title)\" sound name \"Pop\""]
        try? p.run()
    }

    private func run(_ args: [String], completion: @escaping (String) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: args[0])
            p.arguments = Array(args.dropFirst())
            var env = ProcessInfo.processInfo.environment
            env.removeValue(forKey: "ELECTRON_RUN_AS_NODE")   // 防御：绝不能传给客户端
            p.environment = env
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            do { try p.run() } catch {
                DispatchQueue.main.async { completion("启动失败: \(error.localizedDescription)") }
                return
            }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            DispatchQueue.main.async {
                completion(String(data: data, encoding: .utf8) ?? "")
            }
        }
    }

    private func runSync(_ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: args[0])
        p.arguments = Array(args.dropFirst())
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "ELECTRON_RUN_AS_NODE")
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return "" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? ""
    }

    private func lastLines(_ s: String, _ n: Int) -> String {
        let lines = s.split(separator: "\n").map { String($0) }.filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        return lines.suffix(n).joined(separator: "\n")
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // 无 Dock 图标
app.run()
