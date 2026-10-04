import { state, apiFetch, escapeHtml, stripIdPrefix, readEditableText, insertLineBreakAtCaret, checklistContainer, checklistPanel, checklistCount, updateModal, updateTechSpec, extendModal, extendTechSpec, sendBtn, messageInput, typingIndicator, generationStatus, playSuccessSound, playErrorSound } from './core.js';
import { addMessage, updateImagePreview, setReuseOfferStatus, markPendingReuseOffersExpired } from './chat.js';
import { resetMetaFilters } from './projects.js';
export function newTestCaseId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}
function normalizeStepElement(step) {
    if (step === null || step === undefined)
        return null;
    if (typeof step === 'string')
        return { type: 'text', text: step };
    if (step.type === 'shared') {
        return {
            type: 'shared',
            shared_step_testops_id: step.shared_step_testops_id,
            name: step.name || '',
            text: step.text || ''
        };
    }
    return { type: 'text', text: step.text || '' };
}
function getStepsList(item) {
    if (!item)
        return null;
    if (!Array.isArray(item.steps)) {
        const text = (item.steps || '').trim();
        item.steps = text ? [{ type: 'text', text }] : [];
    }
    else if (item.steps.some(s => !s || typeof s !== 'object')) {
        item.steps = item.steps.map(normalizeStepElement).filter(Boolean);
    }
    return item.steps;
}
function cloneSteps(steps) {
    return (steps || []).map(normalizeStepElement).filter(Boolean);
}
function stepHandleHtml() {
    return `
            <span class="step-handle" title="Перетащить для изменения порядка">
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><rect y="2" width="16" height="2" rx="1"/><rect y="7" width="16" height="2" rx="1"/><rect y="12" width="16" height="2" rx="1"/></svg>
            </span>`;
}
function stepRowHtml(itemId, index, step, isEditor, readOnly = false) {
    const idAttr = isEditor ? '' : ` data-id="${itemId}"`;
    const handle = readOnly ? '' : stepHandleHtml();
    const removeBtn = readOnly ? '' : `<button class="step-remove-btn"${idAttr} data-index="${index}" title="Удалить шаг">×</button>`;
    if (step.type === 'shared') {
        const name = escapeHtml(step.name || (step.shared_step_testops_id ? `#${step.shared_step_testops_id}` : ''));
        return `
        <div class="step-row step-row-shared" data-index="${index}">
            ${handle}
            <span class="step-number">${index + 1}.</span>
            <div class="step-shared-body">
                <div class="step-shared-head">
                    <span class="step-shared-badge">Общий шаг</span>
                    <span class="step-shared-name">${name}</span>
                </div>
                ${step.text ? `<div class="step-shared-text">${escapeHtml(step.text)}</div>` : ''}
            </div>
            ${removeBtn}
        </div>
    `;
    }
    const editable = readOnly ? '' : ` contenteditable="true"`;
    return `
        <div class="step-row" data-index="${index}">
            ${handle}
            <span class="step-number">${index + 1}.</span>
            <div class="step-row-text${readOnly ? ' step-row-readonly' : ''}"${editable}${idAttr} data-index="${index}">${escapeHtml(step.text || '')}</div>
            ${removeBtn}
        </div>
    `;
}
function stepsListBlockHtml(itemId, stepsList, isEditor, readOnly = false) {
    const idAttr = isEditor ? ` id="editorStepsList"` : ` data-id="${itemId}"`;
    const rows = stepsList.length
        ? stepsList.map((step, i) => stepRowHtml(itemId, i, step, isEditor, readOnly)).join('')
        : `<div class="steps-list-empty">Шаги не добавлены</div>`;
    const addIdAttr = isEditor ? '' : ` data-id="${itemId}"`;
    const addBtn = readOnly ? '' : `<button class="step-add-btn"${addIdAttr}>+ Добавить шаг</button>`;
    return `
        <div class="steps-list${readOnly ? ' steps-list-readonly' : ''}"${readOnly ? '' : idAttr}>
            ${rows}
            ${addBtn}
        </div>
    `;
}
function getStepsContainer(itemId, isEditor) {
    const selector = isEditor ? '#editorStepsList' : `.steps-list[data-id="${itemId}"]`;
    return document.querySelector(selector);
}
function rerenderStepsBlock(itemId, stepsList, isEditor) {
    const container = getStepsContainer(itemId, isEditor);
    if (container) {
        container.outerHTML = stepsListBlockHtml(itemId, stepsList, isEditor);
    }
}
function findItem(itemId) {
    return state.checklistItems.find(i => String(i.id) === String(itemId));
}
function syncLocalStepsOnly(itemId, isEditor) {
    if (isEditor)
        return;
    if (!findItem(itemId))
        return;
    saveChecklistState();
}
function persistStepsChange(itemId, isEditor) {
    if (isEditor)
        return;
    if (!findItem(itemId))
        return;
    saveChecklistState();
    saveChecklistToSession(state.checklistItems).then(() => {
        displayChecklist(state.checklistItems);
    }).catch(() => { });
}
function stepsListFor(itemId, isEditor) {
    return isEditor ? state.editorStepsList : getStepsList(findItem(itemId));
}
function addStep(itemId, isEditor) {
    const stepsList = stepsListFor(itemId, isEditor);
    if (!stepsList)
        return;
    stepsList.push({ type: 'text', text: '' });
    rerenderStepsBlock(itemId, stepsList, isEditor);
    syncLocalStepsOnly(itemId, isEditor);
    const container = getStepsContainer(itemId, isEditor);
    const rows = container ? container.querySelectorAll('.step-row-text') : [];
    if (rows.length) {
        const lastRow = rows[rows.length - 1];
        lastRow.focus();
    }
}
function removeStep(itemId, index, isEditor) {
    const stepsList = stepsListFor(itemId, isEditor);
    if (!stepsList || index < 0 || index >= stepsList.length)
        return;
    stepsList.splice(index, 1);
    rerenderStepsBlock(itemId, stepsList, isEditor);
    persistStepsChange(itemId, isEditor);
}
function updateStepText(itemId, index, text, isEditor) {
    const stepsList = stepsListFor(itemId, isEditor);
    if (!stepsList || index < 0 || index >= stepsList.length)
        return;
    const step = stepsList[index];
    if (!step || step.type !== 'text')
        return;
    const trimmed = (text || '').trim();
    if (step.text === trimmed)
        return;
    step.text = trimmed;
    persistStepsChange(itemId, isEditor);
}
let stepsDrag = null;
function onStepHandlePointerDown(e) {
    const handle = e.target.closest('.step-handle');
    if (!handle)
        return;
    const row = handle.closest('.step-row');
    const container = handle.closest('.steps-list');
    if (!row || !container || container.classList.contains('steps-list-readonly'))
        return;
    e.preventDefault();
    const isEditor = container.id === 'editorStepsList';
    const itemId = isEditor ? state.editingItemId : container.getAttribute('data-id');
    const fromIndex = parseInt(row.getAttribute('data-index'), 10);
    stepsDrag = { itemId, isEditor, fromIndex, currentIndex: fromIndex };
    row.classList.add('step-row-dragging');
    document.addEventListener('pointermove', onStepDragMove);
    document.addEventListener('pointerup', onStepDragEnd);
    document.addEventListener('pointercancel', onStepDragEnd);
}
function onStepDragMove(e) {
    if (!stepsDrag)
        return;
    const container = getStepsContainer(stepsDrag.itemId, stepsDrag.isEditor);
    if (!container)
        return;
    const rows = Array.from(container.querySelectorAll('.step-row'));
    const draggingRow = container.querySelector('.step-row-dragging');
    if (!draggingRow || !rows.length)
        return;
    let targetIndex = stepsDrag.currentIndex;
    for (let i = 0; i < rows.length; i++) {
        if (rows[i] === draggingRow)
            continue;
        const rect = rows[i].getBoundingClientRect();
        const midY = rect.top + rect.height / 2;
        const rowIndex = parseInt(rows[i].getAttribute('data-index'), 10);
        if (e.clientY < midY && rowIndex < stepsDrag.currentIndex) {
            targetIndex = rowIndex;
            break;
        }
        if (e.clientY > midY && rowIndex > stepsDrag.currentIndex) {
            targetIndex = rowIndex;
        }
    }
    if (targetIndex !== stepsDrag.currentIndex) {
        const stepsList = stepsListFor(stepsDrag.itemId, stepsDrag.isEditor);
        const [moved] = stepsList.splice(stepsDrag.currentIndex, 1);
        stepsList.splice(targetIndex, 0, moved);
        stepsDrag.currentIndex = targetIndex;
        rerenderStepsBlock(stepsDrag.itemId, stepsList, stepsDrag.isEditor);
        const freshContainer = getStepsContainer(stepsDrag.itemId, stepsDrag.isEditor);
        const newDraggingRow = freshContainer && freshContainer.querySelector(`.step-row[data-index="${targetIndex}"]`);
        if (newDraggingRow)
            newDraggingRow.classList.add('step-row-dragging');
    }
}
function onStepDragEnd() {
    if (!stepsDrag)
        return;
    const container = getStepsContainer(stepsDrag.itemId, stepsDrag.isEditor);
    const draggingRow = container && container.querySelector('.step-row-dragging');
    if (draggingRow)
        draggingRow.classList.remove('step-row-dragging');
    document.removeEventListener('pointermove', onStepDragMove);
    document.removeEventListener('pointerup', onStepDragEnd);
    document.removeEventListener('pointercancel', onStepDragEnd);
    persistStepsChange(stepsDrag.itemId, stepsDrag.isEditor);
    stepsDrag = null;
}
document.addEventListener('pointerdown', onStepHandlePointerDown);
export async function generateChecklistFromChat(techSpec, images, x = null, z = null, count = null) {
    if (!state.isProjectLoaded || !state.sessionId) {
        addMessage('assistant', 'Сначала выберите проект из списка');
        return;
    }
    if (state.isGenerating) {
        addMessage('assistant', '⏳ Пожалуйста, дождитесь завершения текущей генерации.');
        return;
    }
    if (!images) {
        images = state.selectedImages.slice();
        state.selectedImages = [];
        state.selectedImageFiles = [];
        updateImagePreview();
    }
    const cleanTechSpec = techSpec;
    markPendingReuseOffersExpired();
    sendBtn.disabled = true;
    state.isProcessing = true;
    state.isGenerating = true;
    typingIndicator.classList.add('active');
    generationStatus.textContent = '⏳ Генерация чек-листа...';
    try {
        const response = await apiFetch('/api/generate-checklist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: state.currentProject,
                session_id: state.sessionId,
                tech_spec: cleanTechSpec,
                temperature: 0.3,
                x: x,
                z: z,
                count: count,
                images: images
            })
        });
        if (!response.ok)
            throw new Error('Ошибка генерации чек-листа');
        const data = await response.json();
        if (data.error)
            throw new Error(data.error);
        if (data.reuse_offer) {
            showReuseOffer(data);
            typingIndicator.classList.remove('active');
            generationStatus.textContent = 'Найдено в базе';
            playSuccessSound();
            return;
        }
        await applyGenerationResult(data);
    }
    catch (error) {
        console.error('Ошибка:', error);
        typingIndicator.classList.remove('active');
        generationStatus.textContent = 'Ошибка';
        addMessage('assistant', `Ошибка генерации: ${error.message}`);
        if (error instanceof SyntaxError)
            playErrorSound();
    }
    finally {
        finishGeneration();
    }
}
function finishGeneration() {
    state.isGenerating = false;
    state.isProcessing = false;
    sendBtn.disabled = false;
    messageInput.focus();
    setTimeout(() => {
        if (generationStatus.textContent === 'Чек-лист готов' ||
            generationStatus.textContent === 'Найдено в базе' ||
            generationStatus.textContent === 'Ошибка') {
            generationStatus.textContent = '';
        }
    }, 5000);
}
async function applyGenerationResult(data) {
    state.checklistItems = data.test_cases || [];
    resetExtendState();
    state.chatHasChecklist = true;
    state.previewItems = [];
    const reused = data.reused_existing === true || data.panel_mode === 'search';
    state.searchHasMore = false;
    state.searchShown = 0;
    showPanelMode(reused ? 'search' : 'checklist', { reused });
    displayChecklist(state.checklistItems);
    saveChecklistState();
    await saveChecklistToSession(state.checklistItems);
    typingIndicator.classList.remove('active');
    generationStatus.textContent = reused ? 'Найдено в базе' : 'Чек-лист готов';
    addMessage('assistant', data.chat_summary || (reused
        ? `В базе уже есть подходящие тест-кейсы: ${state.checklistItems.length}. Генерация не запускалась.`
        : `Чек-лист сгенерирован! Найдено ${state.checklistItems.length} тест-кейсов.`));
    toggleChecklistPanel(true);
    resetMetaFilters();
    playSuccessSound();
}
function showReuseOffer(data) {
    const offer = data.reuse_offer;
    state.previewItems = offer.candidates || [];
    addMessage('assistant', data.chat_summary || '', [], {
        kind: 'reuse_offer',
        id: offer.message_id,
        payload: { status: offer.status || 'pending' }
    });
    showPanelMode('preview');
    displayPreview(state.previewItems);
    toggleChecklistPanel(true);
}
function setOfferButtonsBusy(messageEl, busy) {
    if (!messageEl)
        return;
    messageEl.querySelectorAll('.reuse-offer-btn').forEach(btn => { btn.disabled = busy; });
}
export async function acceptReuseOffer(messageId, messageEl) {
    if (state.isProcessing)
        return;
    setOfferButtonsBusy(messageEl, true);
    state.isProcessing = true;
    sendBtn.disabled = true;
    generationStatus.textContent = '⏳ Открываю найденные кейсы...';
    try {
        const response = await apiFetch(`/api/reuse-offer/${messageId}/accept`, { method: 'POST' });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            if (response.status === 409)
                setReuseOfferStatus(messageEl, 'expired');
            throw new Error(err.detail || 'Не удалось открыть найденные кейсы');
        }
        const data = await response.json();
        setReuseOfferStatus(messageEl, 'accepted');
        state.previewItems = [];
        state.checklistItems = data.test_cases || [];
        state.chatHasChecklist = state.checklistItems.length > 0;
        state.searchHasMore = false;
        state.searchShown = 0;
        resetExtendState();
        showPanelMode('search', { reused: true });
        displayChecklist(state.checklistItems);
        saveChecklistState();
        toggleChecklistPanel(true);
        generationStatus.textContent = '';
    }
    catch (error) {
        console.error('Ошибка:', error);
        generationStatus.textContent = 'Ошибка';
        addMessage('assistant', `Ошибка: ${error.message}`);
        if (messageEl && messageEl.dataset.status === 'pending')
            setOfferButtonsBusy(messageEl, false);
    }
    state.isProcessing = false;
    sendBtn.disabled = false;
}
export async function declineReuseOffer(messageId, messageEl) {
    if (state.isProcessing || state.isGenerating)
        return;
    setOfferButtonsBusy(messageEl, true);
    setReuseOfferStatus(messageEl, 'declined');
    state.previewItems = [];
    state.checklistItems = [];
    showPanelMode('checklist');
    displayChecklist([]);
    sendBtn.disabled = true;
    state.isProcessing = true;
    state.isGenerating = true;
    typingIndicator.classList.add('active');
    generationStatus.textContent = '⏳ Генерация чек-листа...';
    try {
        const response = await apiFetch(`/api/reuse-offer/${messageId}/decline`, { method: 'POST' });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            if (response.status === 409)
                setReuseOfferStatus(messageEl, 'expired');
            throw new Error(err.detail || 'Ошибка генерации чек-листа');
        }
        const data = await response.json();
        if (data.error)
            throw new Error(data.error);
        await applyGenerationResult(data);
    }
    catch (error) {
        console.error('Ошибка:', error);
        typingIndicator.classList.remove('active');
        generationStatus.textContent = 'Ошибка';
        addMessage('assistant', `Ошибка генерации: ${error.message}`);
    }
    finally {
        finishGeneration();
    }
}
export async function updateChecklistFromChat(techSpec) {
    if (state.checklistItems.length === 0) {
        addMessage('assistant', 'Чек-лист пуст. Сначала сгенерируйте его в режиме «Генерация чек-листа».');
        return;
    }
    const isSearch = state.panelMode === 'search';
    const selected = state.checklistItems.filter(item => item.selected === true);
    if (selected.length === 0) {
        addMessage('assistant', 'Отметьте галочками тест-кейсы, которые нужно обновить.');
        return;
    }
    const selectedIds = selected.map(item => String(item.id));
    const frozenCount = state.checklistItems.length - selected.length;
    const proceed = confirm(`Обновить отмеченные тест-кейсы по новым требованиям?\n\n` +
        `Отмечено (будет обновлено): ${selected.length}\n` +
        `Не отмечено (останется без изменений): ${frozenCount}\n\n` +
        `Модель может править, объединять и разбивать отмеченные кейсы.` +
        (isSearch ? ' Новые кейсы в этом режиме не создаются.' : '') +
        `\nОбъединение и разбиение создадут в TestOps новые кейсы; исходные там останутся.`);
    if (!proceed)
        return;
    sendBtn.disabled = true;
    state.isProcessing = true;
    typingIndicator.classList.add('active');
    generationStatus.textContent = '⏳ Обновление чек-листа...';
    try {
        const response = await apiFetch('/api/update-checklist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                project_id: state.currentProject,
                items: state.checklistItems,
                tech_spec: techSpec,
                selected_ids: selectedIds,
                allow_new: !isSearch
            })
        });
        if (!response.ok) {
            let detail = 'Ошибка обновления чек-листа';
            try {
                const err = await response.json();
                detail = err.detail || detail;
            }
            catch { }
            throw new Error(detail);
        }
        const data = await response.json();
        if (data.error)
            throw new Error(data.error);
        state.checklistItems = data.test_cases || [];
        resetExtendState();
        displayChecklist(state.checklistItems);
        saveChecklistState();
        await saveChecklistToSession(state.checklistItems);
        typingIndicator.classList.remove('active');
        generationStatus.textContent = 'Чек-лист обновлен';
        const pendingExport = state.checklistItems.filter(i => i.needs_export).length;
        addMessage('assistant', `Обновлено. Всего тест-кейсов в списке: ${state.checklistItems.length}, ` +
            `ожидают отправки в TestOps: ${pendingExport}.`);
        playSuccessSound();
    }
    catch (error) {
        console.error('Ошибка:', error);
        typingIndicator.classList.remove('active');
        generationStatus.textContent = 'Ошибка';
        addMessage('assistant', `Ошибка обновления: ${error.message}`);
        if (error instanceof SyntaxError)
            playErrorSound();
    }
    state.isProcessing = false;
    sendBtn.disabled = false;
    messageInput.focus();
    setTimeout(() => {
        if (generationStatus.textContent === 'Чек-лист обновлен' ||
            generationStatus.textContent === 'Ошибка') {
            generationStatus.textContent = '';
        }
    }, 5000);
}
export async function extendChecklist(techSpec, count = null, x = null, z = null) {
    if (state.checklistItems.length === 0) {
        addMessage('assistant', 'Чек-лист пуст. Сначала сгенерируйте его в режиме «Генерация чек-листа».');
        return;
    }
    if (state.isProcessing) {
        addMessage('assistant', '⏳ Пожалуйста, дождитесь завершения текущей операции.');
        return;
    }
    const snapshot = JSON.parse(JSON.stringify(state.checklistItems));
    sendBtn.disabled = true;
    state.isProcessing = true;
    typingIndicator.classList.add('active');
    generationStatus.textContent = '⏳ Дополнение чек-листа...';
    try {
        const response = await apiFetch('/api/extend-checklist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                project_id: state.currentProject,
                tech_spec: techSpec,
                items: state.checklistItems,
                x: x,
                z: z,
                count: count
            })
        });
        if (!response.ok) {
            let detail = 'Ошибка дополнения чек-листа';
            try {
                const err = await response.json();
                detail = err.detail || detail;
            }
            catch { }
            throw new Error(detail);
        }
        const data = await response.json();
        if (data.error)
            throw new Error(data.error);
        const addedIds = data.added_ids || [];
        state.checklistItems = data.test_cases || [];
        state.newItemIds = addedIds.map(String);
        state.checklistSnapshot = addedIds.length ? snapshot : null;
        showPanelMode('checklist');
        displayChecklist(state.checklistItems);
        saveChecklistState();
        await saveChecklistToSession(state.checklistItems);
        updateUndoExtendButton();
        scrollToFirstNewItem();
        typingIndicator.classList.remove('active');
        generationStatus.textContent = addedIds.length ? 'Чек-лист дополнен' : 'Ничего не добавлено';
        addMessage('assistant', data.chat_summary ||
            `Добавлено тест-кейсов: ${addedIds.length}. Всего: ${state.checklistItems.length}.`);
        toggleChecklistPanel(true);
        if (addedIds.length)
            playSuccessSound();
    }
    catch (error) {
        console.error('Ошибка дополнения:', error);
        typingIndicator.classList.remove('active');
        generationStatus.textContent = 'Ошибка';
        addMessage('assistant', `Ошибка дополнения: ${error.message}`);
        if (error instanceof SyntaxError)
            playErrorSound();
    }
    state.isProcessing = false;
    sendBtn.disabled = false;
    messageInput.focus();
    setTimeout(() => {
        if (generationStatus.textContent === 'Чек-лист дополнен' ||
            generationStatus.textContent === 'Ничего не добавлено' ||
            generationStatus.textContent === 'Ошибка') {
            generationStatus.textContent = '';
        }
    }, 5000);
}
function scrollToFirstNewItem() {
    if (!state.newItemIds.length)
        return;
    const el = checklistContainer.querySelector(`.checklist-item[data-id="${state.newItemIds[0]}"]`);
    if (el)
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function resetExtendState() {
    state.newItemIds = [];
    state.checklistSnapshot = null;
    updateUndoExtendButton();
}
function updateUndoExtendButton() {
    const btn = document.getElementById('undoExtendBtn');
    if (btn)
        btn.style.display = state.checklistSnapshot ? 'inline-block' : 'none';
}
export function undoExtend() {
    if (!state.checklistSnapshot)
        return;
    const removed = state.checklistItems.length - state.checklistSnapshot.length;
    state.checklistItems = state.checklistSnapshot;
    state.checklistSnapshot = null;
    state.newItemIds = [];
    displayChecklist(state.checklistItems);
    saveChecklistState();
    saveChecklistToSession(state.checklistItems);
    updateUndoExtendButton();
    addMessage('assistant', `Дополнение отменено, убрано тест-кейсов: ${removed}.`);
}
export function addEmptyChecklistItem() {
    const newId = newTestCaseId();
    state.checklistItems.push({
        id: newId,
        title: '',
        steps: [],
        expected_result: '',
        selected: false,
        status: 'Готово к автоматизации',
        origin: 'generated',
        testops_id: null,
        needs_export: true,
        edited_since_search: false
    });
    state.newItemIds = [String(newId)];
    displayChecklist(state.checklistItems);
    saveChecklistState();
    saveChecklistToSession(state.checklistItems);
    const titleEl = checklistContainer.querySelector(`.item-title[data-id="${newId}"]`);
    if (titleEl) {
        titleEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        titleEl.focus();
    }
}
export async function searchTestCases(query, limit = null) {
    if (!state.isProjectLoaded || !state.sessionId) {
        addMessage('assistant', 'Сначала выберите проект из списка');
        return;
    }
    sendBtn.disabled = true;
    state.isProcessing = true;
    typingIndicator.classList.add('active');
    generationStatus.textContent = limit ? '⏳ Догружаю результаты...' : '⏳ Поиск по базе...';
    if (!limit) {
        // Фильтры фиксируются в момент запроса: «Показать ещё» догружает с теми же.
        const xRaw = document.getElementById('filterX').value;
        const zRaw = document.getElementById('filterZ').value;
        state.searchFilters = {
            x: xRaw !== '' ? parseInt(xRaw, 10) : null,
            z: zRaw !== '' ? parseInt(zRaw, 10) : null
        };
        markPendingReuseOffersExpired();
    }
    const { x, z } = state.searchFilters || { x: null, z: null };
    try {
        const response = await apiFetch('/api/search-testcases', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: state.currentProject,
                session_id: state.sessionId,
                query: query,
                x: x,
                z: z,
                limit: limit
            })
        });
        if (!response.ok) {
            let detail = 'Ошибка поиска по базе';
            try {
                const err = await response.json();
                detail = err.detail || detail;
            }
            catch { }
            throw new Error(detail);
        }
        const data = await response.json();
        if (data.error)
            throw new Error(data.error);
        state.lastSearchQuery = query;
        state.previewItems = [];
        showPanelMode('search');
        renderSearchResults(data);
        displayChecklist(state.checklistItems);
        toggleChecklistPanel(true);
        if (state.checklistItems.length > 0) {
            state.chatHasChecklist = true;
        }
        typingIndicator.classList.remove('active');
        generationStatus.textContent = 'Поиск завершён';
        if (!limit) {
            const count = state.checklistItems.length;
            addMessage('assistant', data.chat_summary || `Найдено похожих тест-кейсов: ${count}.`);
        }
        playSuccessSound();
    }
    catch (error) {
        console.error('Ошибка поиска:', error);
        typingIndicator.classList.remove('active');
        generationStatus.textContent = 'Ошибка поиска';
        addMessage('assistant', `Ошибка поиска: ${error.message}`);
        if (error instanceof SyntaxError)
            playErrorSound();
    }
    state.isProcessing = false;
    sendBtn.disabled = false;
    messageInput.focus();
    setTimeout(() => {
        if (generationStatus.textContent === 'Поиск завершён' || generationStatus.textContent === 'Ошибка поиска') {
            generationStatus.textContent = '';
        }
    }, 5000);
}
function renderSearchResults(data) {
    state.checklistItems = data.test_cases || [];
    const searchCount = document.getElementById('searchCount');
    if (searchCount)
        searchCount.textContent = state.checklistItems.length;
    state.searchHasMore = Boolean(data.has_more);
    state.searchShown = state.checklistItems.length;
    renderSearchMore(data);
    saveChecklistState();
}
function renderSearchMore(data) {
    const box = document.getElementById('searchMore');
    const info = document.getElementById('searchMoreInfo');
    if (!box || !info)
        return;
    if (!data.has_more) {
        box.style.display = 'none';
        return;
    }
    box.style.display = '';
    info.textContent = `Показаны ${state.searchShown} из ${data.total_found}`;
}
export async function showMoreSearchResults() {
    if (!state.searchHasMore || state.isProcessing)
        return;
    await searchTestCases(state.lastSearchQuery, state.searchShown + 50);
}
export function clearSearchResults() {
    document.getElementById('searchResultsContainer').innerHTML = '';
    const searchCount = document.getElementById('searchCount');
    if (searchCount)
        searchCount.textContent = '0';
    const more = document.getElementById('searchMore');
    if (more)
        more.style.display = 'none';
    state.searchHasMore = false;
    state.searchShown = 0;
}
export function clearSearchChecklist() {
    if (state.checklistItems.length === 0)
        return;
    const edited = state.checklistItems.filter(i => i.edited_since_search).length;
    const warning = edited
        ? `\n\nСреди них изменённых вручную: ${edited}. Правки будут потеряны безвозвратно.`
        : '';
    const what = state.panelReused ? 'все найденные в базе тест-кейсы' : 'все результаты поиска';
    if (!confirm(`Убрать ${what} (${state.checklistItems.length})?${warning}`))
        return;
    state.checklistItems = [];
    clearSearchResults();
    saveChecklistState();
    saveChecklistToSession(state.checklistItems);
}
export function selectAllSearchResults() {
    if (state.checklistItems.length === 0)
        return;
    const allSelected = state.checklistItems.every(item => item.selected === true);
    state.checklistItems.forEach(item => { item.selected = !allSelected; });
    displayChecklist(state.checklistItems);
    saveChecklistState();
    saveChecklistToSession(state.checklistItems);
}
const REUSED_PANEL_TITLE = 'Найдено в базе';
const SEARCH_PANEL_TITLE = 'Результаты поиска';
export function showPanelMode(mode, options = {}) {
    state.panelMode = mode;
    const isSearch = mode === 'search';
    const isPreview = mode === 'preview';
    const isChecklist = !isSearch && !isPreview;
    state.panelReused = isSearch && options.reused === true;
    if (!isPreview)
        state.previewItems = [];
    const searchTitleText = document.getElementById('searchTitleText');
    if (searchTitleText) {
        searchTitleText.textContent = state.panelReused ? REUSED_PANEL_TITLE : SEARCH_PANEL_TITLE;
    }
    const show = (id, visible, display = 'block') => {
        const el = document.getElementById(id);
        if (el)
            el.style.display = visible ? display : 'none';
    };
    show('checklistContainer', isChecklist);
    show('searchResultsContainer', isSearch);
    show('previewContainer', isPreview);
    show('previewHint', isPreview);
    show('checklistActionsBar', isChecklist, 'flex');
    show('searchActionsBar', isSearch, 'flex');
    show('checklistTitle', isChecklist, 'flex');
    show('searchTitle', isSearch, 'flex');
    show('previewTitle', isPreview, 'flex');
    const more = document.getElementById('searchMore');
    if (more)
        more.style.display = (isSearch && state.searchHasMore) ? '' : 'none';
    if (checklistPanel)
        checklistPanel.classList.toggle('preview-mode', isPreview);
}
export function displayPreview(items) {
    state.previewItems = items || [];
    const container = document.getElementById('previewContainer');
    const counter = document.getElementById('previewCount');
    if (!container)
        return;
    if (counter)
        counter.textContent = state.previewItems.length;
    if (!state.previewItems.length) {
        container.innerHTML = `<div class="checklist-empty"><p>Найденных кейсов нет</p></div>`;
        return;
    }
    container.innerHTML = state.previewItems.map((item, index) => {
        const title = escapeHtml(stripIdPrefix(item.title) || 'Без названия');
        const expected = escapeHtml(item.expected_result || '');
        return `
            <div class="checklist-item search-variant preview-item" data-preview-index="${index}">
                <div class="item-header">
                    <div class="item-content">
                        <div class="item-title-row">
                            <span class="item-id">#${index + 1}</span>
                            <div class="item-title item-readonly">${title}</div>
                        </div>
                        ${renderStatusRow(item)}
                        <div class="item-steps-label">Шаги:</div>
                        ${stepsListBlockHtml(null, getStepsList(item), false, true)}
                        <div class="item-expected item-readonly">${expected}</div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}
export function displayChecklist(items) {
    state.checklistItems = items || [];
    const isSearch = state.panelMode === 'search';
    const container = isSearch ? document.getElementById('searchResultsContainer') : checklistContainer;
    const counter = isSearch ? document.getElementById('searchCount') : checklistCount;
    if (!state.checklistItems || state.checklistItems.length === 0) {
        container.innerHTML = isSearch && state.panelReused
            ? `<div class="checklist-empty">
                   <p>Список пуст</p>
                   <p style="font-size: 13px; margin-top: 8px;">Все найденные в базе тест-кейсы убраны из списка.</p>
               </div>`
            : isSearch
                ? `<div class="checklist-empty">
                   <p>Ничего не найдено</p>
                   <p style="font-size: 13px; margin-top: 8px;">В базе нет тест-кейсов, достаточно близких к запросу. Переформулируйте его или снимите кавычки, если они были.</p>
               </div>`
                : `<div class="checklist-empty"><p>Чек-лист пуст</p></div>`;
        if (counter)
            counter.textContent = '0';
        return;
    }
    let html = '';
    state.checklistItems.forEach((item, index) => {
        const itemId = item.id || (item.id = newTestCaseId());
        const displayNumber = index + 1;
        const isChecked = item.selected === true;
        const title = escapeHtml(stripIdPrefix(item.title) || '');
        const stepsListHtml = stepsListBlockHtml(itemId, getStepsList(item), false);
        const expected = escapeHtml(item.expected_result || '');
        const isNew = state.newItemIds.includes(String(itemId));
        html += `
            <div class="checklist-item ${isChecked ? 'selected' : ''} ${isNew ? 'new-item' : ''} ${isSearch ? 'search-variant' : ''}" data-id="${itemId}">
                <div class="item-header">
                    <input type="checkbox"
                           class="item-checkbox item-checkbox-select"
                           ${isChecked ? 'checked' : ''}
                           title="Взять тест-кейс в работу: обновление моделью и экспорт в TestOps"
                           data-id="${itemId}">
                    <div class="item-content">
                        <div class="item-title-row">
                            <span class="item-id">#${displayNumber}</span>
                            <div class="item-title" contenteditable="true"
                                 data-id="${itemId}"
                                 data-field="title">${title}</div>
                        </div>
                        ${renderStatusRow(item)}
                        <div class="item-steps-label">Шаги:</div>
                        ${stepsListHtml}
                        <div class="item-expected" contenteditable="true"
                             data-id="${itemId}"
                             data-field="expected_result">${expected}</div>
                        <div class="item-actions">
                            <button class="delete-btn" data-id="${itemId}">${isSearch ? 'Убрать' : 'Удалить'}</button>
                        </div>
                    </div>
                    <button class="item-edit-btn" data-id="${itemId}" title="Открыть тест-кейс в полноэкранном редакторе">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path>
                        </svg>
                    </button>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
    if (counter)
        counter.textContent = state.checklistItems.length;
    saveChecklistState();
}
function renderStatusRow(item) {
    const badges = [];
    if (item.testops_id) {
        badges.push(item.needs_export
            ? `<span class="item-badge item-badge-pending">изменён после синхронизации</span>`
            : `<span class="item-badge item-badge-synced">синхронизирован</span>`);
        badges.push(`<span class="item-origin-testops">TestOps #${escapeHtml(String(item.testops_id))}</span>`);
    }
    else {
        badges.push(`<span class="item-badge item-badge-new">не отправлен в TestOps</span>`);
    }
    if (item.edited_since_search) {
        badges.push(`<span class="item-badge item-badge-edited">изменён</span>`);
    }
    else {
        if (item.match_type === 'exact') {
            badges.push(`<span class="item-badge item-badge-exact">точное совпадение</span>`);
        }
        else if (item.match_type === 'lemma') {
            badges.push(`<span class="item-badge item-badge-lemma">по словоформам</span>`);
        }
        if (item.score !== undefined && item.score !== null) {
            const pct = Math.round(Math.min(item.score, 1) * 100);
            badges.push(`<span class="item-badge">${pct}% совпадение</span>`);
        }
    }
    const source = item.source ? escapeHtml(item.source) : '';
    const sourceEl = source
        ? `<span class="item-origin-source" title="${source}">${source}</span>`
        : '';
    return `<div class="item-origin">${sourceEl}${badges.join('')}</div>`;
}
export function toggleChecklistItem(id) {
    const item = state.checklistItems.find(i => String(i.id) === String(id));
    if (item) {
        item.selected = !item.selected;
        displayChecklist(state.checklistItems);
        saveChecklistState();
        saveChecklistToSession(state.checklistItems);
    }
    else {
        console.error('Элемент с id', id, 'не найден');
        console.log('Доступные ID:', state.checklistItems.map(i => i.id));
    }
}
export function updateChecklistItem(id, field, value) {
    console.log(` updateChecklistItem: id=${id}, field=${field}, value="${value}"`);
    const item = state.checklistItems.find(i => String(i.id) === String(id));
    if (item) {
        let cleanValue = value.trim();
        if (field === 'steps') {
            cleanValue = cleanValue.replace(/^\s*/, '').trim();
        }
        if (field === 'expected_result') {
            cleanValue = cleanValue.replace(/^\s*/, '').trim();
        }
        if (field === 'title') {
            cleanValue = stripIdPrefix(cleanValue);
        }
        const newValue = cleanValue || value.trim();
        const changed = newValue !== (item[field] || '');
        item[field] = newValue;
        saveChecklistState();
        saveChecklistToSession(state.checklistItems).then(() => {
            if (changed)
                displayChecklist(state.checklistItems);
        }).catch(() => { });
        console.log(`Обновлено поле "${field}" для тест-кейса "${item.title}"`);
    }
    else {
        console.error('Элемент с id', id, 'не найден');
    }
}
export function deleteChecklistItem(id) {
    console.log(' deleteChecklistItem вызван с id:', id);
    console.log('Тип id:', typeof id, 'Значение:', id);
    console.log('Текущий checklistItems:', JSON.stringify(state.checklistItems.map(i => i.id)));
    const itemToDelete = state.checklistItems.find(i => String(i.id) === String(id));
    if (!itemToDelete) {
        console.error('Элемент с id', id, 'не найден');
        console.log('Доступные ID:', state.checklistItems.map(i => i.id));
        alert('Ошибка: тест-кейс не найден');
        return;
    }
    const title = itemToDelete.title || 'Без названия';
    const question = itemToDelete.testops_id
        ? `Убрать тест-кейс "${title}" из списка?\n\nВ TestOps кейс #${itemToDelete.testops_id} останется без изменений — отсюда он только исчезнет с экрана.`
        : state.panelMode === 'search'
            ? `Убрать тест-кейс "${title}" из результатов поиска?\n\nВ базе знаний он останется без изменений.`
            : `Удалить тест-кейс "${title}" из чек-листа?`;
    if (!confirm(question))
        return;
    const index = state.checklistItems.findIndex(i => String(i.id) === String(id));
    if (index !== -1) {
        state.checklistItems.splice(index, 1);
        console.log(`Удалён тест-кейс "${title}" (id: ${id})`);
        displayChecklist(state.checklistItems);
        saveChecklistState();
        saveChecklistToSession(state.checklistItems).then(() => {
            console.log('Чек-лист синхронизирован с бэкендом');
        }).catch(err => {
            console.warn('Ошибка синхронизации с бэкендом:', err);
        });
        addMessage('assistant', state.panelMode === 'search'
            ? `Тест-кейс "${title}" убран из результатов. Осталось ${state.checklistItems.length}.`
            : `Тест-кейс "${title}" удалён. Осталось ${state.checklistItems.length} тест-кейсов.`);
    }
    else {
        console.error('Не удалось найти индекс для удаления');
    }
}
export function selectAllChecklist() {
    if (state.checklistItems.length === 0) {
        alert('Чек-лист пуст');
        return;
    }
    const allSelected = state.checklistItems.every(item => item.selected === true);
    state.checklistItems.forEach(item => { item.selected = !allSelected; });
    displayChecklist(state.checklistItems);
    saveChecklistState();
    saveChecklistToSession(state.checklistItems);
}
export function clearChecklist() {
    if (state.checklistItems.length === 0)
        return;
    if (confirm('Очистить весь чек-лист?')) {
        state.checklistItems = [];
        resetExtendState();
        displayChecklist(state.checklistItems);
        saveChecklistState();
        saveChecklistToSession(state.checklistItems);
    }
}
export function saveChecklistState() {
    try {
        sessionStorage.setItem('checklist', JSON.stringify(state.checklistItems));
    }
    catch (e) {
        console.error('Ошибка сохранения:', e);
    }
}
export function loadChecklistState() {
    try {
        const saved = sessionStorage.getItem('checklist');
        if (saved) {
            const items = JSON.parse(saved);
            if (items && items.length > 0) {
                state.checklistItems = items;
                displayChecklist(state.checklistItems);
                toggleChecklistPanel(true);
            }
        }
    }
    catch (e) {
        console.error('Ошибка загрузки:', e);
    }
}
export async function saveChecklistToSession(items) {
    try {
        const response = await apiFetch('/api/session/checklist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.sessionId, checklist: items })
        });
        if (!response.ok) {
            console.warn('Не удалось сохранить чек-лист в сессию:', response.status);
            return response;
        }
        const data = await response.json();
        if (data && Array.isArray(data.test_cases)) {
            state.checklistItems = data.test_cases;
            saveChecklistState();
        }
        return response;
    }
    catch (e) {
        console.warn('Ошибка сохранения чек-листа в сессию:', e);
        throw e;
    }
}
export function toggleChecklistPanel(show) {
    if (typeof show === 'boolean') {
        if (show)
            checklistPanel.classList.remove('hidden');
        else
            checklistPanel.classList.add('hidden');
    }
    else {
        checklistPanel.classList.toggle('hidden');
    }
}
export function openUpdateModal() {
    if (state.checklistItems.length === 0) {
        alert(state.panelMode === 'search'
            ? 'Результаты поиска пусты — обновлять нечего.'
            : 'Чек-лист пуст. Сначала сгенерируйте его в режиме «Генерация чек-листа».');
        return;
    }
    if (!state.checklistItems.some(i => i.selected)) {
        alert('Отметьте галочками тест-кейсы, которые нужно обновить.');
        return;
    }
    const hint = document.getElementById('updateModalHint');
    if (hint) {
        hint.innerHTML = state.panelMode === 'search'
            ? 'Опишите, что изменить в отмеченных тест-кейсах. Их можно <strong>править, объединять и разбивать</strong>. Неотмеченные останутся без изменений, новые кейсы не создаются.'
            : 'Опишите новые требования или изменения. Обновлены будут <strong>только отмеченные галочками</strong> тест-кейсы, остальные останутся как есть.';
    }
    updateModal.classList.add('active');
    updateTechSpec.value = '';
    updateTechSpec.focus();
}
export function closeUpdateModal() {
    updateModal.classList.remove('active');
}
export async function confirmUpdate() {
    const techSpec = updateTechSpec.value.trim();
    if (!techSpec) {
        alert('Пожалуйста, введите новые требования');
        return;
    }
    closeUpdateModal();
    await updateChecklistFromChat(techSpec);
}
export function openExtendModal() {
    if (state.checklistItems.length === 0) {
        alert('Чек-лист пуст. Сначала сгенерируйте его в режиме «Генерация чек-листа».');
        return;
    }
    extendModal.classList.add('active');
    extendTechSpec.value = '';
    document.getElementById('extendX').value = document.getElementById('filterX').value || '';
    document.getElementById('extendZ').value = document.getElementById('filterZ').value || '';
    document.getElementById('extendCount').value = '';
    extendTechSpec.focus();
}
export function closeExtendModal() {
    extendModal.classList.remove('active');
}
export async function confirmExtend() {
    const techSpec = extendTechSpec.value.trim();
    const xRaw = document.getElementById('extendX').value;
    const zRaw = document.getElementById('extendZ').value;
    const countRaw = document.getElementById('extendCount').value;
    closeExtendModal();
    await extendChecklist(techSpec, countRaw ? parseInt(countRaw, 10) : null, xRaw ? parseInt(xRaw, 10) : null, zRaw ? parseInt(zRaw, 10) : null);
}
updateModal.addEventListener('click', function (e) {
    if (e.target === this)
        closeUpdateModal();
});
extendModal.addEventListener('click', function (e) {
    if (e.target === this)
        closeExtendModal();
});
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        closeUpdateModal();
        closeExtendModal();
    }
});
const testCaseEditorView = document.getElementById('testCaseEditorView');
const chatPanelEl = document.querySelector('.chat-panel');
const editorItemIdEl = document.getElementById('editorItemId');
const editorTitleField = document.getElementById('editorTitleField');
const editorStepsContainer = document.getElementById('editorStepsContainer');
const editorExpectedField = document.getElementById('editorExpectedField');
export function openTestCaseEditor(id) {
    const item = state.checklistItems.find(i => String(i.id) === String(id));
    if (!item) {
        console.error('Элемент с id', id, 'не найден');
        alert('Ошибка: тест-кейс не найден');
        return;
    }
    state.editingItemId = id;
    const position = state.checklistItems.findIndex(i => String(i.id) === String(id)) + 1;
    editorItemIdEl.textContent = `#${position}`;
    editorTitleField.textContent = stripIdPrefix(item.title) || '';
    state.editorStepsList = cloneSteps(getStepsList(item));
    editorStepsContainer.innerHTML = stepsListBlockHtml(id, state.editorStepsList, true);
    editorExpectedField.textContent = item.expected_result || '';
    chatPanelEl.classList.add('hidden-by-editor');
    testCaseEditorView.classList.remove('hidden');
    editorTitleField.focus();
}
export function closeTestCaseEditor() {
    testCaseEditorView.classList.add('hidden');
    chatPanelEl.classList.remove('hidden-by-editor');
    state.editingItemId = null;
    state.editorStepsList = [];
}
export function cancelTestCaseEdit() {
    closeTestCaseEditor();
}
export function saveTestCaseEdit() {
    if (!state.editingItemId) {
        closeTestCaseEditor();
        return;
    }
    const item = state.checklistItems.find(i => String(i.id) === String(state.editingItemId));
    if (item) {
        item.title = stripIdPrefix(readEditableText(editorTitleField).replace(/\n+/g, ' ').trim());
        item.steps = cloneSteps(state.editorStepsList);
        item.expected_result = readEditableText(editorExpectedField).trim();
        displayChecklist(state.checklistItems);
        saveChecklistState();
        saveChecklistToSession(state.checklistItems).then(() => {
            displayChecklist(state.checklistItems);
        }).catch(() => { });
        console.log(`Тест-кейс #${state.editingItemId} сохранён из полноэкранного редактора`);
    }
    else {
        console.error('Элемент с id', state.editingItemId, 'не найден при сохранении');
    }
    closeTestCaseEditor();
}
document.addEventListener('click', function (e) {
    const deleteBtnEl = e.target.closest('.delete-btn');
    if (deleteBtnEl) {
        const id = deleteBtnEl.getAttribute('data-id');
        if (id !== null && id !== undefined && id !== '') {
            e.preventDefault();
            e.stopPropagation();
            deleteChecklistItem(id);
        }
        else {
            console.error(' data-id пустой или отсутствует');
            alert('Ошибка: ID тест-кейса не найден');
        }
    }
    const editBtn = e.target.closest('.item-edit-btn');
    if (editBtn) {
        const id = editBtn.getAttribute('data-id');
        if (id !== null && id !== undefined && id !== '') {
            e.preventDefault();
            e.stopPropagation();
            openTestCaseEditor(id);
        }
        else {
            console.error(' data-id пустой или отсутствует');
            alert('Ошибка: ID тест-кейса не найден');
        }
    }
    const stepAddBtn = e.target.closest('.step-add-btn');
    if (stepAddBtn) {
        e.preventDefault();
        const container = stepAddBtn.closest('.steps-list');
        const isEditor = container && container.id === 'editorStepsList';
        const itemId = isEditor ? state.editingItemId : stepAddBtn.getAttribute('data-id');
        addStep(itemId, isEditor);
    }
    const stepRemoveBtn = e.target.closest('.step-remove-btn');
    if (stepRemoveBtn) {
        e.preventDefault();
        const container = stepRemoveBtn.closest('.steps-list');
        const isEditor = container && container.id === 'editorStepsList';
        const itemId = isEditor ? state.editingItemId : stepRemoveBtn.getAttribute('data-id');
        const index = parseInt(stepRemoveBtn.getAttribute('data-index'), 10);
        removeStep(itemId, index, isEditor);
    }
});
document.addEventListener('change', function (e) {
    if (e.target.classList.contains('item-checkbox')) {
        const id = e.target.getAttribute('data-id');
        if (id) {
            toggleChecklistItem(id);
        }
    }
});
document.addEventListener('blur', function (e) {
    if (e.target.classList.contains('item-title') ||
        e.target.classList.contains('item-expected')) {
        const id = e.target.getAttribute('data-id');
        const field = e.target.getAttribute('data-field');
        if (id && field) {
            let value = readEditableText(e.target);
            if (field === 'title')
                value = value.replace(/\n+/g, ' ');
            updateChecklistItem(id, field, value);
        }
    }
    if (e.target.classList.contains('step-row-text')) {
        const container = e.target.closest('.steps-list');
        const isEditor = container && container.id === 'editorStepsList';
        const itemId = isEditor ? state.editingItemId : e.target.getAttribute('data-id');
        const index = parseInt(e.target.getAttribute('data-index'), 10);
        updateStepText(itemId, index, readEditableText(e.target), isEditor);
    }
}, true);
function isSingleLineField(el) {
    return el.classList.contains('item-title') || el.id === 'editorTitleField';
}
function isMultiLineField(el) {
    return el.classList.contains('step-row-text') ||
        el.classList.contains('item-expected') ||
        el.id === 'editorExpectedField';
}
document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.isComposing)
        return;
    const el = e.target;
    if (!el || !el.isContentEditable)
        return;
    if (isSingleLineField(el)) {
        e.preventDefault();
        el.blur();
        return;
    }
    if (isMultiLineField(el)) {
        e.preventDefault();
        insertLineBreakAtCaret();
    }
}, true);
export function openExportModal() {
    const modal = document.getElementById('exportModal');
    if (!modal)
        return;
    if (state.checklistItems.length === 0) {
        alert('Список пуст — экспортировать нечего.');
        return;
    }
    const selected = state.checklistItems.filter(i => i.selected === true);
    if (selected.length === 0) {
        alert('Отметьте галочками тест-кейсы, которые нужно отправить в TestOps.');
        return;
    }
    const pending = selected.filter(i => i.needs_export);
    const toCreate = pending.filter(i => !i.testops_id).length;
    const toUpdate = pending.length - toCreate;
    const alreadySynced = selected.length - pending.length;
    const summary = document.getElementById('exportSummary');
    if (summary) {
        const parts = [];
        if (toCreate)
            parts.push(`будет создано: ${toCreate}`);
        if (toUpdate)
            parts.push(`будет обновлено: ${toUpdate}`);
        if (alreadySynced)
            parts.push(`уже синхронизировано, пропустим: ${alreadySynced}`);
        summary.textContent = `Отмечено ${selected.length} — ` +
            (parts.length ? parts.join(', ') : 'отправлять нечего');
    }
    const input = document.getElementById('exportFolderPath');
    modal.classList.add('active');
    if (input) {
        input.value = '';
        input.focus();
    }
}
export function closeExportModal() {
    const modal = document.getElementById('exportModal');
    if (modal)
        modal.classList.remove('active');
}
async function runExport(folderPath, testCaseIds, resolutions = null) {
    await saveChecklistToSession(state.checklistItems);
    const body = {
        session_id: state.sessionId,
        folder_path: folderPath,
        test_case_ids: testCaseIds
    };
    if (resolutions)
        body.resolutions = resolutions;
    const response = await apiFetch('/api/testops/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    if (Array.isArray(data.test_cases)) {
        state.checklistItems = data.test_cases;
        saveChecklistState();
        displayChecklist(state.checklistItems);
    }
    return data;
}
function reportExport(data) {
    const conflicts = data.conflicts || [];
    generationStatus.textContent = conflicts.length
        ? 'Часть кейсов ждёт решения'
        : (data.success ? 'Экспорт завершён' : 'Экспорт с ошибками');
    let message = `${data.message}.`;
    if (data.folder)
        message += `\nПапка: ${data.folder}`;
    if (data.errors && data.errors.length) {
        const lines = data.errors
            .map(e => `- ${e.title || 'без названия'}: ${e.error}`)
            .join('\n');
        message += `\n\nНе отправлены:\n${lines}`;
    }
    addMessage('assistant', message);
    if (data.success && (data.created || data.updated || data.taken_remote)) {
        playSuccessSound();
    }
    if (conflicts.length)
        openConflictsModal(data.folder_path, conflicts);
}
export async function confirmExport() {
    const input = document.getElementById('exportFolderPath');
    const folderPath = (input ? input.value : '').trim();
    if (!folderPath) {
        alert('Укажите номер папки TestOps, например 1.1.1');
        if (input)
            input.focus();
        return;
    }
    if (!/^\d+(\.\d+)*\.?$/.test(folderPath)) {
        alert('Номер папки состоит только из цифр и точек: например, 1.1.1 — без названия после тире.');
        if (input)
            input.focus();
        return;
    }
    const selectedIds = state.checklistItems
        .filter(i => i.selected === true)
        .map(i => String(i.id));
    const btn = document.getElementById('exportConfirmBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Отправка...';
    }
    generationStatus.textContent = '⏳ Экспорт в TestOps...';
    try {
        const data = await runExport(folderPath, selectedIds);
        closeExportModal();
        reportExport(data);
    }
    catch (error) {
        console.error('Ошибка экспорта:', error);
        generationStatus.textContent = 'Ошибка экспорта';
        addMessage('assistant', `Ошибка экспорта в TestOps: ${error.message}`);
    }
    finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Экспортировать';
        }
        setTimeout(() => { generationStatus.textContent = ''; }, 4000);
    }
}

// ===================== Конфликты версий при экспорте =====================

const CONFLICT_ACTIONS = [
    ['overwrite', 'Перезаписать', 'Отправить вашу версию в TestOps поверх изменений'],
    ['take_remote', 'Взять из TestOps', 'Заменить вашу версию в чате версией из TestOps; в TestOps ничего не меняется'],
    ['save_as_new', 'Сохранить как новый', 'Создать в папке новый кейс с вашей версией; кейс в TestOps не меняется'],
    ['skip', 'Пропустить', 'Ничего не делать сейчас; кейс останется неотправленным'],
];
let pendingConflicts = null;

function formatTestOpsDate(ms) {
    if (ms === null || ms === undefined)
        return 'неизвестно когда';
    return new Date(Number(ms)).toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'
    });
}
function versionLines(version) {
    if (!version)
        return [];
    return [
        `Название: ${version.title || ''}`,
        '',
        'Шаги:',
        ...String(version.steps || '').split('\n'),
        '',
        'Ожидаемый результат:',
        ...String(version.expected_result || '').split('\n'),
    ];
}
// Построчный diff (LCS): для каждой строки — есть ли она в другой версии.
function diffLines(left, right) {
    const n = left.length, m = right.length;
    const lcs = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--)
        for (let j = m - 1; j >= 0; j--)
            lcs[i][j] = left[i] === right[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    const leftChanged = new Array(n).fill(true);
    const rightChanged = new Array(m).fill(true);
    let i = 0, j = 0;
    while (i < n && j < m) {
        if (left[i] === right[j]) {
            leftChanged[i] = false;
            rightChanged[j] = false;
            i++; j++;
        }
        else if (lcs[i + 1][j] >= lcs[i][j + 1]) i++;
        else j++;
    }
    return { leftChanged, rightChanged };
}
function diffColumnHtml(label, lines, changed, cls) {
    const body = lines.map((line, k) =>
        `<div class="diff-line ${changed[k] ? cls : ''}">${escapeHtml(line) || '&nbsp;'}</div>`
    ).join('');
    return `<div class="diff-col"><div class="diff-col-label">${label}</div><div class="diff-col-body">${body}</div></div>`;
}
function conflictDiffHtml(conflict) {
    if (!conflict.remote)
        return `<div class="conflict-diff-unavailable">Не удалось загрузить версию из TestOps для сравнения. Откройте кейс #${escapeHtml(String(conflict.testops_id))} в TestOps.</div>`;
    const left = versionLines(conflict.local);
    const right = versionLines(conflict.remote);
    // Номера шагов не сравниваем: вставка шага в начало иначе подсветила бы все следующие.
    const key = line => line.replace(/^\s*\d+\.\s+/, '').trim();
    const { leftChanged, rightChanged } = diffLines(left.map(key), right.map(key));
    return `<div class="conflict-diff-cols">
        ${diffColumnHtml('Ваша версия', left, leftChanged, 'diff-local')}
        ${diffColumnHtml('Сейчас в TestOps', right, rightChanged, 'diff-remote')}
    </div>`;
}
function openConflictsModal(folderPath, conflicts) {
    pendingConflicts = { folderPath, conflicts };
    const list = document.getElementById('exportConflictsList');
    const modal = document.getElementById('exportConflictsModal');
    if (!list || !modal)
        return;
    list.innerHTML = conflicts.map((c, index) => {
        const who = c.remote_modified_by ? escapeHtml(c.remote_modified_by) : 'кто-то';
        const radios = CONFLICT_ACTIONS.map(([value, label, hint]) => `
            <label class="conflict-action" title="${escapeHtml(hint)}">
                <input type="radio" name="conflict-${index}" value="${value}" ${value === 'skip' ? 'checked' : ''}>
                <span>${label}</span>
            </label>`).join('');
        return `
            <div class="conflict-row" data-index="${index}">
                <div class="conflict-head">
                    <div class="conflict-title">${escapeHtml(stripIdPrefix(c.title) || c.title)}</div>
                    <div class="conflict-meta">TestOps #${escapeHtml(String(c.testops_id))} · изменил ${who}, ${escapeHtml(formatTestOpsDate(c.remote_modified_at))}</div>
                </div>
                <div class="conflict-actions">${radios}</div>
                <button type="button" class="conflict-compare-btn" data-index="${index}">Сравнить</button>
                <div class="conflict-diff" hidden></div>
            </div>`;
    }).join('');
    modal.classList.add('active');
}
export function closeConflictsModal() {
    const modal = document.getElementById('exportConflictsModal');
    if (modal)
        modal.classList.remove('active');
    pendingConflicts = null;
}
export function setAllConflictResolutions(value) {
    document.querySelectorAll('#exportConflictsList input[type="radio"]').forEach(radio => {
        radio.checked = radio.value === value;
    });
}
document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('.conflict-compare-btn');
    if (!btn || !pendingConflicts)
        return;
    const row = btn.closest('.conflict-row');
    const diff = row && row.querySelector('.conflict-diff');
    if (!diff)
        return;
    if (diff.hidden) {
        if (!diff.innerHTML)
            diff.innerHTML = conflictDiffHtml(pendingConflicts.conflicts[Number(btn.dataset.index)]);
        diff.hidden = false;
        btn.textContent = 'Скрыть сравнение';
    }
    else {
        diff.hidden = true;
        btn.textContent = 'Сравнить';
    }
});
export async function confirmConflicts() {
    if (!pendingConflicts)
        return;
    const { folderPath, conflicts } = pendingConflicts;
    const resolutions = {};
    conflicts.forEach((c, index) => {
        const checked = document.querySelector(`#exportConflictsList input[name="conflict-${index}"]:checked`);
        resolutions[String(c.id)] = checked ? checked.value : 'skip';
    });
    const btn = document.getElementById('exportConflictsConfirmBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Отправка...';
    }
    generationStatus.textContent = '⏳ Экспорт в TestOps...';
    try {
        const data = await runExport(folderPath, conflicts.map(c => String(c.id)), resolutions);
        closeConflictsModal();
        reportExport(data);
    }
    catch (error) {
        console.error('Ошибка экспорта:', error);
        generationStatus.textContent = 'Ошибка экспорта';
        addMessage('assistant', `Ошибка экспорта в TestOps: ${error.message}`);
    }
    finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Продолжить экспорт';
        }
        setTimeout(() => { generationStatus.textContent = ''; }, 4000);
    }
}
