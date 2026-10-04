import { state, apiFetch, escapeHtml } from './core.js';
const SUPPORTED_EXTENSIONS = ['.txt', '.md', '.docx', '.pdf', '.xlsx', '.xls', '.json', '.html'];
const POLL_INTERVAL_MS = 2000;
let activeTab = 'history';
let docsPollTimer = null;
let docsCache = [];
let docsCacheProject = null;
let docsLoadSeq = 0;
function burgerOpen() {
    const overlay = document.getElementById('burgerOverlay');
    return overlay && overlay.classList.contains('open');
}
function docsTabVisible() {
    return activeTab === 'docs' && burgerOpen();
}
export function switchBurgerTab(tab) {
    activeTab = tab === 'docs' ? 'docs' : 'history';
    document.querySelectorAll('.burger-tab').forEach(btn => {
        const isActive = btn.getAttribute('data-tab') === activeTab;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-selected', String(isActive));
    });
    document.getElementById('historyTabPanel').style.display = activeTab === 'history' ? '' : 'none';
    document.getElementById('docsTabPanel').style.display = activeTab === 'docs' ? '' : 'none';
    if (activeTab === 'docs') {
        document.getElementById('docsUploadMessages').innerHTML = '';
        loadDocs();
    }
    else {
        stopDocsPolling();
    }
}
export function resetBurgerTabs() {
    switchBurgerTab('history');
}
export function stopDocsPolling() {
    if (docsPollTimer) {
        clearTimeout(docsPollTimer);
        docsPollTimer = null;
    }
}
export function onProjectChanged() {
    if (docsTabVisible()) {
        document.getElementById('docsUploadMessages').innerHTML = '';
        loadDocs();
    }
}
function projectLabel() {
    const id = state.currentProject;
    return (state.projectNames && state.projectNames[id]) || id || '';
}
function docsUrl(suffix = '') {
    return `/api/projects/${encodeURIComponent(state.currentProject)}/docs${suffix}`;
}
export async function loadDocs() {
    const label = document.getElementById('docsProjectLabel');
    const body = document.getElementById('docsBody');
    const listEl = document.getElementById('docsList');
    stopDocsPolling();
    if (!state.currentProject) {
        label.textContent = '';
        body.style.display = 'none';
        listEl.innerHTML = '';
        label.innerHTML = '<div class="history-empty">Выберите проект</div>';
        return;
    }
    body.style.display = '';
    label.textContent = `Проект: ${projectLabel()}`;
    const projectId = state.currentProject;
    if (docsCacheProject !== projectId) {
        docsCache = [];
        docsCacheProject = projectId;
    }
    const seq = ++docsLoadSeq;
    if (!docsCache.length)
        listEl.innerHTML = '<div class="history-empty">Загрузка…</div>';
    try {
        const res = await apiFetch(docsUrl());
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Не удалось загрузить документы');
        }
        const docs = await res.json();
        if (seq !== docsLoadSeq || projectId !== state.currentProject)
            return;
        docsCache = docs;
        renderDocs(docs);
        if (docsTabVisible() && docs.some(d => d.index_status === 'pending' || d.index_status === 'indexing')) {
            docsPollTimer = setTimeout(() => {
                if (docsTabVisible())
                    loadDocs();
            }, POLL_INTERVAL_MS);
        }
    }
    catch (e) {
        if (seq !== docsLoadSeq)
            return;
        listEl.innerHTML = `<div class="history-empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}
export function formatFileSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024)
        return `${n} Б`;
    if (n < 1024 * 1024)
        return `${Math.round(n / 1024)} КБ`;
    return `${(n / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
}
function formatDocDate(iso) {
    if (!iso)
        return '';
    const d = new Date(iso);
    const pad = v => String(v).padStart(2, '0');
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function statusHtml(doc) {
    switch (doc.index_status) {
        case 'pending':
            return '<span class="doc-status doc-status-pending">◌ В очереди</span>';
        case 'indexing':
            return '<span class="doc-status doc-status-pending">◌ Индексируется…</span>';
        case 'indexed':
            return '<span class="doc-status doc-status-ok">● Проиндексирован</span>';
        case 'failed':
            return `<span class="doc-status doc-status-failed">✕ Ошибка: ${escapeHtml(doc.index_error || 'неизвестная ошибка')}</span>
                    <button type="button" class="doc-retry-btn">Повторить</button>`;
        default:
            return '';
    }
}
function renderDocs(docs) {
    const listEl = document.getElementById('docsList');
    if (!docs.length) {
        listEl.innerHTML = '<div class="history-empty">Документов пока нет</div>';
        return;
    }
    listEl.innerHTML = '';
    docs.forEach(doc => {
        const row = document.createElement('div');
        row.className = 'doc-item';
        const meta = [formatFileSize(doc.size_bytes), formatDocDate(doc.updated_at), doc.uploaded_by || '—']
            .map(escapeHtml).join(' · ');
        row.innerHTML = `
            <div class="doc-item-head">
                <span class="doc-icon">📄</span>
                <span class="doc-name" title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</span>
                <span class="doc-actions">
                    <button type="button" class="doc-action doc-download" title="Скачать">⬇</button>
                    <button type="button" class="doc-action doc-delete" title="Удалить">🗑</button>
                </span>
            </div>
            <div class="doc-meta">${meta}</div>
            <div class="doc-status-row">${statusHtml(doc)}</div>
        `;
        row.querySelector('.doc-download').addEventListener('click', () => downloadDoc(doc));
        row.querySelector('.doc-delete').addEventListener('click', () => deleteDoc(doc));
        const retry = row.querySelector('.doc-retry-btn');
        if (retry)
            retry.addEventListener('click', () => retryDoc(doc, retry));
        listEl.appendChild(row);
    });
}
function downloadDoc(doc) {
    const a = document.createElement('a');
    a.href = docsUrl(`/${encodeURIComponent(doc.id)}/file`);
    a.download = doc.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
}
async function deleteDoc(doc) {
    if (!confirm(`Удалить «${doc.filename}»? Документ пропадёт из поиска и из контекста генерации.`))
        return;
    try {
        const res = await apiFetch(docsUrl(`/${encodeURIComponent(doc.id)}`), { method: 'DELETE' });
        if (!res.ok && res.status !== 404) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Не удалось удалить документ');
        }
    }
    catch (e) {
        alert(e.message);
    }
    docsCache = docsCache.filter(d => d.id !== doc.id);
    renderDocs(docsCache);
    loadDocs();
}
async function retryDoc(doc, btn) {
    btn.disabled = true;
    try {
        const res = await apiFetch(docsUrl(`/${encodeURIComponent(doc.id)}/reindex`), { method: 'POST' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Не удалось запустить индексацию');
        }
    }
    catch (e) {
        alert(e.message);
    }
    loadDocs();
}
function showUploadMessages(lines) {
    const box = document.getElementById('docsUploadMessages');
    box.innerHTML = lines
        .map(l => `<div class="docs-upload-message ${l.error ? 'error' : ''}">${escapeHtml(l.text)}</div>`)
        .join('');
}
async function uploadFiles(fileList) {
    if (!state.currentProject) {
        alert('Сначала выберите проект');
        return;
    }
    let files = Array.from(fileList || []);
    if (!files.length)
        return;
    const existing = new Set(docsCache.map(d => d.filename));
    const duplicates = files.filter(f => existing.has(f.name));
    if (duplicates.length) {
        const names = duplicates.map(f => `«${f.name}»`).join(', ');
        if (!confirm(`Эти файлы уже загружены и будут заменены: ${names}. Продолжить?`)) {
            files = files.filter(f => !existing.has(f.name));
        }
    }
    if (!files.length)
        return;
    const form = new FormData();
    files.forEach(f => form.append('files', f, f.name));
    showUploadMessages([{ text: `Загрузка файлов: ${files.length}…` }]);
    try {
        const res = await apiFetch(docsUrl(), { method: 'POST', body: form });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Не удалось загрузить файлы');
        }
        const data = await res.json();
        const lines = [];
        (data.results || []).forEach(r => {
            if (r.status === 'error')
                lines.push({ text: `«${r.filename}»: ${r.error}`, error: true });
            else if (r.status === 'unchanged')
                lines.push({ text: `«${r.filename}»: файл не изменился` });
        });
        showUploadMessages(lines);
    }
    catch (e) {
        showUploadMessages([{ text: e.message, error: true }]);
    }
    loadDocs();
}
function isFileDrag(e) {
    return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
}
function initDocsTab() {
    // Файл, брошенный мимо зоны загрузки, не должен открываться браузером вместо приложения.
    ['dragover', 'drop'].forEach(type => window.addEventListener(type, (e) => {
        if (isFileDrag(e) && !e.target.closest('#docsDropzone'))
            e.preventDefault();
    }));
    const dropzone = document.getElementById('docsDropzone');
    const input = document.getElementById('docsFileInput');
    const pickBtn = document.getElementById('docsPickBtn');
    const extEl = document.getElementById('docsExtensions');
    if (!dropzone || !input)
        return;
    input.accept = SUPPORTED_EXTENSIONS.join(',');
    extEl.textContent = SUPPORTED_EXTENSIONS.map(e => e.slice(1)).join(', ');
    pickBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        input.click();
    });
    input.addEventListener('change', () => {
        uploadFiles(input.files);
        input.value = '';
    });
    ['dragenter', 'dragover'].forEach(type => dropzone.addEventListener(type, (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
    }));
    ['dragleave', 'dragend'].forEach(type => dropzone.addEventListener(type, () => {
        dropzone.classList.remove('dragover');
    }));
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer && e.dataTransfer.files)
            uploadFiles(e.dataTransfer.files);
    });
}
if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', initDocsTab);
else
    initDocsTab();
