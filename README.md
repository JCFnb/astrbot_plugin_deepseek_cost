# astrbot_plugin_deepseek_cost

AstrBot 的 **DeepSeek API 用量与费用** 插件（只针对 DeepSeek）：统计 AstrBot 侧每次模型调用的 token 用量，按 DeepSeek 官方价目表估算花费，并提供一个 WebUI 仪表盘页面。

## 功能

- **QQ 命令**：`/用量 [天数]`（别名：`花费`、`费用`、`查费用`、`查用量`）
- **AI 工具**：`get_api_cost(days)`，机器人可以在被问“今天花了多少钱”时自己查询
- **WebUI 页面**：请求次数、回复消息数、时段花费、平均每条回复花费、平均每次请求、缓存命中率、每日趋势图、高峰/空闲分布、模型明细、最近调用
- 自动区分 DeepSeek **高峰 / 空闲时段** 价格
- 支持深浅色主题，页面动效与骨架屏加载

## 安装

把整个插件目录放到 AstrBot 的插件目录：

```
<AstrBot 数据目录>/plugins/astrbot_plugin_cost/
```

然后在 AstrBot WebUI 的「插件」页面启用即可（无需额外 pip 依赖）。

## 使用

在 QQ 里：

```
/用量          # 今天
/用量 7        # 最近 7 天
```

WebUI：`插件` → `API 用量与费用` → 打开 `cost` 页面。

## 数据与价格

- 数据来源：AstrBot 本地数据库 `data_v4.db` 中的 `provider_stats` 表（仅统计 `status='completed'` 的调用）。
- 价格写在 `main.py` 顶部的 `PRICES` 中，单位：**元 / 百万 tokens**：

| 模型 | 缓存命中（高峰/空闲） | 缓存未命中（高峰/空闲） | 输出（高峰/空闲） |
|---|---|---|---|
| deepseek-flash | 0.04 / 0.02 | 2.0 / 1.0 | 8.0 / 4.0 |
| deepseek-v4-pro | 0.30 / 0.15 | 9.0 / 4.5 | 27.0 / 13.5 |

- 高峰时段：北京时间 **周一至周五 9:00–12:00、14:00–18:00**（不含法定节假日）；其余按空闲价。
- 价格来自 [DeepSeek 官方价目表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，官方调价时请同步修改 `PRICES`。

## 配置

数据库路径默认自动解析：

1. 环境变量 `ASTRBOT_DB_PATH`（优先级最高）
2. `<AstrBot 数据目录>/data_v4.db`（根据插件所在位置推断）
3. AstrBot 提供的 `get_astrbot_data_path()`

一般无需手动配置。

## 说明

- 统计与费用为**本地估算**，实际扣费以 DeepSeek 控制台账单为准；
- 只统计 AstrBot 通过 provider 产生的调用，不包含其他程序使用同一 API Key 的消耗。

## License

MIT
