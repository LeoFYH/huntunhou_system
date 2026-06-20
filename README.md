# 馄饨侯订单工具

一个本地 Web 工具，用于把多门店订单、排产、材料出库、产成品入库和发货拆分成独立模块。

## 当前范围

- 模块 1：订单 Excel + 文字加单确认后生成排产表。
- 模块 2：排产表 + 配方表 + 单位换算表生成材料出库单；理论库存数低于安全库存数 50% 才触发领料；缺配方或换算表时只提示缺配置。
- 模块 3：产成品入库单模板已接入；拍照识别接口预留，当前支持手动确认行生成。
- 模块 4：原始订单 + 文字发货确认后按门店导出发货单。

数字汇总、理论库存、50% 筛选、取整和 Excel 写入都由代码完成；AI 只用于文字到正式名称的候选匹配，且必须人工确认后才参与生成。

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
```

未配置 key 时，页面会使用本地弱解析并提示人工核对。

## 数据保存

上传文件、自动保存文本和导出文件默认存到 `storage/`，该目录不会提交到 public 仓库。

## 原始资料

- [Codex开发需求.md](docs/Codex开发需求.md)
- [UI设计.html](docs/UI设计.html)
