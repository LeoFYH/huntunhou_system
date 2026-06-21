# 馄饨侯订单工具

一个本地 Web 工具，用于把多门店订单、排产、材料出库、产成品入库和发货拆分成独立模块。

## 当前范围

- 模块 1：从订单库 AI 同步，确认下单日期批次后先生成待补充排产表；用户手填盘点库存和入库数后再上传，工具回填理论库存数生成完整排产表。
- 模块 2：本次上传完整排产表 + 常驻投料配方表/单位转换表/所属库表生成领料单；理论库存数低于安全库存数 50% 才触发领料。
- 投料单上传后会显示识别预览，便于确认识别到的成品和配料行数量。
- 模块 3：从机器人产成品库存数据同步，按日期拉车间入库批次；数据不带门店，确认后生成产成品入库单。
- 模块 4：从订单库同步发货数据，确认后按门店导出发货单。

数字汇总、理论库存、50% 筛选、取整和 Excel 写入都由代码完成；AI 同步结果必须人工确认后才参与生成。

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn backend.main:app --reload
```

打开：<http://127.0.0.1:8000>

## DeepSeek

在 `.env` 或系统环境变量里配置：

```env
DEEPSEEK_API_KEY=你的 key
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
ROBOT_API_BASE=http://127.0.0.1:9000
ROBOT_API_TOKEN=机器人共享 token
```

未配置 key 时，页面会使用本地弱解析并提示人工核对。

## 订单库同步

机器人侧暴露：

- `GET {ROBOT_API_BASE}/api/orders?status=new`
- `POST {ROBOT_API_BASE}/api/orders/mark_fetched`，body 为 `{"ids":[...]}`
- `POST {ROBOT_API_BASE}/api/orders/unmark`，body 为 `{"ids":[...]}`，把已拉取订单退回为未拉取

配置 `ROBOT_API_TOKEN` 后，请求会带 `Authorization: Bearer <ROBOT_API_TOKEN>`。

页面模块 1 的“同步订单库”只拉取已识别、已核验订单，展示按下单日期拆开的批次；用户确认并生成排产表成功后，后端再调用 `mark_fetched`，避免生成失败时误标记。
生成待补充排产表后，页面会显示“作废本批 · 退回订单”按钮；退回会调用 `unmark` 解锁本批已成功标记的订单，之后用户手动重新同步，可把期间新增的加货一起纳入最新一批。

订单库同步规则：

- Web 工具按机器人返回的 `order_date`（下单日期）归批，不用消息 `created_at`，也不用 `deliver_date` 分批。
- 一次拉取混有多个 `order_date` 时，前端会按下单日期拆成多个批次，用户选择其中一个批次生成排产表。
- 排产表文件名和表内日期都使用 `order_date`，例如 `排产表_2026-06-21.xlsx`。
- `deliver_date` 只作为可选备注，不参与归批和排产日期。
- `patch` 加货必须能挂到同下单日期、同门店主订单。
- 找不到同门店主订单的 `patch` 会被拒绝并在前端列出门店和加货内容，不进入汇总。
- `mark_fetched` 若返回 `{succeeded:[...], failed:[...]}`，Excel 不受影响；失败 id 会本地记录，前端可点按钮重试标记。
- `unmark` 建议同样返回 `{succeeded:[...], failed:[...]}` 并保持幂等；已经是 `new` 的 id 再退回也算 `succeeded`，只有不存在的 id 算 `failed`。

## 数据保存

常驻模板和导出文件默认存到 `storage/`，该目录不会提交到 public 仓库；模块 2 的完整排产表随生成请求一次性上传，不长期保存。

## 原始资料

- [Codex开发需求.md](docs/Codex开发需求.md)
- [UI设计.html](docs/UI设计.html)
