# 馄饨侯订单工具 - 给 Claude 的当前进展说明

## 当前仓库

- GitHub: https://github.com/LeoFYH/huntunhou_system
- 最新相关提交: 以 `git log --oneline -1` 为准
- 本地项目目录: `D:\huntunhou_system`
- 本地服务地址: `http://127.0.0.1:8000`

## B 轮已落实的机器人对接要求

1. 鉴权：`ROBOT_API_TOKEN` 已接入。工具请求机器人接口时会带：

```http
Authorization: Bearer <ROBOT_API_TOKEN>
```

2. `patch` 加货拒绝规则：某门店有 `patch` 但找不到同门店 `base` 主订单时，该 patch 不并入汇总，也不进入待 `mark_fetched` 的 accepted ids。前端会逐条列出门店和加货内容，例如：

```text
以下加货找不到对应门店的主订单，请先上传这些门店的主订单：
· 老三家：鸡腿 20件
· 振兴学校：豆浆 10箱
```

同门店主订单来源有两个：机器人本次返回的 `kind=base`，或用户已经在模块 1 上传的主订单 Excel。

3. 日期：排产/发货/同步批次按 `deliver_date` 判断，不用 `created_at`。如果本批机器人返回多个 `deliver_date`，前端会阻止“确认并入本批”，需要机器人侧按到货日期分批返回或用户分批同步。

4. `mark_fetched` partial failure：机器人返回 `{succeeded:[...], failed:[...]}` 时，Excel 已生成结果不回滚；工具会清掉 succeeded、本地记录 failed、前端提示失败 id，并提供重试按钮。重试接口是：

```http
POST /api/robot/orders/retry-mark
Content-Type: application/json

{ "ids": [123, 456] }
```

## 已完成的基础能力

当前工具是 FastAPI + 静态前端，已经有四个模块：

1. 订单汇总 -> 排产表
2. 排产表 -> 材料出库单
3. 产成品入库单
4. 发货单

关键原则已经落实：

- AI 只做文字/名称模糊匹配。
- 数字计算全部由代码做。
- AI 结果必须人工确认后才进入最终 Excel。
- 上传文件、文本输入、导出文件存在本地 `storage/`，不提交 public 仓库。

## 已支持的 Excel/投料单格式

已接入：

- 订单/发货单模板
- 排产单模板
- 材料出库单模板
- 产成品入库单模板
- 投料单/配方表

投料单支持多文件上传。解析方式不是看颜色/高亮/样式，而是看表头和内容：

- 每个 sheet 按一个成品处理。
- sheet 名或标题里的 `XX投料单` 会识别成成品名。
- 能识别 `原料名称`、`单品净重 g`、`得率`、`生产个数（订单量+保存样）` 等字段。
- 面皮、馅料等分段会合并成同一个成品的配料。
- 半成品如果在投料单中出现，也直接当作配料，不递归拆。

用用户给的 3 份真实投料单测过：

- 3 个文件
- 23 个成品
- 244 条配料
- 未识别 sheet 为 0

## 当前模块 2 业务逻辑

理论库存数公式：

```text
理论库存数 = 盘点库存数 + 入库数 - 出库数
```

触发领料条件：

```text
理论库存数 < 安全库存数 * 50%
```

注意是严格低于 50%，等于 50% 不触发。

排产表里的理论排产量：

```text
理论排产量 = 该产品安全库存数
```

## 本轮新增：机器人订单库 Fetch

用户的新需求是：AI 机器人/微信侧会暴露 HTTP GET JSON 数据，本工具需要拉下来，套成现有格式，前端加按钮同步订单库。

已完成：

### 后端配置

`.env.example` 新增：

```env
ROBOT_API_BASE=
ROBOT_API_TIMEOUT_SECONDS=20
ROBOT_API_TOKEN=
```

实际联调时设置：

```env
ROBOT_API_BASE=http://机器人地址
ROBOT_API_TOKEN=和机器人一致的共享 token
```

### 新增后端文件

`backend/services/robot_service.py`

负责：

- 调机器人接口
- 标准化订单 JSON
- 按门店聚合展示
- 生成模块 1 可直接使用的 `confirmed_items`
- 生成待回调的机器人订单 `ids`
- `patch` 找不到同门店 `base` 或本地已上传主订单门店时直接拒绝，并返回 `rejected_patches`
- 收集 `deliver_date`，单日期时返回 `target_deliver_date`，多日期时返回 `blocking_reasons`

### 新增后端接口

```http
GET /api/robot/orders/fetch
```

内部调用：

```http
GET {ROBOT_API_BASE}/api/orders?status=new
```

机器人返回格式按文档约定：

```json
{
  "orders": [
    {
      "id": 123,
      "kind": "base",
      "source": "excel|photo",
      "store": "鼓楼店",
      "order_no": "...",
      "items": [
        {
          "code": "05020094",
          "name": "鸡汤虾肉馄饨",
          "spec": "500g/袋*12袋",
          "unit": "箱",
          "qty": 1,
          "price": 399.11,
          "category": "馄饨"
        }
      ]
    }
  ]
}
```

本工具会返回前端：

```json
{
  "ids": [123, 456],
  "items": [
    {
      "store": "鼓楼店",
      "product": "鸡汤虾肉馄饨",
      "name": "鸡汤虾肉馄饨",
      "quantity": 1,
      "qty": 1,
      "unit": "箱",
      "code": "05020094",
      "spec": "500g/袋*12袋",
      "price": 399.11,
      "category": "馄饨",
      "source": "robot",
      "robot_order_id": 123,
      "order_kind": "base"
    }
  ],
  "grouped": [
    {
      "store": "鼓楼店",
      "orders": [{ "id": 123, "kind": "base" }],
      "items": [{ "name": "鸡汤虾肉馄饨", "quantity": 1, "unit": "箱" }]
    }
  ],
  "warnings": [],
  "counts": {
    "orders": 1,
    "items": 1,
    "stores": 1,
    "base": 1,
    "patch": 0,
    "other": 0
  }
}
```

### 生成排产表后的回调

模块 1 的生成接口仍是：

```http
POST /api/generate/production
```

现在额外接受：

```json
{
  "confirmed_items": [],
  "robot_order_ids": [123, 456]
}
```

只有排产表成功生成后，后端才调用：

```http
POST {ROBOT_API_BASE}/api/orders/mark_fetched
Content-Type: application/json
Authorization: Bearer <ROBOT_API_TOKEN>

{ "ids": [123, 456] }
```

如果排产生成失败，不会 mark fetched。

如果排产生成成功但 mark_fetched 全部或部分失败：

- Excel 仍然生成。
- 前端 warnings 会提示 mark_fetched 失败。
- `succeeded` 会从本地失败记录里清掉。
- `failed` 会记录到本地 `settings.robot_mark_failures`。
- 前端会给失败 id 一个“重试标记”按钮，调用 `POST /api/robot/orders/retry-mark`。

## 前端已加功能

模块 1 新增按钮：

```text
从订单库同步
```

流程：

1. 用户点击“从订单库同步”。
2. 前端调 `GET /api/robot/orders/fetch`。
3. 页面展示“订单库本批全貌”：
   - 订单数
   - 门店数
   - 行数
   - 按门店分组的商品数量
4. 用户点“确认并入本批”。
5. 数据并入模块 1 的待生成数据。
6. 用户点“生成排产表”。
7. 生成成功后才 mark_fetched。

## 已有测试

当前测试通过：

```text
4 passed
```

新增测试文件：

- `tests/test_robot_service.py`

覆盖点：

- base/patch 订单标准化
- 按门店分组
- code 为空或 `#N/A` 时按 name 对齐
- patch 找不到同门店主订单时进入 `rejected_patches`，不进入汇总
- 本地已上传主订单门店可让同门店 patch 挂靠
- 多个 `deliver_date` 会产生阻断原因
- 机器人请求会带 Bearer token

## 需要 Claude/机器人侧确认或配合

1. `GET /api/orders?status=new` 是否稳定返回上述 JSON。
2. `items[].qty` 是否一定是数字或可转数字字符串。
3. `items[].code` 缺失时是否为 `null`、空字符串、`#N/A` 之一。
4. `kind=patch` 是否一定有 `store`。
5. `POST /api/orders/mark_fetched` 建议保持幂等，方便工具侧重试失败 id。
6. `mark_fetched` partial failure 格式已按 `{succeeded:[...], failed:[...]}` 接入。
7. 机器人接口鉴权已按 Bearer token 接入。

## 当前未做

- 没有直接连接机器人数据库。
- 没有在本工具里重新做微信订单识别。
- 没有改模块 2/3/4 核心逻辑。
- 没有把订单库数据永久存入本工具数据库；当前是拉取后在前端确认并入，然后生成排产表。

## 给 Claude 的重点问题

请帮忙确认机器人侧接口细节：

1. 最终 JSON schema 是否和上面一致。
2. `base` / `patch` 的挂靠是否只按 `store` 就够。
3. mark_fetched 是否需要批次号或只传 ids。
4. `ROBOT_API_TOKEN` 是否已经和机器人侧使用同一个值。
5. 如果要避免重复生成，机器人侧是否保证 `status=new` 不返回已 fetched 的订单。
6. 后续是否需要本工具保存一份 fetch 批次日志。
