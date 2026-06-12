# 贡献指南 | Contributing Guide

感谢您对 MediaExportTool 的关注！欢迎各种形式的贡献。

## 如何贡献 | How to Contribute

### 🐛 报告 Bug
1. 在 [Issues](https://github.com/your-username/MediaExportTool/issues) 中搜索是否已有相同问题
2. 如果没有，创建新 Issue，请包含：
   - 操作系统版本
   - Python 版本
   - 完整的错误日志
   - 复现步骤

### 💡 提出功能建议
1. 在 Issues 中描述你的需求
2. 说明该功能解决什么问题
3. 如果可能，附上实现思路

### 🔧 提交代码
1. Fork 本仓库
2. 创建特性分支: git checkout -b feature/your-feature-name
3. 提交改动: git commit -m 'feat: add some feature'
4. 推送到分支: git push origin feature/your-feature-name
5. 创建 Pull Request

## 开发规范 | Development Guidelines

### 代码风格
- Python 代码遵循 PEP 8
- 使用中文注释（考虑到主要用户群体）
- 所有函数/类要有 docstring

### 提交信息格式
采用 [Conventional Commits](https://www.conventionalcommits.org/)：

\\\
feat: 新功能
fix: Bug 修复
docs: 文档更新
style: 代码格式调整（不影响功能）
refactor: 代码重构
perf: 性能优化
test: 测试相关
chore: 构建/工具链相关
\\\

### 测试
- 新增功能建议附带简单测试
- 提交前确保现有功能正常运行

## 项目结构

\\\
scripts/
├── douyin_export_all.py    # 抖音导出（Playwright 自动化）
├── xhs_export_all.py       # 小红书导出
├── wechat_export_all.py    # 视频号导出主脚本
└── wechat/                 # 视频号子导出模块
    ├── run_video_all_export.py
    ├── run_single_video_export_fresh.py
    ├── run_follower_export.py
    ├── run_source_dist_export.py
    └── run_fans_portrait_export.py
\\\

---

再次感谢您的贡献！🎉
