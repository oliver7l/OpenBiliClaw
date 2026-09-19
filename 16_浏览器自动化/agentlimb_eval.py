#!/usr/bin/env python3
"""AgentLimb 浏览器桥接通用封装 —— 在用户真实 Chrome（带登录态）里执行 JS / 导航。

用法:
    from agentlimb_eval import AgentLimbBrowser
    b = AgentLimbBrowser()
    b.start()                       # 建会话（要求 Chrome 开着 + AgentLimb 侧边栏面板开着）
    print(b.tabs())                 # 列标签页拿 tabId
    b.navigate("https://weread.qq.com/")
    v = b.eval_js('1+1', tab_id=123)   # 显式传 tabId，防止用户切走 active tab

前置: Bridge 常驻 127.0.0.1:7791；extensionOffline 时让用户在
chrome://extensions 刷新 AgentLimb 扩展并重开侧边栏。
"""
import json
import re
import subprocess

NODE = "/Users/imac/.workbuddy/binaries/node/versions/22.22.2-3/bin/node"
CLI = ("/Volumes/固态硬盘1T/002-探索项目/1172-github项目/AgentLimb/"
       "agentlimb-extension/agentlimb-chrome-v0.1.4/kernel/bridge/mvp/terminal-client.mjs")


class AgentLimbBrowser:
    def _cli(self, *args, timeout=120):
        r = subprocess.run([NODE, CLI, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout

    def start(self, name="WorkBuddy"):
        out = self._cli("start", "--name", name, "--type", "claude-code")
        if '"ok": false' in out[:400]:
            raise RuntimeError("AgentLimb 扩展离线: 让用户刷新扩展+开侧边栏。"
                               + out[:300])
        return json.loads(out[:out.rindex("}") + 1])

    def call(self, tool, params):
        out = self._cli("call", "--tool", tool, "--params",
                        json.dumps(params, ensure_ascii=False))
        m = re.search(r'"value": (".*?")\n', out, re.S)
        if m:
            return json.loads(json.loads(m.group(1)))  # JSON-in-JSON
        m = re.search(r'"ok": (true|false)', out)
        return {"raw": out[-800:], "ok": bool(m and m.group(1) == "true")}

    def tabs(self):
        return self.call("tabs_context", {})

    def navigate(self, url):
        return self.call("navigate", {"url": url})

    def eval_js(self, expression, tab_id=None):
        params = {"expression": expression}
        if tab_id is not None:
            params["tabId"] = tab_id
        return self.call("javascript_eval", params)


if __name__ == "__main__":
    b = AgentLimbBrowser()
    print(b.start())
    print(json.dumps(b.tabs(), ensure_ascii=False)[:500])
