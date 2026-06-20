from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

import httpx

from ..config import DEEPSEEK_API_BASE, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .excel_service import normalize_key, to_number


NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _best_match(text: str, candidates: list[dict[str, str]]) -> dict[str, Any] | None:
    if not text or not candidates:
        return None
    key = normalize_key(text)
    best = None
    best_score = 0.0
    for candidate in candidates:
        name = candidate["name"]
        candidate_key = normalize_key(name)
        if candidate_key and candidate_key in key:
            score = 1.0
        elif key and key in candidate_key:
            score = 0.9
        else:
            score = SequenceMatcher(None, key, candidate_key).ratio()
        if score > best_score:
            best = candidate
            best_score = score
    if not best:
        return None
    return {**best, "confidence": round(best_score, 3)}


def _local_parse(text: str, products: list[dict[str, str]], stores: list[dict[str, str]], include_store: bool) -> dict[str, Any]:
    items = []
    for raw_line in re.split(r"[\n;；]+", text):
        line = raw_line.strip()
        if not line:
            continue
        match = NUMBER_RE.search(line)
        quantity = float(match.group(1)) if match else None
        if quantity is None:
            continue
        before_number = line[: match.start()]
        after_number = line[match.end() :]
        unit_match = re.match(r"\s*([\u4e00-\u9fffA-Za-z]+)", after_number)
        unit = unit_match.group(1) if unit_match else ""
        store_match = _best_match(line, stores) if include_store else None
        product_text = before_number
        if store_match:
            product_text = product_text.replace(store_match["name"], "")
        product_match = _best_match(product_text or line, products)
        item: dict[str, Any] = {
            "product": product_match["name"] if product_match else product_text.strip(),
            "quantity": quantity,
            "unit": unit,
            "type": "加货" if any(word in line for word in ("加", "再", "补")) else "新增",
            "confidence": product_match["confidence"] if product_match else 0.35,
            "raw_text": line,
        }
        if include_store:
            item["store"] = store_match["name"] if store_match else ""
            item["store_confidence"] = store_match["confidence"] if store_match else 0.0
        items.append(item)
    return {
        "provider": "local_fallback",
        "needs_api_key": True,
        "items": items,
        "message": "未配置 DEEPSEEK_API_KEY，已使用本地弱解析，确认前请人工核对。",
    }


def _system_prompt(include_store: bool) -> str:
    target = "正式门店名 + 正式商品名 + 数量" if include_store else "正式商品名 + 数量 + 新增/加货类型"
    return (
        "你是餐饮订货工具里的文字结构化助手。"
        f"任务：把用户随口输入的中文文本解析成{target}。"
        "只做模糊名称匹配和结构化，不要做汇总、加总、库存、金额或任何业务计算。"
        "数量按原文提取为数字，单位按原文提取。"
        "只能返回 JSON，格式为 {\"items\": [...]}。"
        "每个 item 至少包含 product、quantity、unit、confidence、raw_text；"
        "如果是门店发货，还要包含 store、store_confidence。"
    )


async def parse_text_with_deepseek(
    text: str,
    products: list[dict[str, str]],
    stores: list[dict[str, str]] | None = None,
    include_store: bool = False,
) -> dict[str, Any]:
    stores = stores or []
    if not text.strip():
        return {"provider": "none", "items": []}
    if not DEEPSEEK_API_KEY:
        return _local_parse(text, products, stores, include_store)

    catalog_text = {
        "products": [item["name"] for item in products[:300]],
        "stores": [item["name"] for item in stores[:100]],
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt(include_store)},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "text": text,
                        "available_names": catalog_text,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            f"{DEEPSEEK_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"items": [], "raw_response": content}
    parsed["provider"] = "deepseek"
    parsed["model"] = DEEPSEEK_MODEL
    return parsed

