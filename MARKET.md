# 市场调研：MCP 财经 Server（2026-09-29）

> 按 laogu-skill-maker 方法论第 6 节：供给 / 需求 / 槽点 / 差异化。

## 一、供给：现有 A 股相关 MCP Server

| 项目 | 数据源 | 工具数 | 上架情况 | 备注 |
|---|---|---|---|---|
| zwldarren/akshare-one-mcp | AKShare | 多 | Smithery 已上架（有 badge） | 最接近的竞品；中英双语 README |
| mcu-uav/akshare-one-mcp | 腾讯/新浪/东财/雪球（stdlib 直调） | 精简 | GitHub | 轻量，无 akshare/pandas 重依赖，树莓派可跑 |
| guangxiangdebizi/FinanceMCP | Tushare Pro + Binance | 19 | GitHub | 覆盖 A/H/美/基金/宏观；**需要 Tushare Pro token** |
| aahl/mcp-aktools | AKShare | 多 | GitHub | AKShare 生态封装 |
| xyonium/finance-mcp | 东财 push2（keyless） | quote/history/search | GitHub | 无 key 直调东财，2026-09 新项目 |
| chengzuopeng/stock-sdk | 浏览器抓取 | SDK+MCP | GitHub | TS 零依赖 SDK |

美股侧供给极多（Alpha Vantage、Alpaca、Polygon、FMP、TradingView、Yahoo），但 **A 股中文场景是供给洼地**：只有上面 5-6 个，且全部是"原始数据搬运"形态。

## 二、需求：用户原话证据

- 中老年股民要的是"打开就能用"：早报推送、解禁提醒、"这只股能不能碰"——场景化需求，不是"我要一个行情 API"。
- 开发者/Claude/Cherry Studio 用户：想要 keyless（无 token） 的 A 股数据源，Tushare 的付费墙是最大劝退。
- 搜索联想证据：awesome-ai-in-finance 等清单 2025-2026 大幅扩充 finance MCP 章节，生态在快速长大。

## 三、槽点：现有方案被吐槽最多的 4 点

1. **要 token / 收费墙**：FinanceMCP 依赖 Tushare Pro，免费额度不够用；散户不会配 key。
2. **英文文档、没中文场景**：akshare-one-mcp 有中文 README 但 tool 全是英文原始数据口径。
3. **只给数字不给解读框架**：拿到 K 线/财报数字后，LLM 不知道按什么逻辑解读，容易编造。
4. **重依赖、安装慢**：AKShare 全家桶（pandas/numpy/lxml）在小白机器上装半天；mcu-uav 的轻量路线验证了"stdlib 直调"受欢迎。

## 四、差异化：我们凭什么赢

1. **16 个场景化 tool，不是 16 个数据接口**：每个 tool 对应一个散户真实场景（早报/龙虎榜/解禁/估值锚…），tool 描述里直接写 Output Contract，宿主 LLM 按契约解读——竞品只给 raw data，我们给"数据 + 解读框架"。
2. **零 key、零登录**：全部走公开接口（新浪/东财公告/东财 datacenter），`uvx` 一行装好，没有任何 token 要配——对标槽点 1。
3. **中文优先**：tool 名、描述、返回字段、README 全中文；错误信息也是中文（"未核验"而不是 "N/A"）。
4. **诚实降级链**：每个 tool 内置 主力→备选→诚实失败（三级），失败时返回搜索模板而不是编数字；这是 16 个 skill 冒烟沉淀下来的方法论，竞品没有。
5. **与 skill 矩阵同源**：tool 描述 = skill 的 Output Contract，MCP 与 SkillHub/Coze 共用一套内容资产，维护一次多处复用。

## 五、发布渠道结论

| 渠道 | 方式 | 费用 | 审核 | 优先级 |
|---|---|---|---|---|
| Smithery | GitHub 登录 → /new 选仓库 → smithery.yaml 自动扫描 | 免费 | 发布即上架（近实时），verified 徽章需单独申请 | P0（流量最大，一键安装） |
| 官方 MCP Registry | mcp-publisher CLI，命名空间 io.github.<owner>/<repo> | 免费 | schema/命名空间校验 | P0（PulseMCP/Glama 自动抓取的上游） |
| mcp.so | 社区免费路线：chatmcp/mcpso Issue #1 评论提交 | 免费 | 维护者手动添加 | P1 |
| PulseMCP | 官方 Registry 发布后约 1 周自动同步；或 pulsemcp.com/submit 表单 | 免费 | 自动/1-2周 | P1（等 Registry） |
| Glama.ai | 加 GitHub topics（mcp, model-context-protocol, mcp-server）+ 网站提交 | 免费 | 24h 内，自动 A-F 评分 | P1 |

mcp.so 的 $39 付费 web 表单只针对 Remote Server（托管 endpoint），我们是 stdio 本地 server，走免费社区路线即可。
