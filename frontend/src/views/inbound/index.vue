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
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              :disabled="acting"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(row).length" class="empty-state">无可用动作</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无入库管理数据，可先登记入库单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条入库管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/inbound'
const columns = ["入库单号", "供应商名称", "货物名称", "批次号", "入库数量", "到货温度", "收货人", "入库时间"]
const actions = ["确认收货", "安排上架", "退回入库"]
// 与后端 ACTION_SOURCES 保持一致：每个动作只允许从特定状态发起，已退回是终态
const actionSources: Record<string, string[]> = {
  确认收货: ['待收货'],
  安排上架: ['已收货'],
  退回入库: ['待收货', '已收货', '已上架'],
}
const statuses = ["待收货", "已收货", "已上架", "已退回"]
const stats = [{"label": "今日入库单", "value": 0}, {"label": "待上架单", "value": 0}, {"label": "到货温度不达标", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const acting = ref(false)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function availableActions(row: Row) {
  const status = String(row.status ?? '')
  return actions.filter((action) => actionSources[action]?.includes(status))
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
  if (acting.value) {
    return
  }
  errorMessage.value = ''
  const values: Record<string, string> = { action }
  if (action === '确认收货') {
    const temperature = window.prompt('请确认到货温度', String(row['到货温度'] ?? ''))
    if (temperature === null) {
      return
    }
    if (temperature.trim()) {
      values['到货温度'] = temperature.trim()
    }
  }
  acting.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const result = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(result?.detail ?? '入库管理动作未生效，请稍后重试')
    }
    if (!result?.ok) {
      throw new Error(result?.message ?? '入库管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '入库管理操作失败'
  } finally {
    acting.value = false
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
