import { state, apiFetch, escapeHtml, chatContainer, projectSelect, generationStatus } from './core.js';
import { loadProjects, setMode } from './projects.js';
import { createMessageElement } from './chat.js';
import { displayChecklist, displayPreview, saveChecklistState, toggleChecklistPanel, showPanelMode, clearSearchResults } from './checklist.js';
import { resetBurgerTabs, stopDocsPolling, onProjectChanged } from './docs.js';
export function applyUserRole(role) {
    state.userRole = role || null;
    const isAdmin = role === 'admin';
    document.querySelectorAll('.admin-only').forEach(el => {
        el.style.display = isAdmin ? '' : 'none';
    });
}
export async function forceReAuth() {
    try {
        await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
    }
    catch (e) {
        console.warn('Не удалось сбросить предыдущую сессию:', e);
    }
    applyUserRole(null);
    document.getElementById('authOverlay').classList.remove('hidden');
}
export async function submitLogin() {
    const login = document.getElementById('authLogin').value.trim();
    const password = document.getElementById('authPassword').value;
    const errorEl = document.getElementById('authError');
    const btn = document.getElementById('authSubmitBtn');
    errorEl.style.display = 'none';
    if (!login || !password) {
        errorEl.textContent = 'Введите логин и пароль';
        errorEl.style.display = 'block';
        return;
    }
    btn.disabled = true;
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ login, password }),
            credentials: 'same-origin'
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Ошибка входа');
        }
        const data = await res.json().catch(() => ({}));
        applyUserRole(data.role);
        await new Promise(r => setTimeout(r, 100));
        document.getElementById('authOverlay').classList.add('hidden');
        document.getElementById('historyCurrentUser').textContent = login;
        loadProjects();
    }
    catch (e) {
        errorEl.textContent = e.message;
        errorEl.style.display = 'block';
    }
    finally {
        btn.disabled = false;
    }
}
export async function logoutUser() {
    if (!confirm('Выйти из аккаунта?'))
        return;
    await apiFetch('/api/auth/logout', { method: 'POST' });
    location.reload();
}
export function toggleBurgerMenu(forceState) {
    const overlay = document.getElementById('burgerOverlay');
    const btn = document.getElementById('burgerBtn');
    const shouldOpen = typeof forceState === 'boolean' ? forceState : !overlay.classList.contains('open');
    overlay.classList.toggle('open', shouldOpen);
    btn.classList.toggle('open', shouldOpen);
    if (shouldOpen) {
        // При каждом открытии: вкладка «История», все группы свёрнуты.
        historyExpanded.clear();
        resetBurgerTabs();
        loadChatHistory({ reset: true });
    }
    else {
        stopDocsPolling();
    }
}
const HISTORY_GROUPS = [
    { mode: 'chat', title: 'Чат' },
    { mode: 'generate', title: 'Генерация чек-листа' },
    { mode: 'search', title: 'Поиск по базе' }
];
const historyExpanded = new Set();
function historyGroupOf(mode) {
    if (mode === 'search')
        return 'search';
    if (mode === 'generate' || mode === 'update' || mode === 'extend')
        return 'generate';
    return 'chat';
}
function historyModeClass(mode) {
    if (mode === 'search')
        return 'mode-search';
    if (mode === 'generate' || mode === 'update' || mode === 'extend')
        return 'mode-generate';
    return 'mode-chat';
}
export async function loadChatHistory({ reset = false } = {}) {
    const listEl = document.getElementById('historyList');
    if (reset || !listEl.children.length)
        listEl.innerHTML = '<div class="history-empty">Загрузка…</div>';
    try {
        const res = await apiFetch('/api/chats');
        if (!res.ok)
            throw new Error('Не удалось загрузить историю');
        const chats = await res.json();
        renderChatHistory(chats);
    }
    catch (e) {
        listEl.innerHTML = `<div class="history-empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}
function renderChatHistory(chats) {
    const listEl = document.getElementById('historyList');
    listEl.innerHTML = '';
    const byGroup = { chat: [], generate: [], search: [] };
    chats.forEach(chat => byGroup[historyGroupOf(chat.mode)].push(chat));
    HISTORY_GROUPS.forEach(group => {
        const items = byGroup[group.mode]
            .slice()
            .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
        const expanded = historyExpanded.has(group.mode);
        const groupEl = document.createElement('div');
        groupEl.className = 'history-group' + (expanded ? ' expanded' : '');
        const header = document.createElement('button');
        header.type = 'button';
        header.className = 'history-group-header';
        header.setAttribute('aria-expanded', String(expanded));
        header.innerHTML = `
            <span class="history-group-chevron">${expanded ? '▾' : '▸'}</span>
            <span class="history-group-title ${historyModeClass(group.mode)}">${escapeHtml(group.title)}</span>
            <span class="history-group-count">(${items.length})</span>
        `;
        header.addEventListener('click', () => {
            if (historyExpanded.has(group.mode))
                historyExpanded.delete(group.mode);
            else
                historyExpanded.add(group.mode);
            renderChatHistory(chats);
        });
        groupEl.appendChild(header);
        if (expanded) {
            const body = document.createElement('div');
            body.className = 'history-group-body';
            if (!items.length) {
                body.innerHTML = '<div class="history-empty history-group-empty">Чатов пока нет</div>';
            }
            items.forEach(chat => body.appendChild(createHistoryItem(chat)));
            groupEl.appendChild(body);
        }
        listEl.appendChild(groupEl);
    });
}
function createHistoryItem(chat) {
    const item = document.createElement('div');
    item.className = 'history-item' + (chat.id === state.sessionId ? ' active' : '');
    const date = new Date(chat.updated_at).toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    });
    const modeClass = historyModeClass(chat.mode);
    item.innerHTML = `
        <div class="history-item-main">
            <div class="history-item-title ${modeClass}">${escapeHtml(chat.title || 'Новый чат')}</div>
            <div class="history-item-meta">${escapeHtml(chat.project_id)} · ${date}</div>
        </div>
        <div class="history-item-actions">
            <button class="history-item-action history-item-rename" title="Переименовать чат">✏️</button>
            <button class="history-item-action history-item-delete" title="Удалить чат">🗑</button>
        </div>
    `;
    item.querySelector('.history-item-main').addEventListener('click', () => openChatFromHistory(chat.id));
    item.querySelector('.history-item-rename').addEventListener('click', (e) => {
        e.stopPropagation();
        startChatRename(item, chat);
    });
    item.querySelector('.history-item-delete').addEventListener('click', (e) => {
        e.stopPropagation();
        deleteChatFromHistory(chat.id);
    });
    return item;
}
function startChatRename(item, chat) {
    const titleEl = item.querySelector('.history-item-title');
    const currentTitle = chat.title || 'Новый чат';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'history-item-rename-input';
    input.maxLength = 200;
    input.value = currentTitle;
    titleEl.replaceWith(input);
    input.focus();
    input.select();
    let settled = false;
    const finish = (commit) => {
        if (settled)
            return;
        settled = true;
        const newTitle = input.value.trim();
        if (commit && newTitle && newTitle !== currentTitle) {
            renameChatFromHistory(chat.id, newTitle);
        }
        else {
            loadChatHistory();
        }
    };
    input.addEventListener('click', (e) => e.stopPropagation());
    input.addEventListener('blur', () => finish(true));
    input.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter') {
            e.preventDefault();
            finish(true);
        }
        else if (e.key === 'Escape') {
            e.preventDefault();
            finish(false);
        }
    });
}
async function renameChatFromHistory(chatId, title) {
    try {
        const res = await apiFetch(`/api/chats/${chatId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Не удалось переименовать чат');
        }
    }
    catch (e) {
        alert(e.message);
    }
    loadChatHistory();
}
export async function openChatFromHistory(chatId) {
    try {
        const res = await apiFetch(`/api/chats/${chatId}`);
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Не удалось открыть чат');
        }
        const data = await res.json();
        state.sessionId = data.session_id;
        state.currentProject = data.project_id;
        state.isProjectLoaded = true;
        localStorage.setItem('lastProject', state.currentProject);
        if (projectSelect)
            projectSelect.value = state.currentProject;
        state.messages = (data.messages || []).map(m => ({
            role: m.role, content: m.content, images: m.images || [],
            kind: m.kind || 'text', id: m.id, payload: m.payload || null
        }));
        chatContainer.innerHTML = '';
        let pendingOffer = null;
        (data.messages || []).forEach(m => {
            const extra = { kind: m.kind || 'text', id: m.id, payload: m.payload || null };
            const el = createMessageElement(m.role, m.content, m.images || [], extra);
            chatContainer.appendChild(el);
            if (extra.kind === 'reuse_offer')
                pendingOffer = (m.payload || {}).status === 'pending' ? m : null;
        });
        chatContainer.scrollTop = chatContainer.scrollHeight;
        state.checklistItems = data.test_cases || [];
        state.chatHasChecklist = state.checklistItems.length > 0;
        saveChecklistState();
        clearSearchResults();
        const panelMode = data.panel_mode || (data.mode === 'search' ? 'search' : 'checklist');
        if (panelMode === 'preview' && pendingOffer) {
            showPanelMode('preview');
            displayPreview(pendingOffer.candidates || []);
            toggleChecklistPanel(true);
        }
        else {
            showPanelMode(panelMode === 'preview' ? 'checklist' : panelMode,
                { reused: panelMode === 'search' && data.mode !== 'search' });
            displayChecklist(state.checklistItems);
            toggleChecklistPanel(state.checklistItems.length > 0);
        }
        toggleBurgerMenu(false);
        onProjectChanged();
        setMode(data.mode || 'chat');
        state.welcomeMessageSent = true;
        generationStatus.textContent = `Открыт чат проекта: ${data.project_id}`;
        setTimeout(() => { generationStatus.textContent = ''; }, 3000);
    }
    catch (e) {
        alert('Ошибка открытия чата: ' + e.message);
    }
}
export async function deleteChatFromHistory(chatId) {
    if (!confirm('Удалить этот чат из истории безвозвратно?'))
        return;
    try {
        const res = await apiFetch(`/api/chats/${chatId}`, { method: 'DELETE' });
        if (!res.ok)
            throw new Error('Не удалось удалить чат');
        if (chatId === state.sessionId) {
            state.sessionId = null;
            state.isProjectLoaded = false;
        }
        loadChatHistory();
    }
    catch (e) {
        alert(e.message);
    }
}
