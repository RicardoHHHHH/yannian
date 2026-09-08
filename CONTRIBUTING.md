# 参与研念开发

先按 [README](README.md#快速开始) 创建 Python 3.11+ 环境并安装依赖。开发测试另需 Node.js 22+，不需要 npm install。

## 运行与测试

下面以 Windows 为例；macOS / Linux 将 Python 路径替换为 `.venv/bin/python`。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run.py --no-browser
```

在另一个终端运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
node --check static/app.js
node tests/test_frontend.cjs
node tests/test_pdf_geometry.mjs
node tests/test_pdf_layout.mjs
node tests/test_pdf_reader.mjs
```

后端测试使用临时数据库、合成 PDF 与模拟的模型 / Zotero / 网络响应，不读取个人文献库，不消耗真实模型额度。前端检查使用 DOM 替身，不能替代浏览器中的实际滚动、选择、缩放和视觉验收。

## 提交修改

问题报告请写明操作步骤、预期行为、实际结果，以及系统、浏览器和 Python / Codex 版本。复现 PDF 尽量使用公开论文或最小合成文件；日志中先移除密钥和个人资料。

提交 PR 时说明解决的问题、最终行为和已完成的验证。数据结构变更需要兼顾现有 `data/`；界面修改应在实际浏览器中检查阅读、选区和两侧栏交互。新增依赖请同步更新版本记录和 `THIRD_PARTY.md`。

不要提交 `.env`、`data/`、个人 PDF、模型凭据、日志或运行环境。项目采用 [AGPL-3.0-only](LICENSE)，第三方源码及许可证需完整保留。
