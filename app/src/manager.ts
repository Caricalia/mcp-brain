import { App, applyDocumentTheme, applyHostFonts, applyHostStyleVariables, type McpUiHostContext } from '@modelcontextprotocol/ext-apps'
import './styles.css'

// ---------- Types ----------

interface ProjectRow {
  slug: string
  name: string
  kind: 'team' | 'personal'
  memory_count: number
  parent_slug?: string
  description?: string
}

interface MemoryItem {
  id: string
  project_slug: string
  content: string
  tags: string[]
  pinned: boolean
  visibility: 'shared' | 'private'
  author: string
  updated_at: string
  can_edit: boolean
}

interface SimilarPair {
  a: { id: string; content: string; project_slug: string }
  b: { id: string; content: string; project_slug: string }
  similarity: number
}

type Tab = 'list' | 'add' | 'duplicates'

interface State {
  tab: Tab
  projects: ProjectRow[]
  projectSlug: string | null
  search: string
  visibilityFilter: '' | 'shared' | 'private'
  pinnedFilter: '' | 'true' | 'false'
  tagFilter: string
  items: MemoryItem[]
  loadingList: boolean
  listError: string | null
  editingId: string | null
  confirmDeleteId: string | null
  addStatus: string | null
  addRelated: { id: string; content: string; similarity: number }[]
  duplicates: SimilarPair[] | null
  duplicatesLoading: boolean
  duplicatesAvailable: boolean
}

const state: State = {
  tab: 'list',
  projects: [],
  projectSlug: null,
  search: '',
  visibilityFilter: '',
  pinnedFilter: '',
  tagFilter: '',
  items: [],
  loadingList: true,
  listError: null,
  editingId: null,
  confirmDeleteId: null,
  addStatus: null,
  addRelated: [],
  duplicates: null,
  duplicatesLoading: false,
  duplicatesAvailable: true,
}

const root = document.getElementById('app')!

const app = new App({ name: 'Memory Manager', version: '1.0.0' })

function handleHostContextChanged(ctx: McpUiHostContext) {
  if (ctx.theme) applyDocumentTheme(ctx.theme)
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables)
  if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts)
}

app.onhostcontextchanged = handleHostContextChanged
app.onerror = console.error
app.onteardown = async () => ({})

app.ontoolinput = (params) => {
  const projectSlug = (params?.arguments as { project_slug?: string } | undefined)?.project_slug
  if (projectSlug) state.projectSlug = projectSlug
}

// ---------- Server calls ----------

async function callTool<T = unknown>(name: string, args: Record<string, unknown> = {}): Promise<T> {
  const result = await app.callServerTool({ name, arguments: args })
  const text = result.content?.find((c: { type: string }) => c.type === 'text') as { text?: string } | undefined
  if (!text?.text) throw new Error('Empty response from server')
  const parsed = JSON.parse(text.text)
  if (result.isError || parsed?.error) throw new Error(parsed?.error ?? 'Unknown error')
  return parsed as T
}

async function notifyModel(summary: string) {
  try {
    const anyApp = app as unknown as { updateModelContext?: (x: unknown) => Promise<unknown> }
    if (typeof anyApp.updateModelContext === 'function') {
      await anyApp.updateModelContext({ content: [{ type: 'text', text: summary }] })
    }
  } catch {
    // Host may not support this; never block the UI on it.
  }
}

// ---------- Data loading ----------

async function loadProjects() {
  try {
    const data = await callTool<{ projects: ProjectRow[] }>('list_projects')
    state.projects = data.projects
  } catch (e) {
    state.projects = []
    console.error(e)
  }
}

async function loadList() {
  state.loadingList = true
  state.listError = null
  render()
  try {
    if (state.search.trim()) {
      // app_search (not the model-facing recall tool) so each result carries
      // can_edit - without it, Pin/Edit/Delete would be disabled on every
      // search result.
      const data = await callTool<{ items: MemoryItem[] }>('app_search', {
        query: state.search.trim(),
        project_slug: state.projectSlug ?? undefined,
        limit: 30,
      })
      state.items = data.items
    } else {
      const data = await callTool<{ items: MemoryItem[] }>('app_list_memories', {
        project_slug: state.projectSlug ?? undefined,
        visibility: state.visibilityFilter || undefined,
        pinned: state.pinnedFilter ? state.pinnedFilter === 'true' : undefined,
        tag: state.tagFilter.trim() || undefined,
        offset: 0,
        limit: 50,
      })
      state.items = data.items
    }
  } catch (e) {
    state.listError = e instanceof Error ? e.message : String(e)
    state.items = []
  } finally {
    state.loadingList = false
    render()
  }
}

async function loadDuplicates() {
  state.duplicatesLoading = true
  render()
  try {
    const data = await callTool<{ available: boolean; pairs?: SimilarPair[] }>('app_similar_pairs', {
      project_slug: state.projectSlug ?? undefined,
      threshold: 0.8,
    })
    state.duplicatesAvailable = data.available
    state.duplicates = data.pairs ?? []
  } catch (e) {
    state.duplicatesAvailable = true
    state.duplicates = []
    console.error(e)
  } finally {
    state.duplicatesLoading = false
    render()
  }
}

// ---------- Actions ----------

async function togglePinned(item: MemoryItem) {
  try {
    await callTool('update_memory', { id: item.id, pinned: !item.pinned })
    await notifyModel(`${item.pinned ? 'Unpinned' : 'Pinned'} a memory in "${item.project_slug}".`)
    await loadList()
  } catch (e) {
    alertInline(e)
  }
}

async function saveEdit(item: MemoryItem, content: string, tags: string[], visibility: 'shared' | 'private') {
  try {
    await callTool('update_memory', { id: item.id, content, tags, visibility })
    state.editingId = null
    await notifyModel(`Edited a memory in "${item.project_slug}".`)
    await loadList()
  } catch (e) {
    alertInline(e)
  }
}

async function doDelete(item: MemoryItem) {
  try {
    await callTool('forget', { id: item.id })
    state.confirmDeleteId = null
    await notifyModel(`Deleted a memory from "${item.project_slug}".`)
    await loadList()
  } catch (e) {
    alertInline(e)
  }
}

function alertInline(e: unknown) {
  state.listError = e instanceof Error ? e.message : String(e)
  render()
}

async function submitAdd(form: HTMLFormElement) {
  const data = new FormData(form)
  const project_slug = String(data.get('project_slug') || '')
  const content = String(data.get('content') || '').trim()
  const tags = String(data.get('tags') || '')
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)
  const visibility = String(data.get('visibility') || '') as 'shared' | 'private' | ''

  if (!project_slug || !content) {
    state.addStatus = 'Missing project or content.'
    render()
    return
  }

  try {
    const result = await callTool<{
      id?: string
      duplicate_of?: string
      existing_content?: string
      similarity?: number
      related?: { id: string; content: string; similarity: number }[]
    }>('remember', {
      project_slug,
      content,
      tags: tags.length ? tags : undefined,
      visibility: visibility || undefined,
    })

    if (result.duplicate_of) {
      state.addStatus = `A very similar memory already exists (similarity ${Math.round((result.similarity ?? 0) * 100)}%): "${result.existing_content}"`
      state.addRelated = []
    } else {
      state.addStatus = 'Memory saved.'
      state.addRelated = result.related ?? []
      form.reset()
      await notifyModel(`Added a new memory in "${project_slug}".`)
      if (state.tab === 'list') await loadList()
    }
  } catch (e) {
    state.addStatus = e instanceof Error ? e.message : String(e)
  }
  render()
}

// ---------- Rendering ----------

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
}

function projectOptions(selected: string | null): string {
  let html = `<option value="">All projects</option>`
  for (const p of state.projects) {
    const label = `${p.name} (${p.kind === 'team' ? 'team' : 'personal'})`
    html += `<option value="${escapeHtml(p.slug)}" ${p.slug === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`
  }
  return html
}

function renderHeader(): string {
  return `
    <h1>Memory Manager</h1>
    <div class="tabs">
      <button class="tab-btn ${state.tab === 'list' ? 'active' : ''}" data-tab="list">Memories</button>
      <button class="tab-btn ${state.tab === 'add' ? 'active' : ''}" data-tab="add">Add memory</button>
      <button class="tab-btn ${state.tab === 'duplicates' ? 'active' : ''}" data-tab="duplicates">Possible duplicates</button>
    </div>
  `
}

function renderMemoryCard(item: MemoryItem): string {
  const isEditing = state.editingId === item.id
  const isConfirming = state.confirmDeleteId === item.id

  if (isEditing) {
    return `
      <div class="card" data-id="${item.id}">
        <form class="edit-form" data-id="${item.id}">
          <textarea name="content" maxlength="600">${escapeHtml(item.content)}</textarea>
          <div class="meta"><span class="char-count">${item.content.length}</span>/600</div>
          <div class="row">
            <input type="text" name="tags" value="${escapeHtml(item.tags.join(', '))}" placeholder="comma-separated tags" />
            <select name="visibility">
              <option value="shared" ${item.visibility === 'shared' ? 'selected' : ''}>shared</option>
              <option value="private" ${item.visibility === 'private' ? 'selected' : ''}>private</option>
            </select>
          </div>
          <div class="actions">
            <button type="submit" class="primary">Save</button>
            <button type="button" data-action="cancel-edit">Cancel</button>
          </div>
        </form>
      </div>
    `
  }

  const tagsHtml = item.tags.length ? `<div class="chips">${item.tags.map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join('')}</div>` : ''
  const confirmHtml = isConfirming
    ? `<div class="confirm-box">
         This will be permanently deleted. Are you sure?
         <div class="actions">
           <button class="danger small" data-action="confirm-delete" data-id="${item.id}">Yes, delete</button>
           <button class="small" data-action="cancel-delete">Cancel</button>
         </div>
       </div>`
    : ''

  return `
    <div class="card" data-id="${item.id}">
      <div class="card-head">
        <span class="meta">${escapeHtml(item.project_slug)} - ${escapeHtml(item.author)} - ${escapeHtml(item.updated_at)}</span>
        <span class="badge ${item.visibility}">${item.visibility === 'shared' ? 'shared' : 'private'}</span>
      </div>
      <div class="content">${escapeHtml(item.content)}</div>
      ${tagsHtml}
      <div class="actions">
        <button class="small" data-action="pin" data-id="${item.id}" ${item.can_edit ? '' : 'disabled'}>${item.pinned ? 'Unpin' : 'Pin'}</button>
        <button class="small" data-action="edit" data-id="${item.id}" ${item.can_edit ? '' : 'disabled'}>Edit</button>
        <button class="small danger" data-action="delete" data-id="${item.id}" ${item.can_edit ? '' : 'disabled'}>Delete</button>
      </div>
      ${confirmHtml}
    </div>
  `
}

function renderList(): string {
  const filters = `
    <div class="row">
      <select id="project-select">${projectOptions(state.projectSlug)}</select>
    </div>
    <div class="row">
      <input type="search" id="search-input" placeholder="Search..." value="${escapeHtml(state.search)}" />
    </div>
    <div class="row">
      <select id="visibility-select">
        <option value="">Any visibility</option>
        <option value="shared" ${state.visibilityFilter === 'shared' ? 'selected' : ''}>Shared</option>
        <option value="private" ${state.visibilityFilter === 'private' ? 'selected' : ''}>Private</option>
      </select>
      <select id="pinned-select">
        <option value="">Pinned or not</option>
        <option value="true" ${state.pinnedFilter === 'true' ? 'selected' : ''}>Only pinned</option>
        <option value="false" ${state.pinnedFilter === 'false' ? 'selected' : ''}>Only unpinned</option>
      </select>
      <input type="text" id="tag-input" placeholder="tag" value="${escapeHtml(state.tagFilter)}" />
    </div>
  `

  let body: string
  if (state.loadingList) {
    body = `<div class="loading">Loading memories...</div>`
  } else if (state.listError) {
    body = `<div class="error">Error: ${escapeHtml(state.listError)}</div>`
  } else if (state.items.length === 0) {
    body = `<div class="empty">No matching memories.</div>`
  } else {
    body = state.items.map(renderMemoryCard).join('')
  }

  return filters + body
}

function renderAdd(): string {
  const relatedHtml = state.addRelated.length
    ? `<div class="card">
        <strong>Similar memories:</strong>
        ${state.addRelated
          .map(
            (r) => `<div class="content">(${Math.round(r.similarity * 100)}%) ${escapeHtml(r.content)}
              <div class="actions">
                <button class="small" data-action="goto-edit" data-id="${r.id}">Update the existing one</button>
                <button class="small danger" data-action="quick-forget" data-id="${r.id}">Delete the old one</button>
              </div></div>`,
          )
          .join('')}
      </div>`
    : ''

  return `
    <form id="add-form">
      <div class="row">
        <select name="project_slug">${projectOptions(null)}</select>
      </div>
      <textarea name="content" maxlength="600" placeholder="What do you want to remember?"></textarea>
      <div class="row">
        <input type="text" name="tags" placeholder="comma-separated tags" />
        <select name="visibility">
          <option value="">Default visibility</option>
          <option value="shared">shared</option>
          <option value="private">private</option>
        </select>
      </div>
      <div class="actions">
        <button type="submit" class="primary">Save memory</button>
      </div>
      ${state.addStatus ? `<div class="meta">${escapeHtml(state.addStatus)}</div>` : ''}
    </form>
    ${relatedHtml}
  `
}

function renderDuplicates(): string {
  if (state.duplicatesLoading) return `<div class="loading">Looking for duplicates...</div>`
  if (!state.duplicatesAvailable) {
    return `<div class="empty">Duplicate detection needs semantic search, which is not enabled on this server.</div>`
  }
  if (!state.duplicates || state.duplicates.length === 0) {
    return `<div class="empty">No possible duplicates found.</div>`
  }
  return state.duplicates
    .map(
      (p, i) => `
      <div class="pair-wrap">
        <div class="pair-sim">Similarity: ${Math.round(p.similarity * 100)}%</div>
        <div class="pair">
          <div class="card">
            <div class="meta">${escapeHtml(p.a.project_slug)}</div>
            <div class="content">${escapeHtml(p.a.content)}</div>
            <div class="actions">
              <button class="small" data-action="goto-edit" data-id="${p.a.id}">Edit</button>
              <button class="small danger" data-action="quick-forget" data-id="${p.a.id}">Delete</button>
            </div>
          </div>
          <div class="card">
            <div class="meta">${escapeHtml(p.b.project_slug)}</div>
            <div class="content">${escapeHtml(p.b.content)}</div>
            <div class="actions">
              <button class="small" data-action="goto-edit" data-id="${p.b.id}">Edit</button>
              <button class="small danger" data-action="quick-forget" data-id="${p.b.id}">Delete</button>
            </div>
          </div>
        </div>
      </div>
    `,
    )
    .join('')
}

function render() {
  let body: string
  if (state.tab === 'list') body = renderList()
  else if (state.tab === 'add') body = renderAdd()
  else body = renderDuplicates()

  root.innerHTML = renderHeader() + `<div id="tab-body">${body}</div>`
  wireEvents()
}

function wireEvents() {
  root.querySelectorAll<HTMLButtonElement>('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.tab = btn.dataset.tab as Tab
      state.editingId = null
      state.confirmDeleteId = null
      render()
      if (state.tab === 'list') loadList()
      if (state.tab === 'duplicates') loadDuplicates()
    })
  })

  if (state.tab === 'list') {
    const projectSelect = document.getElementById('project-select') as HTMLSelectElement | null
    projectSelect?.addEventListener('change', () => {
      state.projectSlug = projectSelect.value || null
      loadList()
    })
    const searchInput = document.getElementById('search-input') as HTMLInputElement | null
    let searchTimer: ReturnType<typeof setTimeout> | null = null
    searchInput?.addEventListener('input', () => {
      state.search = searchInput.value
      if (searchTimer) clearTimeout(searchTimer)
      searchTimer = setTimeout(loadList, 350)
    })
    const visSelect = document.getElementById('visibility-select') as HTMLSelectElement | null
    visSelect?.addEventListener('change', () => {
      state.visibilityFilter = visSelect.value as State['visibilityFilter']
      loadList()
    })
    const pinnedSelect = document.getElementById('pinned-select') as HTMLSelectElement | null
    pinnedSelect?.addEventListener('change', () => {
      state.pinnedFilter = pinnedSelect.value as State['pinnedFilter']
      loadList()
    })
    const tagInput = document.getElementById('tag-input') as HTMLInputElement | null
    let tagTimer: ReturnType<typeof setTimeout> | null = null
    tagInput?.addEventListener('input', () => {
      state.tagFilter = tagInput.value
      if (tagTimer) clearTimeout(tagTimer)
      tagTimer = setTimeout(loadList, 350)
    })

    root.querySelectorAll<HTMLButtonElement>('[data-action="pin"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const item = state.items.find((i) => i.id === btn.dataset.id)
        if (item) togglePinned(item)
      })
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="edit"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.editingId = btn.dataset.id ?? null
        render()
      })
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="cancel-edit"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.editingId = null
        render()
      })
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="delete"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.confirmDeleteId = btn.dataset.id ?? null
        render()
      })
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="cancel-delete"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.confirmDeleteId = null
        render()
      })
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="confirm-delete"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const item = state.items.find((i) => i.id === btn.dataset.id)
        if (item) doDelete(item)
      })
    })
    root.querySelectorAll<HTMLFormElement>('.edit-form').forEach((form) => {
      const textarea = form.querySelector('textarea')!
      const counter = form.querySelector('.char-count')!
      textarea.addEventListener('input', () => {
        counter.textContent = String(textarea.value.length)
      })
      form.addEventListener('submit', (ev) => {
        ev.preventDefault()
        const id = form.dataset.id!
        const item = state.items.find((i) => i.id === id)
        if (!item) return
        const data = new FormData(form)
        const content = String(data.get('content') || '')
        const tags = String(data.get('tags') || '')
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean)
        const visibility = String(data.get('visibility') || 'shared') as 'shared' | 'private'
        saveEdit(item, content, tags, visibility)
      })
    })
  }

  if (state.tab === 'add') {
    const form = document.getElementById('add-form') as HTMLFormElement | null
    form?.addEventListener('submit', (ev) => {
      ev.preventDefault()
      submitAdd(form)
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="goto-edit"]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        state.tab = 'list'
        state.editingId = btn.dataset.id ?? null
        await loadList()
      })
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="quick-forget"]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          await callTool('forget', { id: btn.dataset.id })
          state.addRelated = state.addRelated.filter((r) => r.id !== btn.dataset.id)
          render()
        } catch (e) {
          alertInline(e)
        }
      })
    })
  }

  if (state.tab === 'duplicates') {
    root.querySelectorAll<HTMLButtonElement>('[data-action="goto-edit"]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        state.tab = 'list'
        state.editingId = btn.dataset.id ?? null
        await loadList()
      })
    })
    root.querySelectorAll<HTMLButtonElement>('[data-action="quick-forget"]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          await callTool('forget', { id: btn.dataset.id })
          await notifyModel('Deleted a duplicate memory.')
          await loadDuplicates()
        } catch (e) {
          console.error(e)
        }
      })
    })
  }
}

// ---------- Boot ----------

async function boot() {
  root.innerHTML = '<div class="loading">Loading...</div>'
  await app.connect()
  const ctx = app.getHostContext()
  if (ctx) handleHostContextChanged(ctx)
  await loadProjects()
  render()
  await loadList()
}

boot().catch((e) => {
  root.innerHTML = `<div class="error">Could not start: ${escapeHtml(e instanceof Error ? e.message : String(e))}</div>`
})
