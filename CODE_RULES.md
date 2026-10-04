# Codex 工作规范（自优化规则）

> 写于 2026-07-05，基于 MediaExportTool 升级任务的实战教训。

## 1. 文件搜索：永远从三个维度找

| 维度 | 路径 | 示例 |
|------|------|------|
| 工作区根 | `{workspace_root}` | `D:\Codex Work\` |
| 工作区平级 | `D:\Codex\config\` | Skills、配置 |
| C盘用户 | `%USERPROFILE%`, `%APPDATA%`, `%LOCALAPPDATA%` | 全局配置 |

❌ 不要在只搜了项目目录后就放弃。
✅ 搜索前先列出：项目目录 → workspace_root 的平级目录 → C盘常用路径。

## 2. 文件检查：只用 PowerShell，不用 cmd

```powershell
# ✅ 用这个
Test-Path "D:\Obsidian Vault\记录\"

# ❌ 不要用这个（无输出不代表不存在）
cmd /c "if exist D:\...\ (echo EXISTS)"
```

`cmd /c` 的 `if exist` 在路径含中文、空格、特殊字符时不可靠。

## 3. 同一错误只犯一次

PowerShell 5.x 不支持三元运算符 `? :`，一行语法报错后立即换写法：

```powershell
# ❌ PowerShell 5.x 不支持
$vis = $r.private ? "private" : "public"

# ✅ 用 if/else
$vis = if ($r.private) { "private" } else { "public" }
```

## 4. 不推断"不存在"

搜不到时报告"在以下 N 个路径中未找到"，不要断言"XX 不存在"。
除非已确认网络扫描了所有可能的路径。

## 5. 文件编码：写入必须显式无 BOM

```powershell
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($path, $content, $utf8)
```

❌ `Set-Content -Encoding UTF8` 会加 BOM
❌ `ConvertTo-Json` 可能破坏缩进（PowerShell 5.x）

## 6. Zip 打包：用 staging 目录

```powershell
# 1. Copy-Item 到临时目录（保持结构）
# 2. Compress-Archive -Path "$staging\*" -DestinationPath ...
# 3. Remove-Item staging
```

❌ 管道输入 `Compress-Archive` 会扁平化路径。
❌ .NET `ZipArchiveMode` 在 PowerShell 5.x 可能加载失败。

## 7. GitHub API：中文 JSON 用 -InFile

```powershell
# ✅ 先写 JSON 到文件，再用 -InFile
Invoke-RestMethod -Uri ... -InFile $bodyFile -ContentType "application/json; charset=utf-8"

# ❌ -Body 传入含中文的 JSON 字符串可能编码异常
```

## 8. Skill 存储在 D:\Codex\config\skills\

Skills 以**目录 + SKILL.md** 形式组织，不是 `.skill` 文件：
```
D:\Codex\config\skills\
├── .system\
├── task-board\
│   └── SKILL.md
├── work-daily-report\
│   └── SKILL.md
└── 同步保存\
    └── SKILL.md
```
