import { state, apiFetch, projectSelect, statusDot, generationStatus, chatContainer, checklistContainer, checklistCount, messageInput } from './core.js';
import { addMessage } from './chat.js';
import { clearSearchResults, showPanelMode } from './checklist.js';
import { onProjectChanged } from './docs.js';
export async function loadProjects() {
    try {
        const response = await apiFetch('/api/projects');
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP ${response.status}: ${errorText}`);
        }
        const projects = await response.json();
        if (!Array.isArray(projects)) {
            projectSelect.innerHTML = '<option value="">Ошибка: неверный формат данных</option>';
            return [];
        }
        projectSelect.innerHTML = '';
        const emptyOption = document.createElement('option');
        emptyOption.value = '';
        emptyOption.textContent = '-- Выберите проект --';
        projectSelect.appendChild(emptyOption);
        state.projectNames = {};
        projects.forEach(proj => {
            state.projectNames[proj.id] = proj.name || proj.id;
            const option = document.createElement('option');
            option.value = proj.id || '';
            let label = proj.name || 'Без названия';
            const docCount = proj.documents_count || 0;
            if (docCount > 0)
                label += ` (${docCount} док.)`;
            if (proj.has_knowledge === true)
                label += ' · база знаний';
            if (proj.testops_configured === true)
                label += ' · TestOps';
            option.textContent = label;
            projectSelect.appendChild(option);
        });
        const savedProject = localStorage.getItem('lastProject');
        if (savedProject && projects.some(p => p.id === savedProject)) {
            projectSelect.value = savedProject;
            setTimeout(() => { switchProject(); }, 300);
        }
        return projects;
    }
    catch (error) {
        console.error('Ошибка загрузки проектов:', error);
        projectSelect.innerHTML = `<option value="">Ошибка: ${error.message}</option>`;
        return [];
    }
}
async function openFreshSession(projectId, { welcomeMessage, resetMode }) {
    const response = await apiFetch('/api/projects/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, sync_documents: true })
    });
    if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Ошибка переключения проекта');
    }
    const data = await response.json();
    state.sessionId = data.session_id;
    state.currentProject = data.project_id;
    state.isProjectLoaded = true;
    localStorage.setItem('lastProject', projectId);
    state.messages = [];
    state.checklistItems = [];
    chatContainer.innerHTML = '';
    checklistContainer.innerHTML = `
        <div class="checklist-empty">
            <p>Чек-лист пока пуст</p>
            <p style="font-size: 13px; margin-top: 8px;">Сгенерируйте его через чат</p>
        </div>
    `;
    checklistCount.textContent = '0';
    clearSearchResults();
    showPanelMode('checklist');
    state.welcomeMessageSent = false;
    state.chatHasChecklist = false;
    setMode(resetMode ? 'chat' : state.currentMode);
    if (welcomeMessage)
        addMessage('assistant', welcomeMessage);
    state.welcomeMessageSent = true;
    onProjectChanged();
    return data;
}
export async function startResultChat() {
    const response = await apiFetch('/api/chats/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: state.currentProject })
    });
    if (!response.ok) {
        let detail = 'Не удалось создать новый чат';
        try {
            const err = await response.json();
            detail = err.detail || detail;
        }
        catch { }
        throw new Error(detail);
    }
    const data = await response.json();
    state.sessionId = data.session_id;
    state.messages = [];
    state.checklistItems = [];
    state.chatHasChecklist = false;
    chatContainer.innerHTML = '';
    checklistContainer.innerHTML = `
        <div class="checklist-empty">
            <p>Чек-лист пока пуст</p>
        </div>
    `;
    checklistCount.textContent = '0';
    clearSearchResults();
    return data;
}
export async function switchProject() {
    const projectId = projectSelect.value.trim();
    if (!projectId)
        return;
    statusDot.className = 'status-dot offline';
    generationStatus.textContent = '⏳ Загрузка...';
    try {
        await openFreshSession(projectId, {
            welcomeMessage: 'Проект загружен. Выберите режим внизу: генерация чек-листа по функционалу или поиск похожих тест-кейсов в базе.',
            resetMode: true
        });
        statusDot.className = 'status-dot';
        generationStatus.textContent = 'Готово';
        setTimeout(() => { generationStatus.textContent = ''; }, 3000);
    }
    catch (error) {
        console.error('Ошибка:', error);
        statusDot.className = 'status-dot offline';
        generationStatus.textContent = 'Ошибка';
        alert(`Ошибка: ${error.message}`);
    }
}
export async function startNewChat() {
    if (!state.currentProject) {
        alert('Сначала выберите проект');
        return;
    }
    statusDot.className = 'status-dot offline';
    generationStatus.textContent = '⏳ Создание нового чата...';
    try {
        await openFreshSession(state.currentProject, {
            welcomeMessage: 'Начат новый чат.',
            resetMode: false
        });
        statusDot.className = 'status-dot';
        generationStatus.textContent = 'Новый чат создан';
        setTimeout(() => { generationStatus.textContent = ''; }, 3000);
    }
    catch (error) {
        console.error('Ошибка:', error);
        statusDot.className = 'status-dot offline';
        generationStatus.textContent = 'Ошибка';
        alert(`Ошибка: ${error.message}`);
    }
}
export async function syncTestOpsExamples() {
    if (!state.currentProject || !state.sessionId) {
        alert('Сначала выберите проект');
        return;
    }
    if (!confirm(`Синхронизировать тест-кейсы из TestOps для проекта?`))
        return;
    generationStatus.textContent = '⏳ Синхронизация с TestOps...';
    addMessage('assistant', `Загружаю тест-кейсы из TestOps...`);
    try {
        const response = await apiFetch('/api/testops/sync-examples', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: state.currentProject,
                max_cases: 5000,
                sync_mode: 'append'
            })
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка синхронизации');
        }
        const data = await response.json();
        if (data.success) {
            generationStatus.textContent = ` ${data.message}`;
            let message = `Синхронизация с TestOps завершена!\n${data.message}`;
            if (data.errors && data.errors.length)
                message += `\nОшибок: ${data.errors.length} (подробности в логах сервера)`;
            addMessage('assistant', message);
            if (data.files && data.files.length > 0) {
                const fileList = data.files.slice(0, 5).map(f => `- ${f.file}: ${f.name}`).join('\n');
                if (data.files.length > 5) {
                    addMessage('assistant', `Примеры:\n${fileList}\n... и еще ${data.files.length - 5}`);
                }
                else {
                    addMessage('assistant', `Примеры:\n${fileList}`);
                }
            }
        }
        else {
            throw new Error(data.message || 'Неизвестная ошибка');
        }
    }
    catch (error) {
        console.error('Ошибка:', error);
        generationStatus.textContent = 'Ошибка синхронизации';
        addMessage('assistant', `Ошибка синхронизации с TestOps: ${error.message}`);
    }
    setTimeout(() => {
        if (generationStatus.textContent.startsWith('') || generationStatus.textContent.startsWith('')) {
            generationStatus.textContent = '';
        }
    }, 5000);
}
export async function reindexProject() {
    if (!state.currentProject) {
        alert('Сначала выберите проект');
        return;
    }
    if (!confirm('Проиндексировать изменения базы знаний проекта (тест-кейсы, общие шаги, документы)? Неизменённые записи будут пропущены. К TestOps запрос не отправляется.'))
        return;
    generationStatus.textContent = '⏳ Переиндексация...';
    addMessage('assistant', 'Запущена переиндексация базы знаний...');
    try {
        const response = await apiFetch(`/api/project/${state.currentProject}/reindex`, {
            method: 'POST'
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка переиндексации');
        }
        const data = await response.json();
        const stats = data.stats || {};
        const categories = stats.categories || {};
        const line = (label, c) => c
            ? `${label}: проиндексировано ${c.indexed ?? 0}, без изменений ${c.skipped ?? 0}, удалено ${c.deleted ?? 0}` +
                (c.failed ? `, с ошибками ${c.failed}` : '')
            : `${label}: —`;
        generationStatus.textContent = 'Индексация завершена';
        addMessage('assistant', `Индексация завершена!\n` +
            `Проиндексировано: ${stats.indexed ?? 0}, без изменений: ${stats.skipped ?? 0}, ` +
            `удалено из индекса: ${stats.deleted ?? 0}\n` +
            `${line('Тест-кейсы', categories.examples)}\n` +
            `${line('Общие шаги', categories.shared_steps)}\n` +
            `${line('Документы', categories.docs)}`);
    }
    catch (error) {
        console.error('Ошибка:', error);
        generationStatus.textContent = 'Ошибка переиндексации';
        addMessage('assistant', `Ошибка переиндексации: ${error.message}`);
    }
    setTimeout(() => {
        if (generationStatus.textContent.startsWith('') || generationStatus.textContent.startsWith('')) {
            generationStatus.textContent = '';
        }
    }, 5000);
}
export function setMode(mode) {
    state.currentMode = mode;
    document.querySelectorAll('#modeDropdown .burger-item[data-mode]').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-mode') === mode);
    });
    const modeNames = { chat: 'Чат', generate: 'Генерация чек-листа', search: 'Поиск по базе' };
    const chip = document.getElementById('currentModeChip');
    const chipText = document.getElementById('currentModeText');
    if (chip && chipText) {
        chipText.textContent = modeNames[mode] || mode;
        chip.className = 'current-mode-chip mode-' + mode;
    }
    // Значения фильтров при смене режима не сбрасываются.
    const metaFilter = document.getElementById('metaFilter');
    const countField = document.getElementById('countField');
    if (mode === 'generate') {
        messageInput.placeholder = 'Опишите функционал для генерации чек-листа...';
        metaFilter.classList.add('visible');
        if (countField)
            countField.style.display = '';
    }
    else if (mode === 'search') {
        messageInput.placeholder = 'Опишите функционал или укажите номер тест-кейса';
        metaFilter.classList.add('visible');
        if (countField)
            countField.style.display = 'none';
    }
    else {
        messageInput.placeholder = 'Введите сообщение...';
        metaFilter.classList.remove('visible');
    }
    messageInput.focus();
}
export function toggleModeMenu(forceState) {
    const dropdown = document.getElementById('modeDropdown');
    const chip = document.getElementById('currentModeChip');
    const shouldOpen = typeof forceState === 'boolean' ? forceState : !dropdown.classList.contains('open');
    dropdown.classList.toggle('open', shouldOpen);
    chip.classList.toggle('open', shouldOpen);
}
export function setModeFromDropdown(mode) {
    setMode(mode);
    toggleModeMenu(false);
}
document.addEventListener('click', function (e) {
    const wrap = document.querySelector('.mode-selector-wrap');
    if (wrap && !wrap.contains(e.target)) {
        toggleModeMenu(false);
    }
});
export async function endSession() {
    if (!confirm('Завершить текущую сессию? Все несохранённые данные чата будут потеряны.'))
        return;
    if (state.sessionId) {
        try {
            await apiFetch('/api/session/close', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: state.sessionId })
            });
        }
        catch (error) {
            console.error('Ошибка завершения сессии:', error);
        }
    }
    sessionStorage.removeItem('checklist');
    localStorage.removeItem('lastProject');
    location.reload();
}
const metaCombosConfig = [
    { comboId: 'moduleCombo', triggerId: 'moduleTrigger', menuId: 'moduleMenu', valueId: 'moduleValue', clearId: 'moduleClear', hiddenId: 'filterX', defaultTitle: 'Модуль — необязательно' },
    { comboId: 'testTypeCombo', triggerId: 'testTypeTrigger', menuId: 'testTypeMenu', valueId: 'testTypeValue', clearId: 'testTypeClear', hiddenId: 'filterZ', defaultTitle: 'Вид тестов — необязательно' }
];
function closeAllMetaCombos(exceptComboId) {
    metaCombosConfig.forEach(cfg => {
        if (cfg.comboId === exceptComboId)
            return;
        const combo = document.getElementById(cfg.comboId);
        const trigger = document.getElementById(cfg.triggerId);
        if (combo)
            combo.classList.remove('open');
        if (trigger)
            trigger.setAttribute('aria-expanded', 'false');
    });
}
function selectMetaComboOption(cfg, value, text) {
    const combo = document.getElementById(cfg.comboId);
    const valueEl = document.getElementById(cfg.valueId);
    const hiddenInput = document.getElementById(cfg.hiddenId);
    const trigger = document.getElementById(cfg.triggerId);
    hiddenInput.value = value;
    valueEl.textContent = text;
    trigger.title = text || cfg.defaultTitle || '';
    combo.classList.toggle('filled', value !== '');
    combo.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
}
export function initMetaCombos() {
    metaCombosConfig.forEach(cfg => {
        const combo = document.getElementById(cfg.comboId);
        const trigger = document.getElementById(cfg.triggerId);
        const menu = document.getElementById(cfg.menuId);
        const clearBtnEl = document.getElementById(cfg.clearId);
        trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            const willOpen = !combo.classList.contains('open');
            closeAllMetaCombos(cfg.comboId);
            combo.classList.toggle('open', willOpen);
            trigger.setAttribute('aria-expanded', String(willOpen));
        });
        menu.querySelectorAll('.meta-combo-option').forEach(option => {
            option.addEventListener('click', function (e) {
                e.stopPropagation();
                selectMetaComboOption(cfg, this.getAttribute('data-value'), this.textContent.trim());
            });
        });
        clearBtnEl.addEventListener('click', function (e) {
            e.stopPropagation();
            selectMetaComboOption(cfg, '', '');
        });
    });
    document.addEventListener('click', function () {
        closeAllMetaCombos(null);
    });
    const countInput = document.getElementById('filterCount');
    const countField = document.getElementById('countField');
    const countClear = document.getElementById('countClear');
    function syncCountState() {
        countField.classList.toggle('filled', countInput.value !== '');
    }
    countInput.addEventListener('input', syncCountState);
    countClear.addEventListener('click', function (e) {
        e.stopPropagation();
        countInput.value = '';
        syncCountState();
    });
}
export function resetMetaFilters() {
    metaCombosConfig.forEach(cfg => selectMetaComboOption(cfg, '', ''));
    const countInput = document.getElementById('filterCount');
    const countField = document.getElementById('countField');
    countInput.value = '';
    countField.classList.remove('filled');
}
export async function checkOllamaStatus() {
    try {
        const response = await fetch('/health');
        const data = await response.json();
        statusDot.className = data.status === 'ok' ? 'status-dot' : 'status-dot offline';
    }
    catch {
        statusDot.className = 'status-dot offline';
    }
}
