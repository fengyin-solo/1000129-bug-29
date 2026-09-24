"""入库管理业务规则：状态流转、字段校验与筛选口径都收在这里。

收货、上架、退回三个动作的写回口径：
- 确认收货：仅「待收货」可执行，把到货温度、收货人、收货时间写回入库单。
- 安排上架：仅「已收货」可执行，幂等地写入一条库存批次，重复提交不会产生第二条。
- 退回入库：「待收货/已收货/已上架」可执行；已上架的会同步清掉对应库存批次，不留残留。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "inbound"
INVENTORY_MODULE = "inventory"
REQUIRED_FIELDS = ["入库单号", "供应商名称", "货物名称"]
STATUS_ORDER = ["待收货", "已收货", "已上架", "已退回"]
ACTION_RULES = {"确认收货": "已收货", "安排上架": "已上架", "退回入库": "已退回"}
NEGATIVE_ACTIONS = ["退回入库"]

# 每个动作允许的起始状态；不在表里的状态一律拒绝，避免跨状态重复操作。
ACTION_FROM_STATUSES: dict[str, set[str]] = {
    "确认收货": {"待收货"},
    "安排上架": {"已收货"},
    "退回入库": {"待收货", "已收货", "已上架"},
}
# 冷链常规到货温区（℃），超出区间视为到货温度不达标。
TEMP_LOWER, TEMP_UPPER = -30.0, -5.0


def _today() -> str:
    return date.today().isoformat()


# 入库单 -> 上架生成的库存批次 id，用于上架幂等与退回时精确清理。
# 映射放在业务层而不是库存行上，避免内部字段泄漏到库存列表/导出接口。
_inventory_link: dict[int, int] = {}


def _init_inventory_link() -> None:
    """启动时从种子数据恢复关联：已上架的入库单本就该对应一条库存批次。"""
    inbound_no_to_id = {
        str(row.get("入库单号")): int(row.get("id", 0))
        for row in store.rows(MODULE)
    }
    for row in store.rows(INVENTORY_MODULE):
        source_no = row.get("来源入库单")
        if source_no and source_no in inbound_no_to_id:
            _inventory_link[inbound_no_to_id[source_no]] = int(row.get("id", 0))


_init_inventory_link()


class InboundService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("入库单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 登记时可一并带上批次号、数量、到货温度等信息，供收货环节核对写回。
        for field in ("批次号", "入库数量", "到货温度", "收货人", "入库时间"):
            value = values.get(field)
            if value not in (None, ""):
                entry[field] = value
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"入库单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于入库管理可执行范围"
        values = values or {}

        current = str(entry.get("status") or "")
        allowed = ACTION_FROM_STATUSES[action]
        if current not in allowed:
            return None, (
                f"入库单当前为「{current}」状态，不能执行「{action}」；"
                f"仅{'、'.join(sorted(allowed))}状态可执行该操作"
            )

        if action == "确认收货":
            error = self._receive(entry, values)
        elif action == "安排上架":
            error = self._shelve(entry)
        else:
            error = self._send_back(entry)
        if error:
            return None, error

        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        # 退回是负面动作；收货/上架的异常标记只由到货温度决定，不在这里覆盖。
        if action == "退回入库":
            entry["abnormal"] = True
        return entry, f"入库单已{action}"

    # ----- 三个动作各自的写回逻辑 -----

    def _receive(self, entry: dict[str, Any], values: dict[str, Any]) -> str:
        """确认收货：到货温度必须落到入库单上，缺温度直接拦下并说明。"""
        temperature = str(values.get("到货温度") or entry.get("到货温度") or "").strip()
        if not temperature:
            return "确认收货失败：未填到货温度，收货记录无法写回，请补充到货温度后再提交"
        entry["到货温度"] = temperature
        receiver = str(values.get("收货人") or entry.get("收货人") or "").strip()
        entry["收货人"] = receiver or "现场收货人"
        received_at = str(values.get("入库时间") or entry.get("入库时间") or "").strip()
        entry["入库时间"] = received_at or _today()
        for field in ("批次号", "入库数量"):
            value = values.get(field)
            if value not in (None, ""):
                entry[field] = value
        entry["abnormal"] = not _temperature_in_range(temperature)
        return ""

    def _shelve(self, entry: dict[str, Any]) -> str:
        """安排上架：幂等写入库存批次；已写过则复用，重复点击也只会有一条。"""
        batch_no = str(entry.get("批次号") or "").strip()
        if not batch_no:
            return "安排上架失败：入库单缺少批次号，无法生成库存批次，请先补全批次信息"
        quantity = entry.get("入库数量")
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return "安排上架失败：入库数量缺失或不是整数，无法登记在库数量"
        if quantity <= 0:
            return "安排上架失败：入库数量必须大于 0，请核对后再提交"

        inventory_rows = store.rows(INVENTORY_MODULE)
        existing = self._find_inventory_batch(entry["id"])
        if existing is not None:
            # 同一张入库单已上架过：只回填库位等信息，不再新增第二条库存。
            inventory = existing
        else:
            inventory = {"id": max((int(row.get("id", 0)) for row in inventory_rows), default=0) + 1}
            inventory_rows.append(inventory)
            _inventory_link[entry["id"]] = int(inventory["id"])
        inventory.update({
            "库存编码": f"INVE-{int(inventory['id']):04d}",
            "货物名称": entry.get("货物名称"),
            "批次号": batch_no,
            "库位编号": f"KW-{int(entry['id']):04d}",
            "在库数量": quantity,
            "锁定量": 0,
            "保质期至": entry.get("保质期至", ""),
            "入库日期": entry.get("入库时间") or _today(),
            "status": "正常",
            "pending": False,
            "abnormal": False,
            # 记录库存来源，退回时按入库单精确清理，避免误删同批次号的其他库存。
            "来源入库单": entry["入库单号"],
        })
        entry["上架库位"] = inventory["库位编号"]
        entry["库存批次id"] = inventory["id"]
        return ""

    def _send_back(self, entry: dict[str, Any]) -> str:
        """退回入库：清掉上架时写入的库存批次和上架痕迹，保证列表/库存口径一致。"""
        inventory = self._find_inventory_batch(entry["id"])
        if inventory is not None:
            store.rows(INVENTORY_MODULE).remove(inventory)
            _inventory_link.pop(entry["id"], None)
        entry.pop("上架库位", None)
        entry.pop("库存批次id", None)
        return ""

    def _find_inventory_batch(self, entry_id: int) -> dict[str, Any] | None:
        inventory_id = _inventory_link.get(entry_id)
        if inventory_id is None:
            return None
        return store.find(INVENTORY_MODULE, inventory_id)


def _temperature_in_range(raw: str) -> bool:
    """判断到货温度是否在冷链常规温区；无法解析成数字时不武断判异常。"""
    try:
        value = float(str(raw).replace("℃", "").replace("°C", "").strip())
    except ValueError:
        return True
    return TEMP_LOWER <= value <= TEMP_UPPER
