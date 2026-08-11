const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");

const selectedSection = document.getElementById(
    "selectedSection"
);
const selectedFileList = document.getElementById(
    "selectedFileList"
);

const clearButton = document.getElementById(
    "clearButton"
);
const startButton = document.getElementById(
    "startButton"
);
const uploadMessage = document.getElementById(
    "uploadMessage"
);

const taskSection = document.getElementById(
    "taskSection"
);
const taskMessage = document.getElementById(
    "taskMessage"
);
const taskState = document.getElementById(
    "taskState"
);
const taskIdText = document.getElementById(
    "taskIdText"
);
const progressBar = document.getElementById(
    "progressBar"
);
const resultFileList = document.getElementById(
    "resultFileList"
);

const previewSection = document.getElementById(
    "previewSection"
);
const previewFileName = document.getElementById(
    "previewFileName"
);
const markdownPreview = document.getElementById(
    "markdownPreview"
);
const closePreviewButton = document.getElementById(
    "closePreviewButton"
);


let selectedFiles = [];
let currentTaskId = null;
let pollTimer = null;

/*
 * 标记当前是否有任务正在执行。
 * 防止解析过程中重复提交。
 */
let taskRunning = false;


const terminalTaskStates = new Set([
    "done",
    "failed",
    "partial_failed",
]);

const terminalFileStates = new Set([
    "done",
    "failed",
    "timeout",
    "error",
    "upload_failed",
]);


dropZone.addEventListener("click", () => {
    fileInput.click();
});


dropZone.addEventListener("keydown", (event) => {
    if (
        event.key === "Enter"
        || event.key === " "
    ) {
        event.preventDefault();
        fileInput.click();
    }
});


fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);

    // 允许用户再次选择同一文件
    fileInput.value = "";
});


dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
});


dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragging");
});


dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");

    addFiles(event.dataTransfer.files);
});


clearButton.addEventListener("click", () => {
    selectedFiles = [];
    renderSelectedFiles();
});


startButton.addEventListener("click", () => {
    startParseTask();
});


closePreviewButton.addEventListener(
    "click",
    () => {
        previewSection.classList.add("hidden");
        markdownPreview.textContent = "";
    }
);


function addFiles(fileList) {
    clearMessage();

    for (const file of Array.from(fileList)) {
        const duplicate = selectedFiles.some(
            (existingFile) =>
                existingFile.name.toLowerCase()
                === file.name.toLowerCase()
        );

        if (duplicate) {
            showMessage(
                `已忽略同名文件：${file.name}`,
                true
            );
            continue;
        }

        selectedFiles.push(file);
    }

    renderSelectedFiles();
}


function removeFile(index) {
    selectedFiles.splice(index, 1);
    renderSelectedFiles();
}


function renderSelectedFiles() {
    selectedFileList.innerHTML = "";

    if (selectedFiles.length === 0) {
        selectedSection.classList.add("hidden");
        startButton.disabled = true;
        return;
    }

    selectedSection.classList.remove("hidden");

    /*
     * 有文件，并且当前没有任务运行时，
     * 才允许点击“开始解析”。
     */
    startButton.disabled = taskRunning;

    selectedFiles.forEach((file, index) => {
        const item = document.createElement("li");
        item.className = "file-item";

        const main = document.createElement("div");
        main.className = "file-main";

        const name = document.createElement("div");
        name.className = "file-name";
        name.textContent = file.name;

        const meta = document.createElement("div");
        meta.className = "file-meta";
        meta.textContent = formatFileSize(file.size);

        const removeButton =
            document.createElement("button");

        removeButton.type = "button";
        removeButton.className = "remove-button";
        removeButton.textContent = "×";
        removeButton.title = "移除文件";

        removeButton.addEventListener(
            "click",
            (event) => {
                event.stopPropagation();
                removeFile(index);
            }
        );

        main.appendChild(name);
        main.appendChild(meta);

        item.appendChild(main);
        item.appendChild(removeButton);

        selectedFileList.appendChild(item);
    });
}


async function startParseTask() {
    if (taskRunning) {
        showMessage(
            "当前任务正在解析，请等待任务完成",
            true
        );
        return;
    }

    if (selectedFiles.length === 0) {
        showMessage("请先选择文件", true);
        return;
    }

    clearMessage();

    if (pollTimer !== null) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
    }

    taskRunning = true;
    startButton.disabled = true;
    startButton.textContent = "正在上传...";

    const formData = new FormData();

    for (const file of selectedFiles) {
        formData.append(
            "files",
            file,
            file.name
        );
    }

    try {
        const response = await fetch(
            "/api/tasks",
            {
                method: "POST",
                body: formData,
            }
        );

        if (!response.ok) {
            throw new Error(
                await readErrorMessage(response)
            );
        }

        const data = await response.json();

        currentTaskId = data.task_id;

        /*
         * 后端已经成功接收本批文件。
         * 立即清空本次选择，避免下次重复提交。
         */
        selectedFiles = [];
        renderSelectedFiles();

        taskSection.classList.remove("hidden");
        taskIdText.textContent = currentTaskId;

        taskMessage.textContent =
            data.message || "任务已经创建";

        updateTaskBadge("queued");
        progressBar.style.width = "0%";
        resultFileList.innerHTML = "";

        await pollTask();

    } catch (error) {
        /*
         * 上传没有成功时保留原文件，
         * 用户可以直接重试。
         */
        taskRunning = false;
        renderSelectedFiles();

        showMessage(
            error.message || "上传失败",
            true
        );

    } finally {
        startButton.textContent = "开始解析";
    }
}

async function pollTask() {
    if (!currentTaskId) {
        return;
    }

    try {
        const encodedTaskId =
            encodeURIComponent(currentTaskId);

        const response = await fetch(
            `/api/tasks/${encodedTaskId}`
        );

        if (!response.ok) {
            throw new Error(
                await readErrorMessage(response)
            );
        }

        const task = await response.json();

        renderTask(task);

        if (terminalTaskStates.has(task.state)) {
            pollTimer = null;
            taskRunning = false;

            /*
             * 如果用户在等待期间选择了新文件，
             * 任务完成后自动启用解析按钮。
             */
            renderSelectedFiles();
            return;
        }

        pollTimer = window.setTimeout(
            pollTask,
            2000
        );

    } catch (error) {
        taskMessage.textContent =
            error.message || "查询任务状态失败";

        updateTaskBadge("failed");

        pollTimer = null;
        taskRunning = false;

        renderSelectedFiles();
    }
}

function renderTask(task) {
    taskMessage.textContent = task.message;
    updateTaskBadge(task.state);

    resultFileList.innerHTML = "";

    const files = task.files || [];

    const finishedCount = files.filter(
        (file) => terminalFileStates.has(
            file.state
        )
    ).length;

    const progress = files.length > 0
        ? Math.round(
            finishedCount / files.length * 100
        )
        : 0;

    progressBar.style.width = `${progress}%`;

    for (const file of files) {
        const item = document.createElement("li");
        item.className = "result-item";

        const main = document.createElement("div");
        main.className = "file-main";

        const name = document.createElement("div");
        name.className = "file-name";
        name.textContent = file.file_name;

        const meta = document.createElement("div");
        meta.className = "file-meta";
        meta.textContent = file.message || file.state;

        main.appendChild(name);
        main.appendChild(meta);

        const actions = document.createElement("div");
        actions.className = "result-actions";

        const badge = createStatusBadge(
            file.state
        );

        actions.appendChild(badge);

        if (
            file.state === "done"
            && file.data_id
            && file.has_markdown
        ) {
            const previewButton =
                document.createElement("button");

            previewButton.type = "button";
            previewButton.className =
                "preview-button";
            previewButton.textContent = "预览";

            previewButton.addEventListener(
                "click",
                () => {
                    previewMarkdown(
                        task.task_id,
                        file.data_id,
                        file.file_name
                    );
                }
            );

            const downloadLink =
                document.createElement("a");

            downloadLink.className =
                "download-button";
            downloadLink.textContent = "下载";
            downloadLink.href =
                `/api/tasks/${task.task_id}`
                + `/files/${file.data_id}`
                + `/download`;

            actions.appendChild(previewButton);
            actions.appendChild(downloadLink);
        }

        item.appendChild(main);
        item.appendChild(actions);

        resultFileList.appendChild(item);
    }
}


async function previewMarkdown(
    taskId,
    dataId,
    fileName
) {
    previewSection.classList.remove("hidden");
    previewFileName.textContent = fileName;
    markdownPreview.textContent = "正在加载...";

    previewSection.scrollIntoView({
        behavior: "smooth",
        block: "start",
    });

    try {
        const response = await fetch(
            `/api/tasks/${taskId}`
            + `/files/${dataId}`
            + `/markdown`
        );

        if (!response.ok) {
            throw new Error(
                await readErrorMessage(response)
            );
        }

        markdownPreview.textContent =
            await response.text();

    } catch (error) {
        markdownPreview.textContent =
            error.message || "Markdown加载失败";
    }
}


function createStatusBadge(state) {
    const badge = document.createElement("span");

    badge.className =
        `status-badge status-${normalizeState(state)}`;

    badge.textContent = getStateLabel(state);

    return badge;
}


function updateTaskBadge(state) {
    taskState.className =
        `status-badge status-${normalizeState(state)}`;

    taskState.textContent = getStateLabel(state);
}


function normalizeState(state) {
    return String(state || "unknown")
        .toLowerCase()
        .replaceAll("_", "-");
}


function getStateLabel(state) {
    const labels = {
        queued: "等待处理",
        creating: "创建任务",
        "waiting-file": "等待上传",
        uploading: "正在上传",
        uploaded: "上传完成",
        pending: "排队中",
        parsing: "正在解析",
        running: "正在解析",
        converting: "格式转换",
        downloading: "下载结果",
        done: "解析完成",
        failed: "解析失败",
        timeout: "解析超时",
        partial_failed: "部分失败",
        upload_failed: "上传失败",
        error: "发生错误",
    };

    return labels[state] || state || "未知状态";
}


function formatFileSize(bytes) {
    if (bytes < 1024) {
        return `${bytes} B`;
    }

    if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(1)} KB`;
    }

    return (
        `${(bytes / 1024 / 1024).toFixed(1)} MB`
    );
}


function showMessage(message, isError = false) {
    uploadMessage.textContent = message;

    uploadMessage.classList.toggle(
        "error",
        isError
    );
}


function clearMessage() {
    uploadMessage.textContent = "";
    uploadMessage.classList.remove("error");
}


async function readErrorMessage(response) {
    try {
        const body = await response.json();

        return body.detail
            || body.message
            || `请求失败：HTTP ${response.status}`;

    } catch {
        return `请求失败：HTTP ${response.status}`;
    }
}