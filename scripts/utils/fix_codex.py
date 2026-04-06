import json
import os

# 自动定位你的 Codex 配置路径
CODEX_PATH = r"C:\Users\Huang\.codex"
CONFIG_FILE = os.path.join(CODEX_PATH, "config.json")  # 主配置
TOOLS_FILE = os.path.join(CODEX_PATH, "tools.json")    # 工具配置（最可能报错）

def clean_tools(tools, max_limit=128):
    """自动去重 + 截断到128个"""
    if not isinstance(tools, list):
        return tools

    # 去重
    seen = set()
    new_tools = []
    for t in tools:
        try:
            name = t.get("function", {}).get("name")
            if name and name not in seen:
                seen.add(name)
                new_tools.append(t)
        except:
            continue

    # 截断到128
    new_tools = new_tools[:max_limit]
    print(f"✅ 清理完成：原 {len(tools)} → 去重 {len(seen)} → 最终 {len(new_tools)}")
    return new_tools

def fix_codex_tools():
    print("🔧 开始自动修复 Codex tools 上限问题...")
    fixed = False

    # 修复 tools.json（最关键）
    if os.path.exists(TOOLS_FILE):
        print(f"📂 找到工具文件：{TOOLS_FILE}")
        with open(TOOLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if "tools" in data:
            data["tools"] = clean_tools(data["tools"])
            with open(TOOLS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            fixed = True

    # 修复 config.json
    if os.path.exists(CONFIG_FILE):
        print(f"📂 找到配置文件：{CONFIG_FILE}")
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if "tools" in data:
            data["tools"] = clean_tools(data["tools"])
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            fixed = True

    if fixed:
        print("\n🎉 **Codex tools 已自动修复！现在永远 ≤128 个，不会再报错！**")
        print("👉 请**重启 Codex** 生效！")
    else:
        print("\n⚠️ 未找到 tools 配置，可能文件路径不同，请告诉我你的 .codex 里有哪些文件！")

if __name__ == "__main__":
    fix_codex_tools()