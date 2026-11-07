# astrbot_plugin_cost

AstrBot 的 **API 用量与费用** 插件。统计 AstrBot 侧每次模型调用的 token 用量，支持多平台、多模型查看，并按价目表估算花费，附带 WebUI 仪表盘。

## 支持范围

- **平台**：QQ（OneBot v11 / `aiocqhttp` / SnowLuma）、WebChat、Telegram、Discord、飞书、企业微信、KOOK 等 AstrBot 已接入平台；
- **模型**：DeepSeek、OpenAI、Claude、Gemini、Qwen 等；模型明细会列出所有出现过的 provider_model；
- 平台会话类型根据 UMO 自动识别为私聊、群聊或其他会话。

> 费用为本地估算。当前默认只对 DeepSeek 计价；其他模型只统计 tokens，费用显示为 `—`。可在价格弹窗中手动添加模型和标准价。

## 功能

- **QQ 命令**：`/用量 [天数]`（别名：`花费`、`费用`、`查费用`、`查用量`）
- **AI 工具**：`get_api_cost(days)`，机器人被问“今天花了多少钱”时可自己查询
- **WebUI 仪表盘**：
  - 对齐 AstrBot 原生统计页的浅色仪表盘：总览、模型、对话与最近调用在同一页面连续展示
  - 左侧全局筛选：按平台、按模型筛选后，各视图同步更新
  - 「模型价格参考」弹窗：展示 DeepSeek 参考价，可手动编辑并为未知模型添加标准价
  - 支持按日期查看单日用量，单日严格统计当天 00:00–24:00，不按滚动 24 小时计算
  - “今天”按钮从今天 00:00 起算
  - 请求次数、回复消息数、时段花费、平均每条回复、平均每次请求、缓存命中率
  - **平台筛选**与**私聊 / 群聊花费拆分**（环形图 + 明细）
  - **对话排行榜**：每个对话的调用次数、tokens、命中率、费用、每次均价、占比，可筛选平台/私聊/群聊
  - 每日花费趋势折线图
  - 高峰 / 空闲分布
  - 多模型明细、最近调用
  - 时间范围工具栏、移动端横向滚动表格与响应式布局
- 自动区分 DeepSeek **高峰 / 空闲时段** 价格
- 深浅色主题、动画与骨架屏

## 安装

把整个插件目录放到 AstrBot 的插件目录：

```
<AstrBot 数据目录>/plugins/astrbot_plugin_cost/
```

然后在 AstrBot WebUI 的「插件」页面启用即可（无额外 pip 依赖）。

## 使用

在 QQ 里：

```
/用量          # 今天 00:00 起
/用量 7        # 最近 7 个自然日（含今天）
```

WebUI：`插件` → `AstrBot 用量与费用` → 打开 `cost` 页面；左侧栏「模型价格参考」打开可编辑价格弹窗。  
页面右上角可按日期选择单日查看；单日范围是当天 `00:00–24:00`。

## 数据与价格

- 数据来源：AstrBot 本地数据库 `data_v4.db` 中的 `provider_stats` 表（仅统计 `status='completed'` 的调用）。
- 平台、私聊/群聊与对话标识来自 `provider_stats.umo`，回复数来自 `conversations` 表中的 assistant 消息。
- 价格写在 `main.py` 顶部的 `PRICES` 中，单位：**元 / 百万 tokens**。当前默认只使用 DeepSeek 的内置参考价；模型名按精确名和包含关系匹配。
- 只有 DeepSeek 默认显示参考价估算金额；其他模型只显示 tokens，金额显示 `—`，可在价格弹窗手动配置。
- 高峰时段：北京时间 **周一至周五 9:00–12:00、14:00–18:00**（不含法定节假日）；非 DeepSeek 模型通常使用固定参考价。
- DeepSeek 价格来自 [DeepSeek 官方价目表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)；手动添加的模型价格保存在本地，官方调价时请自行更新。

## 配置

数据库路径默认自动解析：

1. 环境变量 `ASTRBOT_DB_PATH`（优先级最高）
2. `<AstrBot 数据目录>/data_v4.db`（根据插件所在位置推断）
3. AstrBot 提供的 `get_astrbot_data_path()`

一般无需手动配置。

## 说明

- 统计与费用为**本地估算**，实际扣费以对应模型服务商账单为准；已知模型金额为参考价估算，未知模型只统计 tokens；
- 只统计 AstrBot 通过 provider 产生的调用，不包含其他程序使用同一 API Key 的消耗；
- 对话回复数来自会话历史（全量），费用/tokens 为所选时间范围内。

## License

MIT







