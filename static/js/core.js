export const MAX_IMAGES = 5;
export const state = {
    sessionId: null,
    currentProject: null,
    isProjectLoaded: false,
    isGenerating: false,
    messages: [],
    isProcessing: false,
    currentAssistantMessage: null,
    currentAssistantContent: '',
    checklistItems: [],
    welcomeMessageSent: false,
    currentMode: 'chat',
    panelMode: 'checklist',
    panelReused: false,
    selectedImages: [],
    selectedImageFiles: [],
    selectedImageMimeTypes: [],
    editingItemId: null,
    editorStepsList: [],
    newItemIds: [],
    checklistSnapshot: null,
    chatHasChecklist: false,
    lastSearchQuery: '',
    searchFilters: { x: null, z: null },
    searchShown: 0,
    searchHasMore: false,
    previewItems: [],
    userRole: null,
    projectNames: {}
};
export const chatContainer = document.getElementById('chatContainer');
export const messageInput = document.getElementById('messageInput');
export const sendBtn = document.getElementById('sendBtn');
export const clearBtn = document.getElementById('clearBtn');
export const typingIndicator = document.getElementById('typingIndicator');
export const checklistContainer = document.getElementById('checklistContainer');
export const checklistPanel = document.getElementById('checklistPanel');
export const checklistCount = document.getElementById('checklistCount');
export const statusDot = document.getElementById('statusDot');
export const updateModal = document.getElementById('updateModal');
export const updateTechSpec = document.getElementById('updateTechSpec');
export const extendModal = document.getElementById('extendModal');
export const extendTechSpec = document.getElementById('extendTechSpec');
export const projectSelect = document.getElementById('projectSelect');
export const generationStatus = document.getElementById('generationStatus');
export const successSound = document.getElementById('successSound');
export const errorSound = document.getElementById('errorSound');
export async function apiFetch(url, options = {}) {
    const res = await fetch(url, { ...options, credentials: 'same-origin' });
    if (res.status === 401 && url !== '/api/auth/me' && url !== '/api/auth/login') {
        document.getElementById('authOverlay').classList.remove('hidden');
    }
    return res;
}
export function escapeHtml(text) {
    if (!text)
        return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
export function stripIdPrefix(title) {
    if (!title)
        return title;
    return title.replace(/^\s*#\s*\d+[.)]?\s*/, '').trim();
}
export function playSuccessSound() {
    if (!successSound)
        return;
    successSound.currentTime = 0;
    successSound.play().catch(() => { });
}
export function playErrorSound() {
    if (!errorSound)
        return;
    errorSound.currentTime = 0;
    errorSound.play().catch(() => { });
}
export function readEditableText(el) {
    if (!el)
        return '';
    const raw = typeof el.innerText === 'string' ? el.innerText : (el.textContent || '');
    return raw.replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ').replace(/\n+$/, '');
}
export function insertLineBreakAtCaret() {
    try {
        if (document.execCommand && document.execCommand('insertLineBreak'))
            return;
    }
    catch (e) { }
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount)
        return;
    const range = selection.getRangeAt(0);
    range.deleteContents();
    const node = document.createTextNode('\n');
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
}
