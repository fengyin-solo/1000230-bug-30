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
      <article v-for="item in statsCards" :key="item.label" class="stat-card">
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
          <td v-for="column in columns" :key="column">
            <button v-if="column === '审片轮次'" class="link" type="button" @click="openDetail(row)">
              第 {{ row[column] || 0 }} 轮（{{ opinionCount(row) }} 条）
            </button>
            <template v-else>{{ displayCell(row, column) }}</template>
          </td>
          <td class="row-actions">
            <template v-for="action in availableActions(row)" :key="action">
              <button
                class="link"
                type="button"
                :disabled="busyId === String(row.id)"
                @click="openAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-if="!availableActions(row).length" class="muted">—</span>
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

    <div v-if="dialog.kind" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <h3 v-if="dialog.kind === 'detail'">审片意见明细</h3>
        <h3 v-else-if="dialog.action === '提交意见'">提交第 {{ nextRound }} 轮意见</h3>
        <h3 v-else>{{ dialog.action }}</h3>

        <!-- 明细：历史各轮意见完整列出，与列表读同一个详情接口 -->
        <template v-if="dialog.kind === 'detail' && dialog.entry">
          <dl class="detail-list">
            <div v-for="column in columns" :key="column">
              <dt>{{ column }}</dt>
              <dd>{{ displayCell(dialog.entry, column) }}</dd>
            </div>
          </dl>
          <h4 class="detail-sub">各轮意见</h4>
          <table v-if="opinionList(dialog.entry).length" class="data-table inner-table">
            <thead>
              <tr><th>轮次</th><th>问题类型</th><th>修改意见</th><th>回复说明</th><th>审片人</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in opinionList(dialog.entry)" :key="item.轮次">
                <td>第 {{ item.轮次 }} 轮</td>
                <td>{{ item.问题类型 }}</td>
                <td>{{ item.修改意见 }}</td>
                <td>{{ item.回复说明 || '—' }}</td>
                <td>{{ item.审片人 || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">尚未提交意见。</p>
        </template>

        <!-- 动作表单 -->
        <form v-else-if="dialog.kind === 'action' && dialog.row" class="modal-form" @submit.prevent="confirmAction">
          <p class="muted" v-if="dialog.action === '发起审片'">
            审片编号 {{ dialog.row['审片编号'] }}：状态将从「{{ dialog.row.status }}」变为「审片中」。
          </p>
          <template v-else>
            <label class="form-item">
              <span>审片人</span>
              <input v-model="dialog.form.审片人" placeholder="填写本次审片人" />
            </label>
          </template>
          <template v-if="dialog.action === '提交意见'">
            <label class="form-item">
              <span>问题类型 *</span>
              <input v-model="dialog.form.问题类型" placeholder="如：画面色彩、对白字幕、节奏" />
            </label>
            <label class="form-item">
              <span>修改意见 *</span>
              <textarea v-model="dialog.form.修改意见" rows="4" placeholder="请填写具体修改意见，空意见无法提交"></textarea>
            </label>
            <label class="form-item">
              <span>回复说明</span>
              <textarea v-model="dialog.form.回复说明" rows="2" placeholder="可留空"></textarea>
            </label>
          </template>
          <p v-if="dialog.action === '确认通过'" class="muted">
            确认通过后记录锁定，不能再提交意见或重复通过。
          </p>
          <p v-if="dialog.error" class="error-text">{{ dialog.error }}</p>
          <div class="modal-actions">
            <button class="btn" type="button" :disabled="dialog.submitting" @click="closeDialog">取消</button>
            <button class="btn primary" type="submit" :disabled="dialog.submitting">
              {{ dialog.submitting ? '提交中…' : '确认' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Opinion = { 轮次: number; 问题类型: string; 修改意见: string; 回复说明?: string; 审片人?: string }

const ENDPOINT = '/api/review'
const columns = ["审片编号", "审片轮次", "审片人", "审片对象", "问题类型", "修改意见", "回复说明", "审片状态"]
// 列表按状态字段 status 展示，避免出现种子里「审片状态」占位文本与真实状态不一致。
const STATUS_COLUMN = '审片状态'
const filterFields = ["审片编号", "审片人"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const busyId = ref<string>('')

type DialogKind = 'none' | 'action' | 'detail'

const dialog = reactive<{
  kind: DialogKind
  action: string
  row: Row | null
  entry: Row | null
  form: Record<string, string>
  error: string
  submitting: boolean
  requestId: string
}>({
  kind: 'none', action: '', row: null, entry: null, form: {}, error: '', submitting: false, requestId: '',
})

const statsCards = computed(() => [
  { label: '待审片记录', value: rows.value.filter((row) => row.status === '待审片').length },
  { label: '待修改意见', value: rows.value.filter((row) => row.status === '待修改').length },
  { label: '已通过记录', value: rows.value.filter((row) => row.status === '已通过').length },
])

const nextRound = computed(() => {
  if (!dialog.row) return 1
  // 与后端同口径：轮次由已落库意见数决定，首轮即为第 1 轮。
  return opinionList(dialog.row).reduce((max, item) => Math.max(max, Number(item.轮次) || 0), 0) + 1
})

function opinionList(row: Row): Opinion[] {
  const value = (row as Record<string, unknown>).opinions
  return Array.isArray(value) ? (value as Opinion[]) : []
}

function opinionCount(row: Row): number {
  return opinionList(row).length
}

function displayCell(row: Row, column: string): string {
  if (column === STATUS_COLUMN) return String(row.status ?? '—')
  if (column === '审片轮次') return `第 ${row[column] || 0} 轮`
  const value = row[column]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function availableActions(row: Row): string[] {
  switch (row.status) {
    case '待审片':
      return ['发起审片']
    case '审片中':
      return ['提交意见', '确认通过']
    case '待修改':
      return ['提交意见', '确认通过']
    default:
      return []
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '审片记录登记入口尚未接入审批流'
}

function closeDialog() {
  if (dialog.submitting) return
  dialog.kind = 'none'
  dialog.action = ''
  dialog.row = null
  dialog.entry = null
  dialog.form = {}
  dialog.error = ''
  dialog.requestId = ''
}

function openAction(action: string, row: Row) {
  Object.assign(dialog, {
    kind: 'action',
    action,
    row,
    entry: null,
    form: { 审片人: String(row['审片人'] ?? ''), 问题类型: '', 修改意见: '', 回复说明: '' },
    error: '',
    // 打开弹窗即生成 requestId：失败重试沿用同一 ID，保证幂等。
    requestId: createRequestId(),
    submitting: false,
  })
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('审片记录详情读取失败')
    }
    const entry = (await response.json()) as Row
    Object.assign(dialog, { kind: 'detail', action: '', row, entry, form: {}, error: '', requestId: '', submitting: false })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审片记录详情读取失败'
  }
}

async function confirmAction() {
  if (dialog.kind !== 'action' || !dialog.row || dialog.submitting) return
  dialog.error = ''
  dialog.submitting = true
  busyId.value = String(dialog.row.id)
  const body: Record<string, unknown> = {
    action: dialog.action,
    // 乐观并发：带上当前看到的版本，别人先提交时会收到 409。
    expected_version: dialog.row.version ?? 1,
    request_id: dialog.requestId,
  }
  if (dialog.action !== '发起审片') {
    body['审片人'] = dialog.form.审片人.trim()
  }
  if (dialog.action === '提交意见') {
    body['问题类型'] = dialog.form.问题类型.trim()
    body['修改意见'] = dialog.form.修改意见.trim()
    body['回复说明'] = dialog.form.回复说明.trim()
  }

  try {
    const response = await request(`${ENDPOINT}/${dialog.row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = (await response.json().catch(() => null)) as
      | { ok?: boolean; message?: string }
      | null
    if (response.status === 409) {
      // 已有更新轮次：关掉弹窗并强制刷新列表，让操作者看到前一条意见。
      dialog.kind = 'none'
      errorMessage.value = extractMessage(payload, '审片意见已有更新轮次，请刷新后重试')
      await reload()
      return
    }
    if (!response.ok || !payload) {
      throw new Error('审片意见动作未生效，请稍后重试')
    }
    if (payload.ok === false) {
      // 业务校验未通过（如空意见、状态不允许）：弹窗保留，用户修改后直接再提交。
      dialog.error = payload.message || '审片意见动作未生效'
      return
    }
    closeDialog()
    await reload()
  } catch (error) {
    // 网络失败不换 requestId，用户可直接再点「确认」重试且不会重复落库。
    dialog.error = error instanceof Error ? error.message : '审片意见操作失败，可直接重试'
  } finally {
    dialog.submitting = false
    busyId.value = ''
  }
}

function extractMessage(
  payload: { message?: string; detail?: { message?: string } } | null,
  fallback: string,
): string {
  return payload?.message || payload?.detail?.message || fallback
}

function createRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (filters.value['审片编号']) params.set('keyword', filters.value['审片编号'])
  if (filters.value['审片人']) params.set('reviewer', filters.value['审片人'])
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
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
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  background: #fff;
  border-radius: 8px;
  padding: 20px 24px;
  width: min(720px, 92vw);
  max-height: 86vh;
  overflow: auto;
}
.modal h3 { margin: 0 0 12px; }
.modal-form { display: flex; flex-direction: column; gap: 10px; }
.form-item { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.form-item input, .form-item textarea {
  border: 1px solid var(--border, #d0d5dd);
  border-radius: 6px;
  padding: 6px 10px;
  font: inherit;
}
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.detail-list { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; margin: 0 0 12px; }
.detail-list div { display: flex; gap: 8px; font-size: 13px; }
.detail-list dt { color: #667085; min-width: 72px; margin: 0; }
.detail-list dd { margin: 0; }
.detail-sub { margin: 12px 0 6px; font-size: 14px; }
.inner-table { font-size: 13px; }
.muted { color: #667085; font-size: 13px; }
.link:disabled { color: #98a2b3; cursor: not-allowed; }
</style>
