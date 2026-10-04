import { state, apiFetch, MAX_IMAGES, chatContainer, messageInput, sendBtn, clearBtn, typingIndicator, playSuccessSound, playErrorSound } from './core.js';
import { generateChecklistFromChat, searchTestCases, acceptReuseOffer, declineReuseOffer } from './checklist.js';
import { startResultChat } from './projects.js';
export function addMessage(role, content, images, extra) {
    state.messages.push({ role, content, images: images || [], ...(extra || {}) });
    const messageElement = createMessageElement(role, content, images, extra);
    chatContainer.appendChild(messageElement);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}
const REUSE_OFFER_STATUS_TEXT = {
    accepted: 'Выбрано: редактирование найденных кейсов',
    declined: 'Выбрано: генерация нового чек-листа',
    expired: 'Предложение устарело'
};
export function setReuseOfferStatus(messageEl, status) {
    if (!messageEl)
        return;
    messageEl.dataset.status = status;
    const pending = status === 'pending';
    messageEl.querySelectorAll('.reuse-offer-btn').forEach(btn => { btn.disabled = !pending; });
    const statusEl = messageEl.querySelector('.reuse-offer-status');
    if (statusEl) {
        statusEl.textContent = pending ? '' : (REUSE_OFFER_STATUS_TEXT[status] || REUSE_OFFER_STATUS_TEXT.expired);
        statusEl.style.display = pending ? 'none' : '';
    }
}
export function markPendingReuseOffersExpired() {
    chatContainer.querySelectorAll('.message.reuse-offer[data-status="pending"]').forEach(el => {
        setReuseOfferStatus(el, 'expired');
    });
}
function appendReuseOfferControls(div, extra) {
    div.classList.add('reuse-offer');
    const messageId = extra.id;
    const actions = document.createElement('div');
    actions.className = 'reuse-offer-actions';
    actions.innerHTML = `
        <button type="button" class="reuse-offer-btn reuse-offer-accept">Редактировать найденные</button>
        <button type="button" class="reuse-offer-btn reuse-offer-decline">Сгенерировать новый чек-лист</button>
    `;
    const statusEl = document.createElement('div');
    statusEl.className = 'reuse-offer-status';
    div.appendChild(actions);
    div.appendChild(statusEl);
    actions.querySelector('.reuse-offer-accept').addEventListener('click', () => acceptReuseOffer(messageId, div));
    actions.querySelector('.reuse-offer-decline').addEventListener('click', () => declineReuseOffer(messageId, div));
    setReuseOfferStatus(div, ((extra.payload || {}).status) || 'expired');
}
export function createMessageElement(role, content, images, extra) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    const label = document.createElement('div');
    label.className = 'role-label';
    label.textContent = role === 'user' ? 'Вы' : 'ИИ';
    div.appendChild(label);
    if (images && images.length) {
        const imagesDiv = document.createElement('div');
        imagesDiv.className = 'message-images';
        images.forEach(src => {
            const img = document.createElement('img');
            img.src = src;
            img.className = 'message-image-thumb';
            img.loading = 'lazy';
            img.alt = 'Прикреплённое изображение';
            img.addEventListener('click', () => openImageInNewTab(src));
            imagesDiv.appendChild(img);
        });
        div.appendChild(imagesDiv);
    }
    const contentDiv = document.createElement('div');
    contentDiv.className = 'content';
    contentDiv.textContent = content || ' ';
    div.appendChild(contentDiv);
    if (extra && extra.kind === 'reuse_offer' && extra.id)
        appendReuseOfferControls(div, extra);
    return div;
}
function openImageInNewTab(src) {
    if (!src.startsWith('data:')) {
        window.open(src, '_blank');
        return;
    }
    const commaIndex = src.indexOf(',');
    const header = src.slice(0, commaIndex);
    const base64 = src.slice(commaIndex + 1);
    const mimeMatch = header.match(/^data:([^;]+);base64$/);
    const mimeType = mimeMatch ? mimeMatch[1] : 'image/png';
    let blobUrl;
    try {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++)
            bytes[i] = binary.charCodeAt(i);
        blobUrl = URL.createObjectURL(new Blob([bytes], { type: mimeType }));
    }
    catch (e) {
        console.error('Не удалось открыть изображение:', e);
        return;
    }
    window.open(blobUrl, '_blank');
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
}
export function updateMessageContent(element, content) {
    const contentDiv = element.querySelector('.content');
    if (contentDiv)
        contentDiv.textContent = content || ' ';
}
export async function sendMessage() {
    const content = messageInput.value.trim();
    if (!content || state.isProcessing)
        return;
    if (!state.isProjectLoaded || !state.sessionId) {
        addMessage('assistant', 'Сначала выберите проект из списка');
        return;
    }
    const images = state.selectedImages.slice();
    const imageMimeTypes = state.selectedImageMimeTypes.slice();
    const startsNewResult = state.currentMode === 'generate' || state.currentMode === 'search';
    if (startsNewResult && state.chatHasChecklist) {
        try {
            await startResultChat();
        }
        catch (e) {
            console.error('Не удалось создать новый чат:', e);
            addMessage('assistant', `Не удалось создать новый чат: ${e.message}. ` +
                `Запрос не отправлен — предыдущий результат сохранён.`);
            return;
        }
    }
    state.selectedImages = [];
    state.selectedImageFiles = [];
    state.selectedImageMimeTypes = [];
    updateImagePreview();
    const imagePreviewSrcs = images.map((b64, i) => `data:${imageMimeTypes[i] || 'image/png'};base64,${b64}`);
    addMessage('user', content, imagePreviewSrcs);
    messageInput.value = '';
    messageInput.style.height = 'auto';
    if (state.currentMode === 'generate') {
        const xRaw = document.getElementById('filterX').value;
        const zRaw = document.getElementById('filterZ').value;
        const countRaw = document.getElementById('filterCount').value;
        const x = xRaw !== '' ? parseInt(xRaw, 10) : null;
        const z = zRaw !== '' ? parseInt(zRaw, 10) : null;
        const count = countRaw !== '' ? parseInt(countRaw, 10) : null;
        await generateChecklistFromChat(content, images, x, z, count);
        return;
    }
    if (state.currentMode === 'search') {
        await searchTestCases(content);
        return;
    }
    sendBtn.disabled = true;
    state.isProcessing = true;
    typingIndicator.classList.add('active');
    state.currentAssistantContent = '';
    const assistantMessageElement = createMessageElement('assistant', '');
    chatContainer.appendChild(assistantMessageElement);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    state.currentAssistantMessage = assistantMessageElement;
    try {
        const body = {
            session_id: state.sessionId,
            message: content,
            stream: true,
            temperature: 0.7
        };
        if (images.length > 0) {
            body.images = images;
        }
        const response = await apiFetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!response.ok)
            throw new Error(`HTTP error! status: ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        typingIndicator.classList.remove('active');
        while (true) {
            const { done, value } = await reader.read();
            if (done)
                break;
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.error)
                            throw new Error(data.error);
                        if (data.content) {
                            state.currentAssistantContent += data.content;
                            updateMessageContent(state.currentAssistantMessage, state.currentAssistantContent);
                            chatContainer.scrollTop = chatContainer.scrollHeight;
                        }
                        if (data.done) {
                            state.messages.push({ role: 'assistant', content: state.currentAssistantContent });
                        }
                    }
                    catch (e) {
                        if (e instanceof SyntaxError)
                            continue;
                        throw e;
                    }
                }
            }
        }
        if (!state.currentAssistantContent) {
            updateMessageContent(state.currentAssistantMessage, 'Модель не дала ответа. Попробуйте еще раз.');
            state.messages.push({ role: 'assistant', content: 'Модель не дала ответа. Попробуйте еще раз.' });
        }
        playSuccessSound();
    }
    catch (error) {
        console.error('Ошибка:', error);
        typingIndicator.classList.remove('active');
        updateMessageContent(state.currentAssistantMessage, `Ошибка: ${error.message}. Убедитесь, что Ollama запущен.`);
        state.messages.push({ role: 'assistant', content: `Ошибка: ${error.message}` });
        if (error instanceof SyntaxError)
            playErrorSound();
    }
    state.isProcessing = false;
    sendBtn.disabled = false;
    state.currentAssistantMessage = null;
    messageInput.focus();
}
clearBtn.addEventListener('click', function () {
    if (state.messages.length === 0)
        return;
    if (confirm('Очистить историю чата?')) {
        state.messages = [];
        chatContainer.innerHTML = '';
        if (state.currentProject) {
            addMessage('assistant', `История очищена. Чем могу помочь?`);
        }
        else {
            addMessage('assistant', 'История очищена. Выберите проект из списка.');
        }
    }
});
document.getElementById('imageInput').addEventListener('change', function (e) {
    handleFiles(e.target.files);
    this.value = '';
});
function extractImageFilesFromDataTransfer(clipboardData) {
    const files = [];
    if (clipboardData.items && clipboardData.items.length) {
        for (let i = 0; i < clipboardData.items.length; i++) {
            const item = clipboardData.items[i];
            if (item.kind === 'file' && item.type && /^image\//i.test(item.type)) {
                const file = item.getAsFile();
                if (file)
                    files.push(file);
            }
        }
    }
    if (files.length === 0 && clipboardData.files && clipboardData.files.length) {
        for (let i = 0; i < clipboardData.files.length; i++) {
            const file = clipboardData.files[i];
            if (file.type && /^image\//i.test(file.type)) {
                files.push(file);
            }
        }
    }
    return files;
}
function clipboardLikelyHasFiles(clipboardData) {
    if (clipboardData.types) {
        const types = Array.from(clipboardData.types);
        if (types.indexOf('Files') !== -1)
            return true;
    }
    if (clipboardData.items && clipboardData.items.length) {
        for (let i = 0; i < clipboardData.items.length; i++) {
            if (clipboardData.items[i].kind === 'file')
                return true;
        }
    }
    return false;
}
async function extractImageFilesFromClipboardApi() {
    if (!navigator.clipboard || !navigator.clipboard.read)
        return [];
    const files = [];
    try {
        const clipboardItems = await navigator.clipboard.read();
        for (const clipboardItem of clipboardItems) {
            const imageType = clipboardItem.types.find(t => /^image\//i.test(t));
            if (!imageType)
                continue;
            const blob = await clipboardItem.getType(imageType);
            const ext = imageType.split('/')[1] || 'png';
            files.push(new File([blob], `Вставленное изображение.${ext}`, { type: imageType }));
        }
    }
    catch (err) {
        console.warn('Async Clipboard API: не удалось прочитать изображение из буфера обмена:', err);
    }
    return files;
}
messageInput.addEventListener('paste', async function (e) {
    const clipboardData = e.clipboardData || window.clipboardData;
    if (!clipboardData)
        return;
    const directImages = extractImageFilesFromDataTransfer(clipboardData);
    if (directImages.length > 0) {
        e.preventDefault();
        e.stopPropagation();
        handleFiles(directImages);
        return;
    }
    if (!clipboardLikelyHasFiles(clipboardData))
        return;
    e.preventDefault();
    e.stopPropagation();
    const asyncImages = await extractImageFilesFromClipboardApi();
    if (asyncImages.length > 0) {
        handleFiles(asyncImages);
    }
});
function handleFiles(files) {
    if (!files || files.length === 0)
        return;
    const available = MAX_IMAGES - state.selectedImages.length;
    if (files.length > available) {
        alert(`Можно прикрепить не более ${MAX_IMAGES} изображений (уже ${state.selectedImages.length}).`);
        return;
    }
    let pastedCounter = state.selectedImages.length;
    for (let file of files) {
        if (file.size > 5 * 1024 * 1024) {
            alert(`Файл ${file.name || 'изображение'} превышает 5 МБ.`);
            continue;
        }
        pastedCounter += 1;
        const displayName = file.name && file.name.trim() ? file.name : `Вставленное изображение ${pastedCounter}.png`;
        const reader = new FileReader();
        reader.onload = function (event) {
            const base64 = event.target.result.split(',')[1];
            state.selectedImages.push(base64);
            state.selectedImageFiles.push(displayName);
            state.selectedImageMimeTypes.push(file.type || 'image/png');
            updateImagePreview();
        };
        reader.readAsDataURL(file);
    }
}
export function updateImagePreview() {
    const container = document.getElementById('imagePreviewContainer');
    if (!container)
        return;
    container.innerHTML = '';
    if (state.selectedImages.length === 0) {
        container.style.display = 'none';
        return;
    }
    container.style.display = 'flex';
    state.selectedImageFiles.forEach((name, index) => {
        const div = document.createElement('div');
        div.className = 'image-preview-item';
        div.innerHTML = `
            <span class="file-name">${name}</span>
            <button class="remove-image" data-index="${index}" title="Удалить">×</button>
        `;
        container.appendChild(div);
    });
    container.querySelectorAll('.remove-image').forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            const idx = parseInt(this.dataset.index);
            state.selectedImages.splice(idx, 1);
            state.selectedImageFiles.splice(idx, 1);
            state.selectedImageMimeTypes.splice(idx, 1);
            updateImagePreview();
        });
    });
}
