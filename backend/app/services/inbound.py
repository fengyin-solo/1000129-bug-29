"""入库管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "inbound"
INVENTORY_MODULE = "inventory"
REQUIRED_FIELDS = ["入库单号", "供应商名称", "货物名称"]
ENTRY_FIELDS = ["入库单号", "供应商名称", "货物名称", "批次号", "入库数量", "到货温度", "收货人", "入库时间"]
STATUS_ORDER = ["待收货", "已收货", "已上架", "已退回"]
ACTION_RULES = {"确认收货": "已收货", "安排上架": "已上架", "退回入库": "已退回"}
NEGATIVE_ACTIONS = []
TERMINAL_STATUS = "已退回"
# 每个动作允许从哪些状态发起；已退回是终态，任何动作都不可再执行
ACTION_SOURCES = {
    "确认收货": {"待收货"},
    "安排上架": {"已收货"},
    "退回入库": {"待收货", "已收货", "已上架"},
}
# 确认收货时允许随动作一起写回入库单的字段
RECEIPT_WRITEBACK_FIELDS = ["批次号", "入库数量", "到货温度", "收货人", "入库时间"]


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
        # 全量字段都要落库，不能只保留必填项，否则到货温度等随单信息会被静默丢弃
        entry.update({field: values.get(field) for field in ENTRY_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"入库单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于入库管理可执行范围"
        current = str(entry.get("status") or "")
        if current == TERMINAL_STATUS:
            return None, "入库单已退回，不能再执行任何动作"
        if current not in ACTION_SOURCES[action]:
            return None, f"入库单当前状态为「{current}」，不能执行{action}"
        target = ACTION_RULES[action]
        if action == "确认收货":
            self._write_back_receipt(entry, values)
            message = "入库单已确认收货，随单信息已写回"
        elif action == "安排上架":
            created = self._shelve_to_inventory(entry, values)
            if created:
                message = "入库单已安排上架，库存批次已写入库存管理"
            else:
                message = "入库单已安排上架，库存中已存在对应批次，未重复写入"
        else:
            removed = self._clear_inventory_records(entry)
            if removed:
                message = f"入库单已退回，残留的 {removed} 条上架库存记录已清理"
            else:
                message = "入库单已退回"
        entry["status"] = target
        entry["pending"] = target in {"待收货", "已收货"}
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, message

    def _write_back_receipt(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        """确认收货时把随动作提交的到货温度、收货人等写回入库单；空值不覆盖已有数据。"""
        for field in RECEIPT_WRITEBACK_FIELDS:
            incoming = values.get(field)
            if incoming is None or str(incoming).strip() == "":
                continue
            entry[field] = incoming

    def _shelve_to_inventory(self, entry: dict[str, Any], values: dict[str, Any]) -> bool:
        """把入库单批次写入库存管理；同一入库单只允许写入一次，重复上架不会产生重复批次。"""
        rows = store.rows(INVENTORY_MODULE)
        source = str(entry.get("入库单号") or "").strip()
        if source and any(str(row.get("来源入库单") or "") == source for row in rows):
            return False
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        rows.append({
            "id": new_id,
            "库存编码": f"INVE-{new_id:04d}",
            "货物名称": entry.get("货物名称"),
            "批次号": entry.get("批次号"),
            "库位编号": str(values.get("库位编号") or "").strip() or "待分配",
            "在库数量": entry.get("入库数量"),
            "锁定量": 0,
            "保质期至": values.get("保质期至") or "",
            "入库日期": entry.get("入库时间"),
            "来源入库单": source,
            "status": "正常",
            "pending": True,
            "abnormal": False,
        })
        return True

    def _clear_inventory_records(self, entry: dict[str, Any]) -> int:
        """退回入库时清掉该单上架写入的库存批次，避免列表里残留查不到的记录。"""
        rows = store.rows(INVENTORY_MODULE)
        source = str(entry.get("入库单号") or "").strip()
        if not source:
            return 0
        kept = [row for row in rows if str(row.get("来源入库单") or "") != source]
        removed = len(rows) - len(kept)
        if removed:
            rows[:] = kept
        return removed
