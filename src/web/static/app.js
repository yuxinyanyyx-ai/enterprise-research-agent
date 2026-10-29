const state = { sessionId: "", busy: false, awaiting: false, documents: [], projectSlideFiles: [], selectedPptId: "", artifacts: [], thinking: null, dragDepth: 0, view: "chat", conversations: new Map(), sources: [], topics: [], topicTotal: 0, topicOffset: 0, topicRequestId: 0, knowledgeTimer: null, knowledgeLoading: false, activityTimer: null, activityStartedAt: 0, sourceSearchTimer: null };
const elements = {
    appShell: document.querySelector(".app-shell"), sidebar: document.getElementById("sidebar"), menu: document.getElementById("menuButton"), conversation: document.getElementById("conversation"), welcome: document.getElementById("welcome"),
    form: document.getElementById("messageForm"), input: document.getElementById("messageInput"), send: document.getElementById("sendButton"), attach: document.getElementById("attachButton"),
    file: document.getElementById("fileInput"), upload: document.getElementById("uploadButton"), clear: document.getElementById("clearDocumentButton"), documentList: document.getElementById("documentList"),
    documentCount: document.getElementById("documentCount"), sessionStatus: document.getElementById("sessionStatus"), statusDot: document.getElementById("statusDot"), activity: document.getElementById("activityText"), activityElapsed: document.getElementById("activityElapsed"),
    progress: document.getElementById("uploadProgress"), progressText: document.getElementById("uploadProgressText"), chars: document.getElementById("charCount"), toasts: document.getElementById("toastRegion"),
    dropOverlay: document.getElementById("dropOverlay")
};

document.addEventListener("DOMContentLoaded", initialize);
elements.form.addEventListener("submit", event => { event.preventDefault(); sendMessage(elements.input.value); });
elements.input.addEventListener("input", resizeComposer);
elements.input.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.form.requestSubmit(); } });
elements.attach.addEventListener("click", () => elements.file.click());
elements.upload.addEventListener("click", () => elements.file.click());
elements.file.addEventListener("change", () => { if (elements.file.files.length) uploadDocuments(Array.from(elements.file.files)); elements.file.value = ""; });
document.addEventListener("dragenter", handleDragEnter);
document.addEventListener("dragover", handleDragOver);
document.addEventListener("dragleave", handleDragLeave);
document.addEventListener("drop", handleDrop);
elements.clear.addEventListener("click", clearDocuments);
elements.documentList.addEventListener("click", event => {
    const button = event.target.closest("[data-document-id]");
    if (button) deleteDocument(button.dataset.documentId);
});
elements.menu.addEventListener("click", () => {
    if (window.matchMedia("(max-width: 850px)").matches) elements.sidebar.classList.toggle("open");
    else elements.appShell.classList.toggle("sidebar-collapsed");
    syncSidebarToggle();
});
document.getElementById("sidebarCloseButton").addEventListener("click", () => {
    elements.sidebar.classList.remove("open");
    syncSidebarToggle();
});
window.addEventListener("resize", syncSidebarToggle);
const welcomeTemplate = elements.welcome.cloneNode(true);
elements.conversation.addEventListener("click", event => {
    const button = event.target.closest(".suggestion");
    if (button && !state.busy && !state.awaiting) { elements.input.value = button.dataset.prompt; resizeComposer(); elements.input.focus(); }
});
document.getElementById("newConversationButton").addEventListener("click", initialize);
document.getElementById("conversationSearch").addEventListener("input", renderRecentConversations);
document.getElementById("recentConversationList").addEventListener("click", event => {
    const button = event.target.closest("[data-session]");
    if (button) switchConversation(button.dataset.session);
});
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
document.getElementById("materialsToggle").addEventListener("click", () => document.getElementById("contextPanel").classList.toggle("open"));
document.getElementById("pptSourceSelect").addEventListener("change", event => { state.selectedPptId = event.target.value; renderDocumentState(); });
document.getElementById("knowledgeRefresh").addEventListener("click", refreshKnowledge);
document.getElementById("sourceSearch").addEventListener("input", () => {
    clearTimeout(state.sourceSearchTimer);
    state.sourceSearchTimer = setTimeout(renderSources, 150);
});
document.getElementById("sourceScope").addEventListener("change", () => {
    clearTimeout(state.sourceSearchTimer);
    renderSources();
    refreshTopics(true);
});
document.getElementById("topicLevel").addEventListener("change", () => refreshTopics(true));
document.getElementById("topicsLoadMore").addEventListener("click", () => refreshTopics(false));
document.querySelectorAll("[data-knowledge-tab]").forEach(button => {
    button.addEventListener("click", () => setKnowledgeTab(button.dataset.knowledgeTab));
    button.addEventListener("keydown", event => {
        const tabs = [...document.querySelectorAll("[data-knowledge-tab]")];
        const currentIndex = tabs.indexOf(button);
        const nextIndex = event.key === "ArrowRight" ? (currentIndex + 1) % tabs.length
            : event.key === "ArrowLeft" ? (currentIndex - 1 + tabs.length) % tabs.length
                : event.key === "Home" ? 0
                    : event.key === "End" ? tabs.length - 1 : -1;
        if (nextIndex < 0) return;
        event.preventDefault();
        tabs[nextIndex].focus();
        tabs[nextIndex].click();
    });
});
document.getElementById("knowledgeUploadButton").addEventListener("click", () => document.getElementById("knowledgeUploadForm").classList.remove("hidden"));
document.getElementById("knowledgeUploadCancel").addEventListener("click", () => document.getElementById("knowledgeUploadForm").classList.add("hidden"));
document.getElementById("knowledgeUploadForm").addEventListener("submit", uploadKnowledge);
document.getElementById("parseCheckButton").addEventListener("click", () => startKnowledgeJob("check"));
document.getElementById("startIndexButton").addEventListener("click", () => {
    const dialog = document.getElementById("indexConfirmDialog");
    dialog.dataset.scope = document.getElementById("sourceScope").value;
    document.getElementById("indexConfirmText").textContent = `处理范围：${dialog.dataset.scope || "全部资料"}。将抽取资料并调用现有向量服务，更新检索索引；可能产生模型调用费用。此操作不更新离线 Topic 库。`;
    dialog.showModal();
});
document.getElementById("indexConfirmDialog").addEventListener("close", event => { if (event.target.returnValue === "confirm") startKnowledgeJob("index", event.target.dataset.scope); });
document.getElementById("watchlistRefresh").addEventListener("click", refreshWatchlists);
document.getElementById("watchlistForm").addEventListener("submit", createWatchlist);

function hasDraggedFiles(event) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function syncSidebarToggle() {
    const mobile = window.matchMedia("(max-width: 850px)").matches;
    if (!mobile) elements.sidebar.classList.remove("open");
    const expanded = mobile
        ? elements.sidebar.classList.contains("open")
        : !elements.appShell.classList.contains("sidebar-collapsed");
    const label = mobile
        ? expanded ? "关闭导航" : "打开导航"
        : expanded ? "折叠导航" : "展开导航";
    elements.menu.setAttribute("aria-expanded", String(expanded));
    elements.menu.setAttribute("aria-label", label);
    elements.menu.title = label;
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
    if (files.length) {
        if (state.view === "knowledge") {
            if (document.getElementById("knowledgeUploadButton").disabled) { toast("知识库上传接口当前不可用", true); return; }
            const transfer = new DataTransfer(); files.forEach(file => transfer.items.add(file));
            document.getElementById("knowledgeFileInput").files = transfer.files;
            document.getElementById("knowledgeUploadForm").classList.remove("hidden");
        }
        else if (state.view === "chat") uploadDocuments(files);
    }
}

function hideDropOverlay() {
    state.dragDepth = 0;
    elements.dropOverlay.classList.remove("visible");
    elements.dropOverlay.setAttribute("aria-hidden", "true");
}

async function initialize() {
    syncSidebarToggle();
    if (state.busy) return;
    setBusy(true, "正在建立会话");
    refreshIcons();
    try {
        const data = await api("/api/agent/sessions", { method: "POST" });
        saveConversation();
        state.sessionId = data.session_id;
        state.awaiting = false; state.documents = []; state.projectSlideFiles = []; state.selectedPptId = ""; state.artifacts = [];
        state.conversations.set(state.sessionId, { title: "新对话", nodes: [] });
        elements.conversation.replaceChildren(welcomeTemplate.cloneNode(true));
        elements.input.value = "";
        renderDocumentState(); renderArtifacts(); renderRecentConversations(); resizeComposer();
        setView("chat"); refreshIcons();
        setSessionStatus("会话已连接", false);
        elements.input.focus();
    } catch (error) {
        setSessionStatus("连接失败", true);
        toast(error.message, true);
    } finally { setBusy(false); }
}

async function api(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
        let message = `请求失败 (${response.status})`;
        if ([404, 501, 503].includes(response.status)) {
            if (url.startsWith("/api/knowledge")) message = `会议知识库管理接口不可用或尚未接入 (${response.status})`;
            else if (url.startsWith("/api/agent")) message = `Agent 会话服务不可用 (${response.status})`;
        }
        try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || message); } catch (_) { /* no JSON body */ }
        const error = new Error(message); error.status = response.status; throw error;
    }
    return response.status === 204 ? null : response.json();
}

async function sendMessage(rawMessage) {
    const message = rawMessage.trim();
    if (!message || state.busy || state.awaiting || !state.sessionId) return;
    const projectSlideRequest = /project\s*slide|项目页|项目幻灯片|第一页|第\s*1\s*页/i.test(message) && /生成|制作|创建|generate|create/i.test(message);
    const uploadedPptx = state.projectSlideFiles.find(file => file.file_id === state.selectedPptId);
    if (projectSlideRequest && !uploadedPptx && !/\[file_id:/i.test(message)) { toast("请先上传并选择 Project Slide 源 PPTX", true); document.getElementById("contextPanel").classList.add("open"); return; }
    const agentMessage = projectSlideRequest && uploadedPptx && !/\[file_id:/i.test(message)
        ? `${message}\n[file_id: ${uploadedPptx.file_id}]`
        : message;
    elements.input.value = "";
    resizeComposer();
    hideWelcome();
    addUserMessage(message);
    setBusy(true, "Agent 正在处理请求");
    showThinking();
    try {
        const response = await api(`/api/agent/sessions/${state.sessionId}/messages`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: agentMessage })
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

async function uploadDocuments(files) {
    if (!state.sessionId) {
        toast("会话尚未建立，请稍后重试", true);
        return;
    }
    if (state.busy || state.awaiting) {
        toast(state.awaiting ? "请先完成当前确认操作" : "当前任务处理中，请稍后上传", true);
        return;
    }
    const pptxFiles = files.filter(file => file.name.toLowerCase().endsWith(".pptx"));
    const documents = files.filter(file => !file.name.toLowerCase().endsWith(".pptx"));
    setBusy(true, pptxFiles.length && documents.length ? "正在上传文件" : pptxFiles.length ? "正在上传 PPTX" : "正在解析文档");
    elements.progress.classList.remove("hidden");
    elements.progressText.textContent = `正在处理 ${files.length} 个文件`;
    try {
        const uploadedSlides = [];
        for (const file of pptxFiles) {
            const formData = new FormData();
            formData.append("file", file, file.name);
            const uploaded = await api(`/api/agent/sessions/${state.sessionId}/project-slides/files`, { method: "POST", body: formData });
            uploadedSlides.push(uploaded);
            state.projectSlideFiles.push(uploaded);
            state.selectedPptId = uploaded.file_id;
            hideWelcome(); renderDocumentState();
        }
        if (uploadedSlides.length) {
            addSystemMessage(`已上传 Project Slide 数据 PPT：${uploadedSlides.map(file => `${file.filename}（file_id: ${file.file_id}）`).join("、")}`);
        }
        if (documents.length) {
            const formData = new FormData();
            documents.forEach(file => formData.append("files", file, file.name));
            const response = await api(`/api/agent/sessions/${state.sessionId}/documents`, { method: "POST", body: formData });
            state.documents = response.documents || [];
            renderDocumentState();
            hideWelcome();
            const failed = response.errors?.length || 0;
            const succeeded = documents.length - failed;
            if (succeeded) addSystemMessage(`新增 ${succeeded} 份文档，当前共 ${state.documents.length} 份。`);
            response.errors?.forEach(error => toast(`${error.file_name}：${error.message}`, true));
            if (succeeded) toast("文档解析完成");
        }
        if (window.innerWidth <= 850) elements.sidebar.classList.remove("open");
    } catch (error) {
        toast(error.message, true);
    } finally {
        renderDocumentState();
        elements.progress.classList.add("hidden");
        setBusy(false);
    }
}

async function deleteDocument(documentId) {
    if (state.busy || state.awaiting) return;
    try {
        const response = await api(`/api/agent/sessions/${state.sessionId}/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
        state.documents = response.documents || [];
        renderDocumentState();
        addSystemMessage("已删除文档");
    } catch (error) { toast(error.message, true); }
}

async function clearDocuments() {
    if (!state.documents.length || state.busy || state.awaiting) return;
    try {
        const response = await api(`/api/agent/sessions/${state.sessionId}/documents`, { method: "DELETE" });
        state.documents = response.documents || [];
        renderDocumentState();
        addSystemMessage("已清空当前文档");
    } catch (error) { toast(error.message, true); }
}

function handleAgentResponse(response) {
    removeThinking();
    for (const file of response.downloads || []) {
        if (safeDownloadUrl(file.url) && !state.artifacts.some(item => item.url === file.url)) state.artifacts.push({ ...file, created: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) });
    }
    renderArtifacts();
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
    if (interrupt.warnings?.length) {
        const warning = document.createElement("div");
        warning.className = "confirmation-warning";
        warning.textContent = `部分文档未能提取：${interrupt.warnings.map(item => item.message).join("；")}`;
        panel.appendChild(warning);
    }
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
    if (query.sources?.length) {
        const sources = document.createElement("div");
        sources.className = "query-sources";
        sources.textContent = `来源：${query.sources.map(source => source.file_name).join("、")}`;
        item.appendChild(sources);
    }
    const flags = [];
    if (query.broad_query) flags.push("条件较宽泛，可能返回较多记录");
    if (flags.length) {
        const warning = document.createElement("div");
        warning.className = "query-warning";
        warning.textContent = flags.join("；");
        item.appendChild(warning);
    }
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
    const completed = await resume({ action, query });
    if (!completed) {
        panel.querySelectorAll("button,input").forEach(control => { control.disabled = false; });
        panel.style.opacity = "";
    }
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
        return true;
    } catch (error) {
        removeThinking(); state.awaiting = true; toast(error.message, true); addAgentMessage(`操作未完成：${error.message}`);
        return false;
    } finally { setBusy(false); }
}

function addUserMessage(text) {
    const entry = state.conversations.get(state.sessionId);
    if (entry && entry.title === "新对话") { entry.title = text.slice(0, 35); renderRecentConversations(); }
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
        downloads.filter(file => safeDownloadUrl(file.url)).forEach(file => { const link = document.createElement("a"); link.className = "download-link"; link.href = file.url; link.download = file.file_name; link.textContent = `下载 ${file.file_name}`; list.appendChild(link); });
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
    const avatar = document.createElement("div"); avatar.className = "avatar"; avatar.textContent = "R";
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
    const pattern = /(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|`[^`]+`)/g;
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
        element.appendChild(document.createTextNode(text.slice(offset, match.index)));
        const value = match[0];
        const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(value);
        const inline = document.createElement(linkMatch ? "a" : value.startsWith("**") ? "strong" : "code");
        inline.textContent = linkMatch ? linkMatch[1] : value.startsWith("**") ? value.slice(2, -2) : value.slice(1, -1);
        if (linkMatch) {
            try { const url = new URL(linkMatch[2], location.origin); if (["http:", "https:"].includes(url.protocol)) { inline.href = url.href; inline.rel = "noopener noreferrer"; } } catch (_) { /* invalid link */ }
        }
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
function showThinking() { removeThinking(); const row = createAgentRow(); row.classList.add("thinking-row"); row.setAttribute("aria-hidden", "true"); row.querySelector(".agent-content").innerHTML = '<div class="thinking"><span></span><span></span><span></span></div>'; row.querySelector(".message-time").remove(); elements.conversation.appendChild(row); state.thinking = row; scrollConversation(); }
function removeThinking() { if (state.thinking) state.thinking.remove(); state.thinking = null; }
function setBusy(busy, activity = "就绪") {
    state.busy = busy;
    clearInterval(state.activityTimer);
    state.activityTimer = null;
    elements.activity.textContent = state.awaiting && !busy ? "等待确认" : activity;
    elements.activityElapsed.textContent = "";
    if (busy) {
        state.activityStartedAt = Date.now();
        state.activityTimer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - state.activityStartedAt) / 1000);
            const minutes = Math.floor(elapsed / 60);
            const seconds = elapsed % 60;
            elements.activityElapsed.textContent = minutes ? `· ${minutes}分${seconds}秒` : `· ${seconds}秒`;
        }, 1000);
    }
    updateControls();
}
function updateControls() { const disabled = state.busy || state.awaiting || !state.sessionId; elements.send.disabled = disabled; elements.attach.disabled = disabled; elements.upload.disabled = disabled; elements.clear.disabled = disabled; elements.input.disabled = state.awaiting; document.getElementById("newConversationButton").disabled = state.busy; document.getElementById("pptSourceSelect").disabled = disabled; }
function resizeComposer() { elements.input.style.height = "auto"; elements.input.style.height = `${Math.min(elements.input.scrollHeight, 150)}px`; elements.chars.textContent = `${elements.input.value.length} / 10000`; }
function hideWelcome() { elements.conversation.querySelector(".welcome")?.remove(); }
function scrollConversation() { requestAnimationFrame(() => { elements.conversation.scrollTop = elements.conversation.scrollHeight; }); }
function setSessionStatus(text, error) { elements.sessionStatus.textContent = text; elements.statusDot.classList.toggle("error", error); }
function renderDocumentState() {
    elements.documentCount.textContent = state.documents.length + state.projectSlideFiles.length;
    elements.clear.classList.toggle("hidden", !state.documents.length);
    elements.documentList.replaceChildren();
    const select = document.getElementById("pptSourceSelect"); select.replaceChildren();
    state.projectSlideFiles.forEach(file => { const option = new Option(file.filename, file.file_id); select.add(option); });
    select.value = state.selectedPptId;
    document.getElementById("pptSelectionField").classList.toggle("hidden", !state.projectSlideFiles.length);
    document.getElementById("composerSelection").textContent = state.projectSlideFiles.find(file => file.file_id === state.selectedPptId)?.filename || "";
    if (!state.documents.length && !state.projectSlideFiles.length) elements.documentList.appendChild(textNode("div", "暂无资料", "document-empty"));
    state.documents.forEach(documentItem => {
        const row = document.createElement("div"); row.className = "document-item";
        const icon = document.createElement("span"); icon.className = "document-icon"; icon.appendChild(lucideIcon("file-text"));
        const copy = document.createElement("div"); copy.className = "document-copy";
        const name = document.createElement("strong"); name.textContent = documentItem.file_name;
        const status = document.createElement("span"); status.textContent = "已解析"; copy.append(name, status);
        const remove = iconButton("x", `删除 ${documentItem.file_name}`); remove.dataset.documentId = documentItem.document_id;
        row.append(icon, copy, remove); elements.documentList.appendChild(row);
    });
    state.projectSlideFiles.forEach(file => {
        const row = textNode("div", "", `document-item${file.file_id === state.selectedPptId ? " selected" : ""}`);
        const icon = textNode("span", "", "document-icon"); icon.appendChild(lucideIcon("presentation"));
        const copy = textNode("div", "", "document-copy"); copy.append(textNode("strong", file.filename), textNode("span", "Project Slide 源文件"));
        row.append(icon, copy); elements.documentList.append(row);
    });
    refreshIcons();
}
function toast(message, error = false) { const item = document.createElement("div"); item.className = `toast${error ? " error" : ""}`; item.textContent = message; elements.toasts.appendChild(item); window.setTimeout(() => item.remove(), 4200); }

function textNode(tag, text, className = "") { const node = document.createElement(tag); node.textContent = text; node.className = className; return node; }
function lucideIcon(name) { const icon = document.createElement("i"); icon.dataset.lucide = name; return icon; }
function refreshIcons() { window.lucide?.createIcons({ attrs: { "aria-hidden": "true" } }); }
function iconButton(name, title, action) { const button = textNode("button", "", "icon-button"); button.type = "button"; button.title = title; button.setAttribute("aria-label", title); button.append(lucideIcon(name)); if (action) button.addEventListener("click", action); return button; }
function safeDownloadUrl(value) { try { const url = new URL(value, location.origin); return url.origin === location.origin && url.pathname.startsWith("/api/agent/sessions/"); } catch (_) { return false; } }
function saveConversation() {
    if (!state.sessionId) return;
    const entry = state.conversations.get(state.sessionId);
    Object.assign(entry, { nodes: Array.from(elements.conversation.childNodes), documents: state.documents, projectSlideFiles: state.projectSlideFiles, selectedPptId: state.selectedPptId, artifacts: state.artifacts, awaiting: state.awaiting, draft: elements.input.value, scroll: elements.conversation.scrollTop });
}
function switchConversation(sessionId) {
    if (state.busy || sessionId === state.sessionId) return;
    saveConversation();
    const entry = state.conversations.get(sessionId);
    if (!entry) return;
    Object.assign(state, { sessionId, documents: entry.documents || [], projectSlideFiles: entry.projectSlideFiles || [], selectedPptId: entry.selectedPptId || "", artifacts: entry.artifacts || [], awaiting: !!entry.awaiting });
    elements.conversation.replaceChildren(...entry.nodes);
    elements.input.value = entry.draft || "";
    setView("chat"); setBusy(false); renderRecentConversations(); renderDocumentState(); renderArtifacts(); resizeComposer();
    requestAnimationFrame(() => { elements.conversation.scrollTop = entry.scroll || 0; });
}
function renderRecentConversations() {
    const list = document.getElementById("recentConversationList"); list.replaceChildren();
    const query = document.getElementById("conversationSearch").value.toLowerCase();
    [...state.conversations].reverse().filter(([, entry]) => entry.title.toLowerCase().includes(query)).forEach(([sessionId, entry]) => { const button = textNode("button", entry.title, `recent-item${sessionId === state.sessionId ? " active" : ""}`); button.type = "button"; button.dataset.session = sessionId; button.title = entry.title; list.append(button); });
}
function renderArtifacts() {
    const list = document.getElementById("artifactList"); list.replaceChildren();
    if (!state.artifacts.length) list.append(textNode("div", "暂无生成文件", "document-empty"));
    [...state.artifacts].reverse().forEach(file => { const link = textNode("a", "", "artifact-item"); link.href = file.url; link.download = file.file_name; const copy = document.createElement("span"); copy.append(textNode("strong", file.file_name), textNode("small", file.created)); link.append(lucideIcon("file-check"), copy, lucideIcon("download")); list.append(link); }); refreshIcons();
}
function setView(view) {
    state.view = view;
    ["chat", "knowledge", "watchlists"].forEach(name => document.getElementById(`${name}View`).classList.toggle("hidden", name !== view));
    document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === view));
    document.getElementById("pageTitle").textContent = { chat: "对话助手", knowledge: "会议知识库", watchlists: "DMF 关注清单" }[view];
    document.getElementById("materialsToggle").classList.toggle("hidden", view !== "chat");
    elements.sidebar.classList.remove("open");
    syncSidebarToggle();
    clearTimeout(state.knowledgeTimer);
    if (view === "knowledge") refreshKnowledge();
    if (view === "watchlists") refreshWatchlists();
}
function showError(id, message = "") { const node = document.getElementById(id); node.textContent = message; node.classList.toggle("hidden", !message); }
function setKnowledgeTab(tab) {
    ["sources", "jobs", "overview"].forEach(name => document.getElementById(`${name}Tab`).classList.toggle("hidden", name !== tab));
    document.querySelectorAll("[data-knowledge-tab]").forEach(button => {
        const active = button.dataset.knowledgeTab === tab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
        button.tabIndex = active ? 0 : -1;
    });
}
async function refreshKnowledge() {
    if (state.knowledgeLoading) return;
    state.knowledgeLoading = true; clearTimeout(state.knowledgeTimer);
    document.getElementById("knowledgeRefresh").disabled = true;
    ["parseCheckButton", "startIndexButton", "knowledgeUploadButton"].forEach(id => document.getElementById(id).disabled = true);
    try {
        const [inventory, status, tasks] = await Promise.all([api("/api/knowledge/sources"), api("/api/knowledge/status"), api("/api/knowledge/jobs")]);
        state.sources = inventory.sources;
        const scope = document.getElementById("sourceScope"); const selected = scope.value;
        scope.replaceChildren(new Option("全部资料", ""), ...inventory.scopes.map(value => new Option(value, value)));
        if (inventory.scopes.includes(selected)) scope.value = selected;
        renderSources(); renderKnowledgeJobs(tasks.jobs);
        await refreshTopics(true);
        document.getElementById("indexedFiles").textContent = status.file_count ?? "—";
        document.getElementById("indexedChunks").textContent = status.chunk_count ?? "—";
        document.getElementById("vectorStatus").textContent = status.updating ? "更新中" : status.embeddings_ready ? "就绪" : "未就绪";
        const details = document.getElementById("indexDetails"); details.replaceChildren();
        [["最近更新", formatTime(status.updated_at)], ["Embedding", status.embedding_model || "未配置"], ["资料目录", status.sources_dir || "—"], ["索引目录", status.index_dir || "—"], ["Topic 库", "独立离线维护，不随此次入库更新"]].forEach(([label, value]) => details.append(textNode("dt", label), textNode("dd", value)));
        const active = tasks.jobs.some(job => ["queued", "running"].includes(job.status));
        ["parseCheckButton", "startIndexButton", "knowledgeUploadButton"].forEach(id => document.getElementById(id).disabled = active);
        showError("knowledgeError");
        if (active && state.view === "knowledge") state.knowledgeTimer = setTimeout(refreshKnowledge, 2000);
    } catch (error) {
        showError("knowledgeError", error.message); document.getElementById("sourceCount").textContent = "资料读取失败";
        document.getElementById("knowledgeUploadForm").classList.add("hidden");
    }
    finally { state.knowledgeLoading = false; document.getElementById("knowledgeRefresh").disabled = false; }
}
function badge(stateName, label) { return textNode("span", label, `badge ${stateName}`); }
function renderSources() {
    const query = document.getElementById("sourceSearch").value.toLowerCase(); const scope = document.getElementById("sourceScope").value;
    const sources = state.sources.filter(file => (!scope || file.path.startsWith(scope + "/")) && file.path.toLowerCase().includes(query));
    const body = document.getElementById("sourceTableBody"); body.replaceChildren();
    const labels = { indexed: "已索引", pending: "待入库", changed: "已变更", missing: "源文件缺失" };
    sources.forEach(file => { const row = document.createElement("tr"); const first = document.createElement("td"); const name = textNode("div", "", "file-name"); const copy = document.createElement("span"); copy.append(textNode("span", file.filename), textNode("small", file.path)); name.append(lucideIcon("file-text"), copy); first.append(name); const status = document.createElement("td"); status.append(badge(file.state, labels[file.state] || file.state)); row.append(first, textNode("td", file.format), status, textNode("td", String(file.chunk_count))); body.append(row); });
    document.getElementById("sourceCount").textContent = `${sources.length} 份资料`;
    document.getElementById("sourceEmpty").classList.toggle("hidden", sources.length > 0); refreshIcons();
}
function renderKnowledgeJobs(jobs) {
    document.getElementById("jobCount").textContent = jobs.length;
    document.getElementById("jobsStatus").textContent = "最近持久化任务记录 · 服务重启后可继续查看";
    const list = document.getElementById("knowledgeJobs"); list.replaceChildren();
    if (!jobs.length) list.append(textNode("div", "暂无入库任务", "empty-state"));
    const labels = { queued: "排队中", running: "处理中", succeeded: "已完成", failed: "失败", interrupted: "已中断" };
    jobs.forEach(job => {
        const row = textNode("article", "", "job-row");
        const header = document.createElement("header");
        const kind = job.kind || (job.inspect_only ? "check" : "index");
        const terminal = ["succeeded", "failed", "interrupted"].includes(job.status);
        const finishedAt = job.finished_at || (terminal ? job.updated_at : "");
        header.append(textNode("strong", `${kind === "index" ? "索引入库" : "解析检查"} · ${job.scope || "全部资料"}`), badge(job.status, labels[job.status] || job.status));
        row.append(header, textNode("p", `${formatTime(job.created_at)}${finishedAt ? " → " + formatTime(finishedAt) : ""}`));
        if (job.message) row.append(textNode("p", `${job.stage ? job.stage + " · " : ""}${job.message}`, job.status === "failed" ? "notice error" : ""));
        if (job.error) row.append(textNode("p", job.error, "notice error"));
        if (job.result?.files) row.append(textNode("pre", job.result.files.map(file => {
            const name = file.path || file.source_ref || "未知文件";
            const count = file.chunks ?? file.chunk_count ?? 0;
            return `${name}：${file.status === "failed" ? "失败（" + (file.error || file.reason || "详见任务记录") + "）" : file.status === "empty" || file.status === "skipped_no_text" ? "无可提取文本" : count + " 个 Chunk"}`;
        }).join("\n")));
        else if (job.result) row.append(textNode("pre", `新增 ${job.result.added?.length || 0} · 更新 ${job.result.updated?.length || 0} · 移除 ${job.result.removed?.length || 0} · 跳过 ${job.result.skipped?.length || 0}\n总 Chunk ${job.result.chunk_count ?? "—"} · 本次向量化 ${job.result.embedded_texts ?? job.result.embedded_chunks ?? 0}`));
        list.append(row);
    });
}
async function refreshTopics(reset) {
    const requestId = ++state.topicRequestId;
    if (reset) {
        state.topicOffset = 0;
        state.topics = [];
        state.topicTotal = 0;
        renderTopics();
    }
    const loadMore = document.getElementById("topicsLoadMore");
    loadMore.disabled = true;
    document.getElementById("topicsStatus").textContent = "正在读取…";
    try {
        const params = new URLSearchParams({
            level: document.getElementById("topicLevel").value,
            scope: document.getElementById("sourceScope").value,
            offset: String(state.topicOffset),
            limit: "50"
        });
        const result = await api(`/api/knowledge/topics?${params}`);
        if (requestId !== state.topicRequestId) return;
        state.topics = [...state.topics, ...result.topics];
        state.topicOffset += result.topics.length;
        state.topicTotal = result.total;
        showError("topicsError");
        renderTopics();
        document.getElementById("topicsStatus").textContent = `${result.level === "meeting" ? "会议整合" : "单个文件"} · 当前可见资料`;
    } catch (error) {
        if (requestId !== state.topicRequestId) return;
        showError("topicsError", error.message);
        document.getElementById("topicsStatus").textContent = "Topic 读取失败";
        renderTopics();
    } finally {
        if (requestId === state.topicRequestId) loadMore.disabled = false;
    }
}
function renderTopics() {
    const list = document.getElementById("knowledgeTopics");
    list.replaceChildren();
    if (!state.topics.length) list.append(textNode("div", "暂无已发布 Topic", "empty-state"));
    const labels = { background: "背景", for_endorsement: "请求", decision: "决议", todo: "行动" };
    state.topics.forEach(topic => {
        const article = textNode("article", "", "topic-row");
        const heading = document.createElement("header");
        heading.append(textNode("h4", topic.title || "未命名议题"));
        const sources = topic.source_refs || [topic.source_ref].filter(Boolean);
        article.append(heading, textNode("p", `${topic.meeting_scope ? topic.meeting_scope + " · " : ""}${sources.join("、")}`, "topic-sources"));
        const evidenceById = new Map((topic.evidence || []).map(item => [item.chunk_id, item]));
        let hasFacts = false;
        for (const [field, label] of Object.entries(labels)) {
            const facts = topic[field] || [];
            if (!facts.length) continue;
            const section = document.createElement("section");
            section.append(textNode("h5", label));
            facts.forEach(fact => {
                hasFacts = true;
                const item = document.createElement("div");
                item.className = "topic-fact";
                item.append(textNode("p", fact.text || ""));
                const evidence = evidenceById.get(fact.evidence_id);
                if (fact.quote) item.append(textNode("blockquote", `“${fact.quote}”`));
                if (evidence) item.append(textNode("small", `${evidence.source_ref}${evidence.loc ? " · " + evidence.loc : ""}`, "topic-evidence-source"));
                section.append(item);
            });
            article.append(section);
        }
        if (!hasFacts && topic.evidence?.length) {
            const section = document.createElement("section");
            section.append(textNode("h5", "支撑证据"));
            topic.evidence.forEach(item => section.append(textNode("blockquote", `${item.source_ref}${item.loc ? " · " + item.loc : ""}\n${item.text}`)));
            article.append(section);
        }
        (topic.conflicts || []).forEach(conflict => article.append(textNode("p", `来源冲突提示：${conflict.reason}`, "topic-conflict")));
        list.append(article);
    });
    document.getElementById("topicsCount").textContent = `显示 ${state.topics.length} / ${state.topicTotal} 个 Topic`;
    const more = document.getElementById("topicsLoadMore");
    more.classList.toggle("hidden", state.topicOffset >= state.topicTotal);
    refreshIcons();
}
async function uploadKnowledge(event) {
    event.preventDefault();
    const form = event.target;
    const filesInput = document.getElementById("knowledgeFileInput");
    const files = [...filesInput.files]; if (!files.length) { toast("请选择知识库资料", true); return; }
    const button = form.querySelector('[type="submit"]');
    const status = document.getElementById("knowledgeUploadStatus");
    button.disabled = true;
    filesInput.disabled = true;
    document.getElementById("knowledgeUploadCancel").disabled = true;
    form.setAttribute("aria-busy", "true");
    status.textContent = `正在上传 ${files.length} 份资料…`;
    const data = new FormData(); data.append("folder", document.getElementById("knowledgeFolder").value.trim()); files.forEach(file => data.append("files", file));
    try { const result = await api("/api/knowledge/sources", { method: "POST", body: data }); toast(`已上传 ${result.uploaded.length} 份资料，等待入库`); form.reset(); status.textContent = ""; form.classList.add("hidden"); await refreshKnowledge(); }
    catch (error) { showError("knowledgeError", error.message); status.textContent = "上传失败，可重试"; }
    finally {
        button.disabled = false;
        filesInput.disabled = false;
        document.getElementById("knowledgeUploadCancel").disabled = false;
        form.setAttribute("aria-busy", "false");
    }
}
async function startKnowledgeJob(kind, scope = document.getElementById("sourceScope").value) {
    document.getElementById("parseCheckButton").disabled = true; document.getElementById("startIndexButton").disabled = true;
    try { await api("/api/knowledge/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, scope, confirmed: kind === "index" }) }); setKnowledgeTab("jobs"); await refreshKnowledge(); }
    catch (error) { showError("knowledgeError", error.message); document.getElementById("parseCheckButton").disabled = false; document.getElementById("startIndexButton").disabled = false; }
}
function formatTime(value) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—"; }
async function refreshWatchlists() {
    const refresh = document.getElementById("watchlistRefresh"); refresh.disabled = true;
    try {
        const rows = await api("/api/watchlists"); const body = document.getElementById("watchlistTableBody"); body.replaceChildren();
        rows.forEach(item => { const row = document.createElement("tr"); const status = document.createElement("td"); status.append(badge(item.status, item.status === "active" ? "监测中" : "已暂停")); const actions = textNode("div", "", "row-actions");
            actions.append(iconButton("play", "立即检查", event => mutateWatchlist(item.id, "/runs", "POST", null, event.currentTarget)), iconButton(item.status === "active" ? "pause" : "circle-play", item.status === "active" ? "暂停监测" : "恢复监测", event => mutateWatchlist(item.id, "", "PATCH", { status: item.status === "active" ? "paused" : "active" }, event.currentTarget)), iconButton("history", "检查与变更记录", () => showWatchlistHistory(item)), iconButton("trash-2", "删除关注", event => { if (confirm(`删除 DMF ${item.dmf_no} 的关注项？`)) mutateWatchlist(item.id, "", "DELETE", null, event.currentTarget); }));
            const actionCell = document.createElement("td"); actionCell.append(actions); row.append(textNode("td", item.dmf_no), status, textNode("td", `${item.interval_hours} 小时`), textNode("td", formatTime(item.next_run_at)), textNode("td", formatTime(item.last_run_at)), textNode("td", String(item.failure_count)), actionCell); body.append(row);
        });
        document.getElementById("watchlistEmpty").classList.toggle("hidden", rows.length > 0); showError("watchlistError"); document.querySelectorAll("#watchlistForm input,#watchlistForm button").forEach(control => control.disabled = false); refreshIcons();
        try { renderWatchlistMonitoringStatus(await api("/api/watchlists/status")); showError("watchlistStatusError"); }
        catch (error) { showError("watchlistStatusError", error.message); }
    } catch (error) { showError("watchlistError", error.message); if (error.status === 503) document.querySelectorAll("#watchlistForm input,#watchlistForm button").forEach(control => control.disabled = true); }
    finally { refresh.disabled = false; }
}
function renderWatchlistMonitoringStatus(summary) {
    document.getElementById("watchlistActiveValue").textContent = `${summary.active_watchlists} / ${summary.paused_watchlists}`;
    document.getElementById("watchlistDueValue").textContent = `${summary.due_watchlists} / ${summary.running_runs}`;
    document.getElementById("watchlistSuccessValue").textContent = summary.success_rate_7d === null ? "—" : `${(summary.success_rate_7d * 100).toFixed(1)}%`;
    document.getElementById("watchlistNotificationValue").textContent = `${summary.pending_notifications} / ${summary.failed_notifications}`;
    const p50 = summary.p50_latency_ms_7d === null ? "—" : `${Math.round(summary.p50_latency_ms_7d)} ms`;
    const p95 = summary.p95_latency_ms_7d === null ? "—" : `${Math.round(summary.p95_latency_ms_7d)} ms`;
    const latest = summary.latest_run_status ? `${summary.latest_run_status} · ${formatTime(summary.latest_run_started_at)}` : "暂无检查记录";
    document.getElementById("watchlistStatusDetails").textContent = `最近任务：${latest} · 近 7 天失败任务：${summary.failed_runs_7d} · 执行耗时 P50/P95：${p50}/${p95} · 未确认事件：${summary.unread_events} · 发送中：${summary.sending_notifications}`;
}
async function createWatchlist(event) {
    event.preventDefault(); const button = event.target.querySelector("button"); button.disabled = true;
    try { await api("/api/watchlists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dmf_no: document.getElementById("watchlistDmf").value.trim(), interval_hours: Number(document.getElementById("watchlistInterval").value) }) }); document.getElementById("watchlistDmf").value = ""; await refreshWatchlists(); }
    catch (error) { showError("watchlistError", error.message); } finally { button.disabled = false; }
}
async function mutateWatchlist(id, suffix, method, payload, button) {
    button.disabled = true;
    try { const result = await api(`/api/watchlists/${encodeURIComponent(id)}${suffix}`, { method, ...(payload ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) } : {}) }); if (suffix === "/runs") toast(`检查结果：${result.status}${result.error_message ? " · " + result.error_message : ""}`); await refreshWatchlists(); }
    catch (error) { showError("watchlistError", error.message); } finally { button.disabled = false; }
}
async function showWatchlistHistory(item) {
    try {
        const [runs, events] = await Promise.all([api(`/api/watchlists/${item.id}/runs`), api(`/api/watchlists/${item.id}/events`)]);
        const dialog = document.createElement("dialog"); dialog.append(textNode("h2", `DMF ${item.dmf_no}`), textNode("h3", "检查记录"));
        dialog.append(textNode("pre", runs.length ? runs.map(run => { const duration = run.ended_at ? Math.max(0, Date.parse(run.ended_at) - Date.parse(run.started_at)) : null; const elapsed = duration === null ? "未完成" : `${Math.round(duration)} ms`; return `${formatTime(run.started_at)} · ${run.status} · ${elapsed}${run.error_message ? "\n" + run.error_message : ""}`; }).join("\n") : "暂无检查记录"));
        dialog.append(textNode("h3", "变更事件"), textNode("pre", events.length ? events.map(event => `${formatTime(event.created_at)} · ${event.event_type} · ${event.status}\n${JSON.stringify(event.after_payload)}`).join("\n") : "暂无变更事件"));
        dialog.append(iconButton("x", "关闭记录", () => dialog.close())); dialog.addEventListener("close", () => dialog.remove()); document.body.append(dialog); refreshIcons(); dialog.showModal();
    } catch (error) { showError("watchlistError", error.message); }
}
