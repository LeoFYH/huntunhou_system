# Codex 本轮工作总结

更新时间：2026-06-21  
仓库：`D:\huntunhou_system`  
GitHub：`https://github.com/LeoFYH/huntunhou_system`  
当前提交：`213cd6f Harden robot order sync flow`

## 一、项目当前状态

本工具已经是一个本地 Web 应用：

- 后端：FastAPI
- 前端：静态 HTML/CSS/JS
- 本地数据：`storage/`，已被 `.gitignore` 排除，不会提交真实 Excel/业务数据
- 启动地址：`http://127.0.0.1:8000`

当前已实现四个模块：

1. 订单 Excel / 文字加单 / 订单库同步 -> 排产表
2. 排产表 + 投料单/配方表 + 换算表 -> 材料出库单
3. 产成品入库单，当前支持手动确认行生成，拍照识别接口已预留
4. 原始订单 / 文字发货 -> 按门店拆分发货单

核心原则已落实：

- AI 只做模糊匹配和文字识别候选。
- 数字汇总、理论库存、50% 触发、向上取整、Excel 写入全部由代码完成。
- AI 结果必须人工确认后才参与生成。

## 二、本轮重点：机器人订单库同步

用户要求机器人侧暴露 GET JSON，Web 工具需要拉取订单库数据并套成现有格式。

已实现：

- 前端模块 1 增加“从订单库同步”按钮。
- 后端新增机器人订单拉取服务。
- 拉取后按门店聚合展示“本批全貌”。
- 用户点击“确认并入本批”后，机器人订单才并入模块 1 的待生成数据。
- 排产表生成成功后，才调用机器人 `mark_fetched`。
- 排产生成失败时，不会误标记机器人订单。

机器人侧需要提供：

```http
GET {ROBOT_API_BASE}/api/orders?status=new
```

工具侧生成成功后调用：

```http
POST {ROBOT_API_BASE}/api/orders/mark_fetched
Content-Type: application/json
Authorization: Bearer <ROBOT_API_TOKEN>

{ "ids": [123, 456] }
```

## 三、鉴权

已新增配置：

```env
ROBOT_API_BASE=
ROBOT_API_TIMEOUT_SECONDS=20
ROBOT_API_TOKEN=
```

只要配置了 `ROBOT_API_TOKEN`，工具请求机器人接口时都会带：

```http
Authorization: Bearer <ROBOT_API_TOKEN>
```

涉及接口：

- `GET /api/orders`
- `POST /api/orders/mark_fetched`

## 四、patch 加货处理规则

本轮把原来的 warning 改成了严格拒绝。

规则：

- `kind=base` 是主订单。
- `kind=patch` 是加货。
- 加货必须能找到同门店主订单。
- 同门店主订单来源可以是：
  - 机器人本次返回的 `kind=base`
  - 用户已经在模块 1 上传的主订单 Excel

如果某门店只有 patch，没有同门店 base，也没有本地已上传的该门店主订单：

- 该 patch 不并入汇总。
- 该 patch 不进入 accepted ids。
- 后续不会对它调用 `mark_fetched`。
- 前端会明确列出门店和加货内容。

前端提示格式：

```text
以下加货找不到对应门店的主订单，请先上传这些门店的主订单：
· 老三家：鸡腿 20件
· 振兴学校：豆浆 10箱
```

## 五、deliver_date 日期规则

已改为使用机器人返回的 `deliver_date` 判断排产/同步批次，不使用 `created_at`。

规则：

- 单一 `deliver_date`：前端允许“确认并入本批”，生成排产表时后端使用该日期作为到货日期。
- 多个 `deliver_date`：前端阻止“确认并入本批”，要求按到货日期分批同步。

排产表日期逻辑：

- `delivery_date = deliver_date`
- 模板里的明天/到货日期单元格写入 `delivery_date`
- 模板里的当天/制单相关日期写入 `delivery_date - 1 天`
- 输出文件名使用 `排产表_{delivery_date}.xlsx`

发货生成函数也已预留 `target_date` 参数，后续如果机器人发货流也传 `deliver_date`，可以直接沿用。

## 六、mark_fetched 部分失败处理

机器人现在可以返回：

```json
{
  "succeeded": [123],
  "failed": [456]
}
```

工具侧处理方式：

- Excel 已生成结果不回滚。
- `succeeded` 从本地失败记录中清掉。
- `failed` 记录到本地 `settings.robot_mark_failures`。
- 前端显示失败 id。
- 前端提供“重试标记”按钮。

新增本地重试接口：

```http
POST /api/robot/orders/retry-mark
Content-Type: application/json

{ "ids": [456] }
```

如果不传 ids，后端会尝试重试本地记录里的全部失败 id。

## 七、已更新的主要文件

- `backend/config.py`
  - 新增 `ROBOT_API_TOKEN`

- `backend/services/robot_service.py`
  - 机器人 GET/POST 请求带 Bearer token
  - 标准化机器人订单 JSON
  - 拒绝无同门店主订单的 patch
  - 返回 `rejected_patches`
  - 返回 `deliver_dates` / `target_deliver_date` / `blocking_reasons`
  - 兼容 `mark_fetched` 的 `{succeeded, failed}` 返回

- `backend/main.py`
  - `/api/robot/orders/fetch`
  - `/api/robot/orders/retry-mark`
  - 排产生成接收 `target_date`
  - 生成成功后 mark_fetched，失败不影响 Excel

- `backend/storage.py`
  - 本地记录 mark_fetched failed ids
  - 成功后清理 failed ids

- `backend/services/excel_service.py`
  - 排产表按 `target_date/deliver_date` 写日期和文件名
  - 发货生成函数预留 `target_date`

- `web/app.js`
  - 前端订单库同步展示
  - 无主订单 patch 明确列出
  - 多 deliver_date 阻止确认并入
  - mark_fetched 失败 id 可重试
  - 页面加载时显示历史失败 id 重试入口

- `tests/test_robot_service.py`
  - 覆盖 patch 拒绝
  - 覆盖本地已上传主订单门店可挂 patch
  - 覆盖多 deliver_date 阻断
  - 覆盖 Bearer token header

- `README.md`
  - 更新机器人接口、token、patch、deliver_date、partial failure 规则

- `docs/Claude_订单库对接进展.md`
  - 更新给 Claude/机器人侧的对接说明

## 八、已验证

已运行：

```powershell
.\.venv\Scripts\python.exe -m compileall backend
.\.venv\Scripts\python.exe -m pytest -q
node --check web\app.js
```

结果：

```text
7 passed
```

接口烟测：

```text
GET /api/health -> {"status":"ok"}
POST /api/robot/orders/retry-mark {"ids":[]} -> skipped
```

## 九、Claude/机器人侧接下来要确认

1. `GET /api/orders?status=new` 最终 JSON schema 是否固定。
2. `kind=base` / `kind=patch` 是否稳定提供。
3. `store` 是否是 patch 挂靠主订单的唯一依据。
4. `deliver_date` 是否稳定为 `YYYY-MM-DD`。
5. 机器人侧是否按 `deliver_date` 分批返回，避免一次返回多个到货日期。
6. `POST /api/orders/mark_fetched` 是否幂等，方便失败 id 重试。
7. `ROBOT_API_TOKEN` 是否已和 Web 工具配置为同一个值。

