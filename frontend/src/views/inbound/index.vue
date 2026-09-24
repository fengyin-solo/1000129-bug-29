<template>
  <section class="page" data-module="inbound">
    <header class="page-head">
      <div>
        <h2>入库管理管理</h2>
        <p class="page-desc">维护入库单，围绕入库单号、供应商名称、货物名称、批次号做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记入库单</button>
        <button class="btn" type="button" @click="exportRows">导出入库管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ columnLabels[column] ?? column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <template v-for="action in actionsFor(row)" :key="action">
              <button
                class="link"
                type="button"
                :disabled="busyKey === `${action}-${String(row.id)}`"
                @click="runAction(action, row)"
              >
                {{ busyKey === `${action}-${String(row.id)}` ? '提交中…' : action }}
              </button>
            </template>
            <span v-if="!actionsFor(row).length" class="muted-text">—</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无入库管理数据，可先登记入库单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条入库管理记录</span>
      <span v-if="successMessage" class="success-text">{{ successMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/inbound'
const columns = ["入库单号", "供应商名称", "货物名称", "批次号", "入库数量", "到货温度", "收货人", "入库时间", "status"]
const columnLabels: Record<string, string> = { status: '状态' }
const stats = [{"label": "今日入库单", "value": 0}, {"label": "待上架单", "value": 0}, {"label": "到货温度不达标", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const busyKey = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

// 各状态允许的动作必须与后端状态机一致，避免点了才被告知不允许。
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  '待收货': ['确认收货', '退回入库'],
  '已收货': ['安排上架', '退回入库'],
  '已上架': ['退回入库'],
  '已退回': [],
}

function actionsFor(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '入库单登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  const values: Record<string, string> = { action }
  if (action === '确认收货') {
    // 列表上没留到货温度时，收货环节必须补录，否则后端会拒绝写回。
    const existing = String(row['到货温度'] ?? '').trim()
    const temperature = window.prompt('请输入到货温度（℃，冷链常规区间 -30 ~ -5）', existing)
    if (temperature === null) {
      return
    }
    const trimmed = temperature.trim()
    if (!trimmed) {
      errorMessage.value = '确认收货失败：未填到货温度，收货记录无法写回'
      return
    }
    values['到货温度'] = trimmed
  }
  const key = `${action}-${String(row.id)}`
  busyKey.value = key
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    let payload: { ok?: boolean; message?: string } | null = null
    try {
      payload = await response.json()
    } catch {
      payload = null
    }
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' && 'message' in payload
        ? String(payload.message)
        : '入库管理动作未生效，请稍后重试'
      throw new Error(detail)
    }
    // 后端对状态不对、字段缺失等业务失败返回 ok=false，必须把说明展示出来。
    if (!payload || payload.ok === false) {
      throw new Error(payload?.message || '入库管理动作未生效，请稍后重试')
    }
    successMessage.value = payload.message || '操作成功'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '入库管理操作失败'
  } finally {
    busyKey.value = ''
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('入库单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '入库管理列表读取失败'
  }
}

onMounted(reload)
</script>
