const state = { sessionId: "", busy: false, awaiting: false, document: null, thinking: null, dragDepth: 0 };
const elements = {
    sidebar: document.getElementById("sidebar"), menu: document.getElementById("menuButton"), conversation: document.getElementById("conversation"), welcome: document.getElementById("welcome"),
    form: document.getElementById("messageForm"), input: document.getElementById("messageInput"), send: document.getElementById("sendButton"), attach: document.getElementById("attachButton"),
    file: document.getElementById("fileInput"), upload: document.getElementById("uploadButton"), clear: document.getElementById("clearDocumentButton"), documentName: document.getElementById("documentName"),
    documentMeta: document.getElementById("documentMeta"), sessionStatus: document.getElementById("sessionStatus"), statusDot: document.getElementById("statusDot"), activity: document.getElementById("activityText"),
    progress: document.getElementById("uploadProgress"), progressText: document.getElementById("uploadProgressText"), chars: document.getElementById("charCount"), toasts: document.getElementById("toastRegion"),
    dropOverlay: document.getElementById("dropOverlay")
};

document.addEventListener("DOMContentLoaded", initialize);
elements.form.addEventListener("submit", event => { event.preventDefault(); sendMessage(elements.input.value); });
elements.input.addEventListener("input", resizeComposer);
elements.input.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.form.requestSubmit(); } });
elements.attach.addEventListener("click", () => elements.file.click());
elements.upload.addEventListener("click", () => elements.file.click());
elements.file.addEventListener("change", () => { if (elements.file.files[0]) uploadDocument(elements.file.files[0]); elements.file.value = ""; });
document.addEventListener("dragenter", handleDragEnter);
document.addEventListener("dragover", handleDragOver);
document.addEventListener("dragleave", handleDragLeave);
document.addEventListener("drop", handleDrop);
elements.clear.addEventListener("click", clearDocument);
elements.menu.addEventListener("click", () => elements.sidebar.classList.toggle("open"));
document.getElementById("sidebarCloseButton").addEventListener("click", () => elements.sidebar.classList.remove("open"));
document.querySelectorAll(".suggestion").forEach(button => button.addEventListener("click", () => sendMessage(button.dataset.prompt)));

function hasDraggedFiles(event) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function handleDragEnter(event) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    state.dragDepth += 1;
    elements.dropOverlay.classList.add("visible");
    elements.dropOverlay.setAttribute("aria-hidden", "false");
}

function handleDragOver(event) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
}

function handleDragLeave(event) {
    if (state.dragDepth === 0) return;
    event.preventDefault();
    state.dragDepth = Math.max(0, state.dragDepth - 1);
    if (state.dragDepth === 0) hideDropOverlay();
}

function handleDrop(event) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    const files = Array.from(event.dataTransfer?.files || []);
    hideDropOverlay();
    if (files.length !== 1) {
        toast("每次只能上传一个文档", true);
        return;
    }
    uploadDocument(files[0]);
}

function hideDropOverlay() {
    state.dragDepth = 0;
    elements.dropOverlay.classList.remove("visible");
    elements.dropOverlay.setAttribute("aria-hidden", "true");
}

async function initialize() {
    try {
        const data = await api("/api/agent/sessions", { method: "POST" });
        state.sessionId = data.session_id;
        setSessionStatus("会话已连接", false);
        elements.input.focus();
    } catch (error) {
        setSessionStatus("连接失败", true);
        toast(error.message, true);
    }
}

async function api(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
        let message = `请求失败 (${response.status})`;
        try { const body = await response.json(); message = body.detail || message; } catch (_) { /* no JSON body */ }
        throw new Error(message);
    }
    return response.json();
}

async function sendMessage(rawMessage) {
    const message = rawMessage.trim();
    if (!message || state.busy || state.awaiting || !state.sessionId) return;
    elements.input.value = "";
    resizeComposer();
    hideWelcome();
    addUserMessage(message);
    setBusy(true, "Agent 正在处理请求");
    showThinking();
    try {
        const response = await api(`/api/agent/sessions/${state.sessionId}/messages`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message })
        });
        handleAgentResponse(response);
    } catch (error) {
        removeThinking();
        addAgentMessage(`请求未完成：${error.message}`);
        toast(error.message, true);
    } finally {
        setBusy(false);
    }
}

async function uploadDocument(file) {
    if (!state.sessionId) {
        toast("会话尚未建立，请稍后重试", true);
        return;
    }
    if (state.busy || state.awaiting) {
        toast(state.awaiting ? "请先完成当前确认操作" : "当前任务处理中，请稍后上传", true);
        return;
    }
    const formData = new FormData();
    formData.append("file", file, file.name);
    setBusy(true, "正在解析文档");
    elements.progress.classList.remove("hidden");
    elements.progressText.textContent = `正在处理 ${file.name}`;
    try {
        const response = await api(`/api/agent/sessions/${state.sessionId}/document`, { method: "POST", body: formData });
        state.document = response.document;
        renderDocumentState();
        hideWelcome();
        addSystemMessage(`文档已就绪：${response.document.file_name}`);
        toast("文档解析完成");
        if (window.innerWidth <= 850) elements.sidebar.classList.remove("open");
    } catch (error) {
        toast(error.message, true);
    } finally {
        elements.progress.classList.add("hidden");
        setBusy(false);
    }
}

async function clearDocument() {
    if (!state.document || state.busy || state.awaiting) return;
    try {
        await api(`/api/agent/sessions/${state.sessionId}/document`, { method: "DELETE" });
        state.document = null;
        renderDocumentState();
        addSystemMessage("已清除当前文档");
    } catch (error) { toast(error.message, true); }
}

function handleAgentResponse(response) {
    removeThinking();
    if (response.answer) addAgentMessage(response.answer, response.downloads || []);
    if (response.interrupt) {
        state.awaiting = true;
        renderInterrupt(response.interrupt);
    } else {
        state.awaiting = false;
        if (!response.answer && response.downloads?.length) addAgentMessage("导出文件已生成。", response.downloads);
    }
    updateControls();
}

function renderInterrupt(interrupt) {
    if (interrupt.type === "document_query_confirmation") renderDocumentConfirmation(interrupt);
    else addAgentMessage(interrupt.message || "当前操作需要确认。", []);
}

function renderDocumentConfirmation(interrupt) {
    const row = createAgentRow();
    const content = row.querySelector(".agent-content");
    const panel = document.createElement("div");
    panel.className = "interrupt-panel";
    const queries = interrupt.query?.queries || [];
    panel.innerHTML = `<div class="interrupt-header"><strong>确认 DMF 查询条件</strong><span class="query-count">${queries.length} 组</span></div>`;
    const grid = document.createElement("div");
    grid.className = "query-grid";
    queries.forEach((query, index) => grid.appendChild(createQueryItem(query, index)));
    panel.appendChild(grid);
    const actions = createActions();
    const reject = actionButton("拒绝", "danger", () => resumeDocument("reject", null, panel));
    const confirm = actionButton("确认并查询", "primary", () => resumeDocument("edit", collectQueries(panel), panel));
    actions.append(reject, confirm);
    panel.appendChild(actions);
    content.appendChild(panel);
    elements.conversation.appendChild(row);
    scrollConversation();
}

function createQueryItem(query, index) {
    const item = document.createElement("div");
    item.className = "query-item";
    item.innerHTML = `<span class="query-index">${index + 1}</span>`;
    item.appendChild(queryField("DMF 编号", "dmf_no", query.dmf_no || ""));
    item.appendChild(queryField("申请商", "applicant_name", query.applicant_name || ""));
    item.appendChild(queryField("成分（逗号分隔）", "ingredients", (query.ingredients || []).join(", ")));
    return item;
}

function queryField(label, name, value) {
    const wrapper = document.createElement("div");
    wrapper.className = "field";
    const caption = document.createElement("label");
    caption.textContent = label;
    const input = document.createElement("input");
    input.name = name;
    input.value = value;
    wrapper.append(caption, input);
    return wrapper;
}

function collectQueries(panel) {
    return { queries: Array.from(panel.querySelectorAll(".query-item")).map(item => ({
        dmf_no: item.querySelector('[name="dmf_no"]').value.trim(),
        applicant_name: item.querySelector('[name="applicant_name"]').value.trim(),
        ingredients: item.querySelector('[name="ingredients"]').value.split(/[,，]/).map(value => value.trim()).filter(Boolean)
    })) };
}

async function resumeDocument(action, query, panel) {
    disablePanel(panel);
    await resume({ action, query });
}

async function resume(payload) {
    setBusy(true, "正在继续执行");
    showThinking();
    try {
        const response = await api(`/api/agent/sessions/${state.sessionId}/resume`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
        });
        state.awaiting = false;
        handleAgentResponse(response);
    } catch (error) {
        removeThinking(); state.awaiting = true; toast(error.message, true); addAgentMessage(`操作未完成：${error.message}`);
    } finally { setBusy(false); }
}

function addUserMessage(text) {
    const row = document.createElement("article"); row.className = "message-row user";
    const body = document.createElement("div"); body.className = "message-body";
    const paragraph = document.createElement("p"); paragraph.textContent = text;
    body.append(paragraph); row.appendChild(body); elements.conversation.appendChild(row); scrollConversation();
}

function addAgentMessage(text, downloads = []) {
    const row = createAgentRow();
    const content = row.querySelector(".agent-content");
    renderMarkdown(content, text);
    if (downloads.length) {
        const list = document.createElement("div"); list.className = "download-list";
        downloads.forEach(file => { const link = document.createElement("a"); link.className = "download-link"; link.href = file.url; link.download = file.file_name; link.textContent = `下载 ${file.file_name}`; list.appendChild(link); });
        content.appendChild(list);
    }
    elements.conversation.appendChild(row); scrollConversation();
}

function addSystemMessage(text) {
    const row = createAgentRow();
    const content = row.querySelector(".agent-content");
    const paragraph = document.createElement("p"); paragraph.textContent = text; paragraph.style.color = "#65716d";
    content.appendChild(paragraph); elements.conversation.appendChild(row); scrollConversation();
}

function createAgentRow() {
    const row = document.createElement("article"); row.className = "message-row agent";
    const avatar = document.createElement("div"); avatar.className = "avatar"; avatar.textContent = "D";
    const body = document.createElement("div"); body.className = "message-body";
    const content = document.createElement("div"); content.className = "agent-content";
    const time = document.createElement("div"); time.className = "message-time"; time.textContent = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    body.append(content, time); row.append(avatar, body); return row;
}

function renderMarkdown(container, text) {
    const lines = String(text).split("\n");
    let index = 0;
    while (index < lines.length) {
        const line = lines[index];
        if (line.trim().startsWith("|") && index + 1 < lines.length && /^\s*\|?[\s:|-]+\|\s*$/.test(lines[index + 1])) {
            const tableLines = [line]; index += 2;
            while (index < lines.length && lines[index].trim().startsWith("|")) tableLines.push(lines[index++]);
            container.appendChild(buildTable(tableLines)); continue;
        }
        if (!line.trim()) { index += 1; continue; }
        let element;
        let content = line;
        if (line.startsWith("## ")) { element = document.createElement("h2"); content = line.slice(3); }
        else if (line.startsWith("### ")) { element = document.createElement("h3"); content = line.slice(4); }
        else if (/^\d+\.\s/.test(line)) { element = document.createElement("p"); }
        else if (line.startsWith("- ")) { element = document.createElement("p"); content = `• ${line.slice(2)}`; }
        else if (line.startsWith("> ")) { element = document.createElement("p"); content = line.slice(2); element.style.color = "#a86f12"; }
        else { element = document.createElement("p"); }
        appendInlineMarkdown(element, content);
        container.appendChild(element); index += 1;
    }
}

function appendInlineMarkdown(element, text) {
    const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
        element.appendChild(document.createTextNode(text.slice(offset, match.index)));
        const value = match[0];
        const inline = document.createElement(value.startsWith("**") ? "strong" : "code");
        inline.textContent = value.startsWith("**") ? value.slice(2, -2) : value.slice(1, -1);
        element.appendChild(inline);
        offset = match.index + value.length;
    }
    element.appendChild(document.createTextNode(text.slice(offset)));
}

function buildTable(lines) {
    const wrapper = document.createElement("div"); wrapper.className = "table-scroll";
    const table = document.createElement("table");
    lines.forEach((line, rowIndex) => {
        const row = document.createElement("tr");
        splitTableRow(line).forEach(value => { const cell = document.createElement(rowIndex === 0 ? "th" : "td"); cell.textContent = value; row.appendChild(cell); });
        (rowIndex === 0 ? table.createTHead() : (table.tBodies[0] || table.createTBody())).appendChild(row);
    });
    wrapper.appendChild(table); return wrapper;
}

function splitTableRow(line) { return line.trim().replace(/^\||\|$/g, "").split("|").map(value => value.trim().replace(/\\\|/g, "|")); }
function createActions() { const actions = document.createElement("div"); actions.className = "interrupt-actions"; return actions; }
function actionButton(label, kind, handler) { const button = document.createElement("button"); button.type = "button"; button.className = `action-button ${kind}`; button.textContent = label; button.addEventListener("click", handler); return button; }
function disablePanel(panel) { panel.querySelectorAll("button,input").forEach(control => { control.disabled = true; }); panel.style.opacity = ".65"; }
function showThinking() { removeThinking(); const row = createAgentRow(); row.classList.add("thinking-row"); row.querySelector(".agent-content").innerHTML = '<div class="thinking"><span></span><span></span><span></span></div>'; row.querySelector(".message-time").remove(); elements.conversation.appendChild(row); state.thinking = row; scrollConversation(); }
function removeThinking() { if (state.thinking) state.thinking.remove(); state.thinking = null; }
function setBusy(busy, activity = "可继续输入请求") { state.busy = busy; elements.activity.textContent = activity; updateControls(); }
function updateControls() { const disabled = state.busy || state.awaiting || !state.sessionId; elements.send.disabled = disabled; elements.attach.disabled = disabled; elements.upload.disabled = disabled; elements.clear.disabled = disabled; elements.input.disabled = state.awaiting; }
function resizeComposer() { elements.input.style.height = "auto"; elements.input.style.height = `${Math.min(elements.input.scrollHeight, 150)}px`; elements.chars.textContent = `${elements.input.value.length} / 10000`; }
function hideWelcome() { if (elements.welcome) elements.welcome.remove(); }
function scrollConversation() { requestAnimationFrame(() => { elements.conversation.scrollTop = elements.conversation.scrollHeight; }); }
function setSessionStatus(text, error) { elements.sessionStatus.textContent = text; elements.statusDot.classList.toggle("error", error); }
function renderDocumentState() { if (state.document) { elements.documentName.textContent = state.document.file_name; elements.documentMeta.textContent = "已解析，可用于查询"; elements.clear.classList.remove("hidden"); } else { elements.documentName.textContent = "未选择文档"; elements.documentMeta.textContent = "等待上传"; elements.clear.classList.add("hidden"); } }
function toast(message, error = false) { const item = document.createElement("div"); item.className = `toast${error ? " error" : ""}`; item.textContent = message; elements.toasts.appendChild(item); window.setTimeout(() => item.remove(), 4200); }
