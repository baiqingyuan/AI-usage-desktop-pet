# AI 用量桌面宠物

一款 Windows 桌面宠物，以透明机器人挂件和薄荷色界面统一展示多个 AI 平台的 Token 用量、余额、额度及重置时间。

点击机器人展开或收起气泡，切换平台和今日／本月用量，点击“查看详情”进入主界面。支持置顶、拖动、任务栏与托盘唤回，并自动记住显示偏好。

## 下载与运行环境

Windows 10 / 11，Python 3.10 或更高版本（安装时保留 Tcl/Tk）。桌面程序只使用 Python 标准库，无需安装其他运行依赖。可使用 Git 克隆本仓库，或在 GitHub 选择 Code → Download ZIP 后解压；将整个目录放在你希望长期保留的位置。

## 启动

双击 **AIUsage.exe**。也可使用 **启动.vbs**；已经创建本机快捷方式的用户可以继续使用该快捷方式。程序自动寻找父目录的 Python 环境或已安装的 Python，运行无需安装第三方库。
也可运行 `python app.py`。已有实例时再次启动会唤回原挂件。程序在任务栏和通知区域使用专用机器人图标；托盘图标可能位于隐藏图标列表。没有自动开机启动。

挂件展开约 424 × 262 像素，收起后约 144 × 262 像素；背景透明。默认始终置顶，最小化再唤回后也会恢复置顶；设置里仍可取消。拖动机器人或气泡即可移动，位置自动保存。点击气泡右上角三个小圆点，或右键挂件，可查看用量、设置、刷新、收起 / 展开气泡、隐藏或退出。点击托盘图标唤回，右键托盘图标打开菜单。`Ctrl+D` 打开主界面；挂件中的 `Esc` 最小化到任务栏，主界面中的 `Esc` 返回挂件。

平台列表包含已启用的内置平台和手动平台。未连接的平台会明确显示未连接，可从主界面的“连接设置”完成配置；关闭所选平台后自动切换至可用平台。DeepSeek API 等只提供余额的平台会展示余额，隐藏无效的今日 / 本月按钮。所选时段没有 Token 数据时显示未提供，不会用另一个时段或余额冒充 Token。

永久固定任务栏：程序运行后右键任务栏上的机器人图标 → **固定到任务栏**。也可右键本目录的 **AI 用量挂件.lnk**，选择固定到任务栏。专用快捷方式绑定应用标识、启动程序和图标，固定后不会误启动空白 Python 窗口。仅创建快捷方式或显示运行图标并不等于永久固定。

## 数据来源和接入

| 平台 | 自动读取 | 接入条件 |
| --- | --- | --- |
| Codex | 多额度窗口、剩余百分比、重置时间、积分、重置券数量及最早有效期、账户累计 token、历史单日峰值、活跃天数、本机今日/月 token 和近 7 日趋势 | 本机 Codex 已登录 ChatGPT；原生 codex.exe |
| OpenAI API | 组织 completions 今日/月 token、缓存输入、请求数、组织本月费用 | OpenAI Admin Key |
| Claude API | 组织今日/月 token、缓存读取 | Claude 组织 Admin Key，个人账号不支持此组织接口 |
| DeepSeek API | 可用余额、充值余额、赠金余额 | DeepSeek API Key |
| 其他平台 | 手动 token、余额、额度百分比、重置日、到期日 | 设置 → 手动平台 |

在“设置”中连接平台。也支持 `OPENAI_ADMIN_KEY`、`ANTHROPIC_ADMIN_KEY`、`DEEPSEEK_API_KEY` 环境变量，环境变量优先于本地加密密钥。普通 OpenAI / Claude 模型 API Key 不等于组织 Admin Key。未配置平台显示未配置；不会显示示例用量。

到期日可为 `2026-11-08` 或 `2026-11-08T18:30`。只填日期表示北京时间当天 23:59:59。接口未提供订阅、赠金或充值到期日时可手动填写，界面明确标注“手动”。个人 ChatGPT、Claude、DeepSeek 网页聊天的完整用量不能由 API 用量代替。

## 统计口径

- Codex 账户累计 token 和日报来自官方账户接口，可能延迟。它与本机统计分别展示，不相加。没有将额度百分比换算成剩余 token。
- Codex 本机统计只涵盖当前 `CODEX_HOME`（默认 `~/.codex`）里的 sessions 和 archived_sessions 日志。按 token_count 的累计增量计算，消除重复通知和复制的分叉事件。以北京时间划分日/月。首次扫描需读本月有变动的日志，后续增量读取；缺失、被删除、损坏、截断或迁移的日志会使统计不完整。切换账号时本机记录可能包含原账号活动。
- Codex 总 token 为输入 + 输出；缓存输入是输入的子集，推理输出是输出的子集，不再重复加总。此数值不等于计费用量或额度消耗。
- OpenAI / Claude API 的日期按 UTC，默认整个组织。OpenAI token 图只覆盖 completions，组织费用则涵盖 costs 接口返回的 USD 费用，因此不能直接通过两者计算平均价格。Claude 总数包含未缓存输入、缓存创建、缓存读取和输出。
- DeepSeek 余额接口不提供历史 token 或有效期；个人 Claude / Claude Code 订阅额度目前采用手动入口。
- 不做跨平台 token 总和：Codex、API 和手动记录可能存在重叠。
- 自动查询失败时保留上次数据并显示“旧数据”和更新时间。提醒阈值默认为已用 80%，可调整；到期提醒显示在窗口内。已过重置时间的额度等待官方刷新。

## 项目文件与本地数据

源码、启动程序、图标及测试位于项目目录。程序首次使用时在同一目录生成个人配置与用量快照，移动项目目录时请一并保存这些本地文件。Windows 自己管理的任务栏 / 开始菜单快捷方式继续保留在系统位置；Python、Codex 为外部运行程序。

- 应用：app.py、pet_ui.py、core.py、soft_controls.py、ui_helpers.py、windows_integration.py。
- 启动入口：AIUsage.exe、启动.vbs；Launcher.cs 为启动程序源码。
- 图标：app.ico、app-icon.png；build_icon.py 为图标绘制工具。
- 检查：tests/；tools/render_previews.py 可从控件与矢量源码绘制布局预览。

settings.json 存储设置和 Windows DPAPI 加密密钥；只能在同一 Windows 用户上下文解密。snapshot.json 保存个人用量快照。配置、快照、日志、本机快捷方式、个人截图、布局预览及本地历史备份均由 .gitignore 排除，不会随源码提交。

程序不复制 Codex 登录令牌，也不保存或上传聊天内容。自动接入只执行只读账户查询，不请求模型生成、不使用重置券、不修改套餐或付费设置。

## 重建启动程序与图标

仓库内附可直接运行的 AIUsage.exe，也保留其 C# 源码。在 Windows PowerShell 中可以重建启动程序：

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /reference:System.Windows.Forms.dll /win32icon:app.ico /out:AIUsage.exe Launcher.cs
```

可选绘图工具 build_icon.py 和 tools/render_previews.py 需要 Pillow；桌面程序运行时不需要 Pillow。生成的布局预览可能含你当前的真实用量，默认不会提交至版本库。

## 检查

```text
python -m unittest discover -s tests -v
python app.py --check
python app.py --smoke-test
python app.py --offline
```

`--check` 会查询已启用平台并保存指标快照。测试使用本地假数据或网络模拟，不发起付费模型调用。

## 官方文档

- [Codex App Server](https://developers.openai.com/codex/app-server) — account/rateLimits/read 与 account/usage/read
- [OpenAI Usage API](https://platform.openai.com/docs/api-reference/usage)
- [Claude Usage and Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)
- [DeepSeek 查询余额](https://api-docs.deepseek.com/zh-cn/api/get-user-balance/)
