<template>
  <section class="page" data-module="review">
    <header class="page-head">
      <div>
        <h2>审片意见管理</h2>
        <p class="page-desc">维护审片记录，围绕审片编号、审片轮次、审片人、审片对象做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记审片记录</button>
        <button class="btn" type="button" @click="exportRows">导出审片意见清单</button>
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
      <label class="filter-item">
        <span>审片状态</span>
        <select v-model="filters['status']">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
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
          <td v-for="column in columns" :key="column">
            <button v-if="column === '审片编号'" class="link" type="button" @click="openDetail(row)">
              {{ row[column] ?? '—' }}
            </button>
            <template v-else>{{ row[column] || '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              :disabled="busyId === String(row.id)"
              type="button"
              @click="onAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无审片意见数据，可先登记审片记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条审片意见记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="opinionTarget" class="modal-mask" @click.self="closeOpinion">
      <form class="modal-card" @submit.prevent="submitOpinion">
        <h3>提交意见 · 第 {{ nextRound }} 轮</h3>
        <p class="page-desc">
          {{ opinionTarget['审片编号'] }} / {{ opinionTarget['审片对象'] }} / 当前 {{ opinionTarget.status }}
        </p>
        <label class="filter-item">
          <span>问题类型 <em>*</em></span>
          <input v-model="opinionForm['问题类型']" placeholder="如：画面穿帮、字幕错误、节奏问题" />
        </label>
        <label class="filter-item">
          <span>修改意见 <em>*</em></span>
          <textarea v-model="opinionForm['修改意见']" rows="4" placeholder="请描述本轮审片发现的问题与修改要求"></textarea>
        </label>
        <label class="filter-item">
          <span>回复说明</span>
          <textarea v-model="opinionForm['回复说明']" rows="2" placeholder="制作方回复，可为空"></textarea>
        </label>
        <label class="filter-item">
          <span>审片人</span>
          <input v-model="opinionForm['审片人']" :placeholder="String(opinionTarget['审片人'] || '请输入审片人')" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" :disabled="submitting" @click="closeOpinion">取消</button>
          <button class="btn primary" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '提交意见' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="detailTarget" class="modal-mask" @click.self="detailTarget = null">
      <div class="modal-card">
        <h3>审片记录明细</h3>
        <p class="page-desc">{{ detailTarget['审片编号'] }} / {{ detailTarget['审片对象'] }}</p>
        <table class="data-table">
          <tbody>
            <tr v-for="column in columns" :key="column">
              <th>{{ column }}</th>
              <td>{{ detailTarget[column] || '—' }}</td>
            </tr>
            <tr>
              <th>数据版本</th>
              <td>v{{ Number(detailTarget.version ?? 1) }}</td>
            </tr>
          </tbody>
        </table>
        <h4>历轮意见（{{ (detailTarget.opinions ?? []).length }} 轮）</h4>
        <table class="data-table">
          <thead>
            <tr><th>轮次</th><th>问题类型</th><th>修改意见</th><th>回复说明</th><th>审片人</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in detailTarget.opinions ?? []" :key="item.round">
              <td>第 {{ item.round }} 轮</td>
              <td>{{ item['问题类型'] }}</td>
              <td>{{ item['修改意见'] }}</td>
              <td>{{ item['回复说明'] || '—' }}</td>
              <td>{{ item['审片人'] }}</td>
            </tr>
            <tr v-if="!(detailTarget.opinions ?? []).length">
              <td colspan="5" class="empty-state">尚未提交过审片意见</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detailTarget = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Opinion = {
  round: number
  问题类型: string
  修改意见: string
  回复说明: string
  审片人: string
}
type Row = {
  id: number
  status: string
  version: number
  opinions?: Opinion[]
  [key: string]: string | number | Opinion[] | null | undefined
}

const ENDPOINT = '/api/review'
const columns = ["审片编号", "审片轮次", "问题类型", "修改意见", "回复说明", "审片人", "审片对象", "审片状态"]
const statuses = ["待审片", "审片中", "待修改", "已通过"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ 'status': '' })
const filterFields = ["审片编号", "审片轮次", "审片人"]

const stats = computed(() => [
  { label: "待审片记录", value: rows.value.filter((row) => row.status === "待审片").length },
  { label: "待修改意见", value: rows.value.filter((row) => row.status === "待修改").length },
  { label: "已通过记录", value: rows.value.filter((row) => row.status === "已通过").length },
])

const opinionTarget = ref<Row | null>(null)
const detailTarget = ref<Row | null>(null)
const submitting = ref(false)
const busyId = ref<string | null>(null)
// 幂等键在弹窗打开时生成：提交成功或放弃后失效；网络失败重试沿用同一个键，避免追加出两轮。
let idempotencyKey = ''
const opinionForm = reactive<Record<string, string>>({
  问题类型: '',
  修改意见: '',
  回复说明: '',
  审片人: '',
})

const nextRound = computed(() => {
  if (!opinionTarget.value) return 1
  return Number(opinionTarget.value['审片轮次'] ?? 0) + 1
})

function availableActions(row: Row): string[] {
  switch (row.status) {
    case '待审片':
      return ['发起审片', '提交意见']
    case '审片中':
    case '待修改':
      return ['提交意见', '确认通过']
    default:
      return []
  }
}

function resetFilters() {
  filters.value = { 'status': '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '审片记录登记入口尚未接入审批流'
}

function onAction(action: string, row: Row) {
  if (action === '提交意见') {
    openOpinion(row)
  } else {
    void runSimpleAction(action, row)
  }
}

function openOpinion(row: Row) {
  opinionTarget.value = row
  idempotencyKey = (globalThis.crypto?.randomUUID?.() ?? `review-${row.id}-${Date.now()}-${Math.random()}`)
  opinionForm['问题类型'] = ''
  opinionForm['修改意见'] = ''
  opinionForm['回复说明'] = ''
  opinionForm['审片人'] = ''
  errorMessage.value = ''
}

function closeOpinion() {
  opinionTarget.value = null
  idempotencyKey = ''
  submitting.value = false
}

async function submitOpinion() {
  const target = opinionTarget.value
  if (!target) return
  if (!opinionForm['问题类型'].trim() || !opinionForm['修改意见'].trim()) {
    errorMessage.value = '问题类型和修改意见不能为空'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${target.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          action: '提交意见',
          问题类型: opinionForm['问题类型'],
          修改意见: opinionForm['修改意见'],
          回复说明: opinionForm['回复说明'],
          审片人: opinionForm['审片人'],
          expected_version: Number(target.version ?? 1),
          idempotency_key: idempotencyKey,
        },
      }),
    })
    if (response.status === 409) {
      const payload = await response.json().catch(() => ({ detail: '记录已被其他人更新，请刷新后重试' }))
      errorMessage.value = payload.detail || '记录已被其他人更新，请刷新后重试'
      closeOpinion()
      await reload()
      return
    }
    if (!response.ok) {
      // 保留弹窗与幂等键，用户可直接再次点击提交（重试不产生重复轮次）。
      throw new Error('审片意见未生效，请稍后重试（已保留内容，重试不会重复提交）')
    }
    closeOpinion()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审片意见操作失败'
    submitting.value = false
  }
}

async function runSimpleAction(action: string, row: Row) {
  busyId.value = String(row.id)
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, expected_version: Number(row.version ?? 1) } }),
    })
    if (response.status === 409) {
      const payload = await response.json().catch(() => ({ detail: '记录已被其他人更新，请刷新后重试' }))
      errorMessage.value = payload.detail || '记录已被其他人更新，请刷新后重试'
      await reload()
      return
    }
    const payload = await response.json().catch(() => null)
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.message || '审片意见动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审片意见操作失败'
  } finally {
    busyId.value = null
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('审片记录详情读取失败')
    }
    detailTarget.value = (await response.json()) as Row
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审片记录详情读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value && key !== 'status') params.set(key, value)
  }
  if (filters.value.status) params.set('status', filters.value.status)
  const query = params.toString()
  try {
    const response = await request(query ? `${ENDPOINT}?${query}` : ENDPOINT)
    if (!response.ok) {
      throw new Error('审片记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审片意见列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}

.modal-card {
  width: min(720px, 92vw);
  max-height: 86vh;
  overflow: auto;
  background: var(--color-surface, #fff);
  border-radius: 10px;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.modal-card .filter-item {
  flex-direction: column;
  align-items: stretch;
}

.modal-card textarea {
  resize: vertical;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.link:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
