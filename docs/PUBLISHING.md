# 发布到 GitHub

## 仓库信息

- 建议仓库名：`yannian`
- 显示名称：**研念 · Yannian**
- 一句话介绍：**让阅读中的一念，成为研究的起点。**
- About 描述（可直接粘贴）：

> 研念 Yannian：开源本地论文研究工作台。集成多来源检索、PDF 文字/图表选区问答、网址下载归类、Zotero 导入与 idea 分析；默认连接本机 Codex，也支持 OpenAI API。

建议 Topics：

```text
yannian research-workspace paper-reading pdf-reader literature-review
research-assistant zotero codex fastapi local-first
```

## 上传源码

源码压缩包中的 `yannian/` 就是仓库根目录：打开后应直接看到 `README.md`、`app/`、`static/`、`tests/` 等。上传目录内的文件，不要只上传 ZIP，也不要把父目录和整个本地运行环境一起上传。

可以使用 GitHub Desktop：在解压后的 `yannian/` 创建本地仓库，检查变更列表后提交，再使用 Publish repository 选择仓库名和可见性。使用 Git 命令行也可以在这个目录初始化仓库，再按 GitHub 空仓库页面给出的步骤添加远程地址并推送。

仓库根目录已有 README、AGPL-3.0-only 许可证、第三方依赖说明与 `.gitignore`，创建远程空仓库时无需重复生成这些文件。

`.gitignore` 和 `.env.example` 等隐藏文件也应保留。研究资料默认位于 `data/`；`.env`、论文原文件、数据库、日志和 `.venv` 不属于源码包。README 已引用 `docs/screenshots/` 中的三张界面展示图，展示文献库、论文发现与 idea 笔记。图片以使用者提供的实际截图为基础，清理第三方悬浮窗并统一裁剪、留白与外框；上传时一并保留这个目录。

## v0.6.1 发布文案

**研念 · Yannian：从论文阅读到研究灵感的本地工作台。**

本版统一了项目名称与桌面入口，并整理了使用指南和源码发布流程。已有能力包括：

- 多来源论文检索、英文查询展开、来源进度和阅读初筛。
- PDF 原文连续阅读，文字拖选、图表与公式区域提问，回答关联原文位置。
- 粘贴公开论文网址，下载 PDF、保存并加入研究项目。
- 记录 idea，分析相似工作、代码与数据资源及验证实验。
- 项目归类、Zotero 本地导入，以及本地资料完整备份。
- 默认接入本机已登录的 Codex，可选 OpenAI Responses API。

当前为本地单人版本；尚无 OCR、跨设备同步及 Zotero 双向同步。检索初筛和 idea 分析需要研究者核实。验证范围见仓库中的 `VERIFICATION.md`。

## 重新打包

在仓库根目录执行：

```bash
python scripts/package_source.py
```

输出 `dist/yannian-source.zip`。打包器按源码目录和根文件白名单收集文件，排除本地数据、环境配置、运行环境与缓存，并检查 ZIP 完整性。修改版本后，请同步更新 `CHANGELOG.md`、验证记录和程序版本。

本页提供发布材料和操作说明；项目不会替你创建 GitHub 仓库或上传文件。
