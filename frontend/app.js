const API = "/api";

let uploadedFiles = [];
let selectedFileId = null;
let chatHistory = [];

// ============================================================
// DOM ELEMENTS
// ============================================================

const fileInput = document.getElementById("fileInput");
const fileList = document.getElementById("fileList");
const fileCount = document.getElementById("fileCount");
const fileStatus = document.getElementById("fileStatus");

const messages = document.getElementById("messages");
const questionInput = document.getElementById("questionInput");
const sendBtn = document.getElementById("sendBtn");

const reportSpecs = document.getElementById("reportSpecs");
const generateReportBtn = document.getElementById("generateReportBtn");

const reportOutput = document.getElementById("reportOutput");
const reportText = document.getElementById("reportText");
const pdfLink = document.getElementById("pdfLink");
const docxLink = document.getElementById("docxLink");

const clearCacheBtn = document.getElementById("clearCacheBtn");


// ============================================================
// GENERAL HELPERS
// ============================================================

function getScope() {
    const selected = document.querySelector(
        'input[name="scope"]:checked'
    );

    return selected ? selected.value : "single";
}

function addToChatHistory(role, content) {

    chatHistory.push({
        role: role,
        content: content
    });

    // Keep only the latest 6 messages:
    // 3 user + 3 assistant messages
    if (chatHistory.length > 6) {
        chatHistory =
            chatHistory.slice(-6);
    }
}

function resetChatHistory() {
    chatHistory = [];
}

function getSelectedFileIds() {

    const scope = getScope();

    // -----------------------------
    // SINGLE FILE MODE
    // -----------------------------
    if (scope === "single") {

        return selectedFileId
            ? [selectedFileId]
            : [];
    }

    // -----------------------------
    // MULTI FILE MODE
    // -----------------------------

    return Array.from(
        document.querySelectorAll(
            ".file-checkbox:checked"
        )
    ).map(
        checkbox => checkbox.value
    );
}


function escapeHtml(value) {

    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function addMessage(
    role,
    text,
    extraHtml = ""
) {

    const message = document.createElement("div");

    message.className = `msg ${role}`;

    message.innerHTML = `
        <div>
            ${escapeHtml(text).replace(/\n/g, "<br>")}
        </div>

        ${extraHtml}
    `;

    messages.appendChild(message);

    messages.scrollTop =
        messages.scrollHeight;
}


function clearWelcomeMessage() {

    const welcome =
        document.querySelector(
            ".welcome-card"
        );

    if (welcome) {
        welcome.remove();
    }
}


function fileIcon(kind) {

    if (kind === "text") {
        return "📄";
    }

    return "📊";
}


function fileDescription(file) {

    if (file.kind === "text") {

        return `${fileIcon(file.kind)} ${
            file.chunks ?? 0
        } chunks`;
    }

    return `${fileIcon(file.kind)} ${
        Number(file.rows ?? 0).toLocaleString()
    } rows × ${
        file.columns ?? 0
    } columns`;
}


// ============================================================
// FILE SELECTION
// ============================================================

function renderFiles() {

    if (!fileList || !fileCount) {
        return;
    }

    fileCount.textContent =
        `${uploadedFiles.length} file${
            uploadedFiles.length === 1
                ? ""
                : "s"
        }`;

    // No files
    if (uploadedFiles.length === 0) {

        fileList.innerHTML = `
            <div class="empty-files">
                Upload one or more files to begin.
            </div>
        `;

        return;
    }

    const scope = getScope();

    // ========================================================
    // MULTIPLE FILE MODE
    // ========================================================

    if (scope === "multi") {

        fileList.innerHTML =
            uploadedFiles.map(file => {

                return `
                    <label
                        class="file-card multi-card"
                    >

                        <input
                            class="file-checkbox"
                            type="checkbox"
                            value="${escapeHtml(
                                file.file_id
                            )}"
                        >

                        <span
                            class="file-icon"
                        >
                            ${fileIcon(file.kind)}
                        </span>

                        <div
                            class="file-details"
                        >

                            <div
                                class="file-name"
                            >
                                ${escapeHtml(
                                    file.filename
                                )}
                            </div>

                            <div
                                class="file-meta"
                            >
                                ${fileDescription(
                                    file
                                )}
                            </div>

                        </div>

                        <button
                            class="remove-file"
                            data-delete="${escapeHtml(
                                file.file_id
                            )}"
                            type="button"
                            title="Remove file"
                        >
                            ×
                        </button>

                    </label>
                `;

            }).join("");

        return;
    }


    // ========================================================
    // SINGLE FILE MODE
    // ========================================================

    fileList.innerHTML =
        uploadedFiles.map(file => {

            const selected =
                selectedFileId ===
                file.file_id;

            return `
                <div
                    class="file-card ${
                        selected
                            ? "selected-file"
                            : ""
                    }"
                    data-select="${escapeHtml(
                        file.file_id
                    )}"
                >

                    <span
                        class="file-icon"
                    >
                        ${fileIcon(file.kind)}
                    </span>

                    <div
                        class="file-details"
                    >

                        <div
                            class="file-name"
                        >
                            ${escapeHtml(
                                file.filename
                            )}
                        </div>

                        <div
                            class="file-meta"
                        >
                            ${fileDescription(
                                file
                            )}
                        </div>

                    </div>

                    <button
                        class="remove-file"
                        data-delete="${escapeHtml(
                            file.file_id
                        )}"
                        type="button"
                        title="Remove file"
                    >
                        ×
                    </button>

                </div>
            `;

        }).join("");
}


// ============================================================
// STATUS
// ============================================================

function updateStatus() {

    if (!fileStatus) {
        return;
    }

    if (uploadedFiles.length === 0) {

        fileStatus.textContent =
            "Upload files and select a chat scope.";

        return;
    }

    const scope =
        getScope();

    // SINGLE
    if (scope === "single") {

        const file =
            uploadedFiles.find(
                item =>
                    item.file_id ===
                    selectedFileId
            );

        if (file) {

            fileStatus.textContent =
                `Single file: ${file.filename}`;

        } else {

            fileStatus.textContent =
                "Select a file.";

        }

        return;
    }


    // MULTI
    const selectedIds =
        getSelectedFileIds();

    fileStatus.textContent =
        selectedIds.length > 0
            ? `Multiple files: ${
                selectedIds.length
            } selected`
            : "Select one or more files.";
}


// ============================================================
// LOAD EXISTING FILES
// ============================================================

async function loadFiles() {

    try {

        const response =
            await fetch(
                `${API}/files`
            );

        if (!response.ok) {
            throw new Error(
                "Could not retrieve uploaded files."
            );
        }

        const data =
            await response.json();

        uploadedFiles =
            data.files || [];


        // Automatically select last file
        // if nothing is selected yet.
        if (
            !selectedFileId &&
            uploadedFiles.length > 0
        ) {

            selectedFileId =
                uploadedFiles[
                    uploadedFiles.length - 1
                ].file_id;
        }

        renderFiles();
        updateStatus();

    } catch (error) {

        console.error(
            "File loading error:",
            error
        );

        if (fileStatus) {

            fileStatus.textContent =
                "Backend not connected.";

        }
    }
}


// ============================================================
// MULTIPLE FILE UPLOAD
// ============================================================

if (fileInput) {

    fileInput.addEventListener(
        "change",
        async () => {

            const files =
                Array.from(
                    fileInput.files || []
                );

            if (files.length === 0) {
                return;
            }

            const formData =
                new FormData();

            files.forEach(file => {

                formData.append(
                    "files",
                    file
                );

            });

            fileStatus.textContent =
                `Processing ${
                    files.length
                } file${
                    files.length === 1
                        ? ""
                        : "s"
                }...`;

            try {

                const response =
                    await fetch(
                        `${API}/upload`,
                        {
                            method: "POST",
                            body: formData
                        }
                    );

                const data =
                    await response.json();

                if (!response.ok) {

                    throw new Error(
                        data.detail ||
                        "Upload failed."
                    );
                }

                clearWelcomeMessage();

                // Add returned files
                if (
                    Array.isArray(
                        data.files
                    )
                ) {

                    data.files.forEach(
                        file => {

                            // Avoid duplicates
                            const exists =
                                uploadedFiles.some(
                                    existing =>
                                        existing.file_id ===
                                        file.file_id
                                );

                            if (!exists) {

                                uploadedFiles.push(
                                    file
                                );
                            }

                            // Latest uploaded file
                            selectedFileId =
                                file.file_id;
                        }
                    );

                    data.files.forEach(
                        file => {

                            addMessage(
                                "assistant",
                                `Loaded ${file.filename}. It is ready for questions.`
                            );

                        }
                    );
                }

                renderFiles();
                updateStatus();

            } catch (error) {

                console.error(
                    "Upload error:",
                    error
                );

                addMessage(
                    "assistant",
                    `Upload error: ${error.message}`
                );

                updateStatus();

            } finally {

                // Allow the same file
                // to be uploaded again.
                fileInput.value = "";
            }
        }
    );
}


// ============================================================
// FILE LIST CLICK HANDLER
// ============================================================

if (fileList) {

    fileList.addEventListener(
        "click",
        async event => {

            // ==================================================
            // REMOVE FILE
            // ==================================================

            const deleteButton =
                event.target.closest(
                    "[data-delete]"
                );

            if (deleteButton) {

                event.stopPropagation();

                const fileId =
                    deleteButton.dataset.delete;

                await removeFile(
                    fileId
                );

                return;
            }


            // ==================================================
            // SINGLE MODE FILE SELECTION
            // ==================================================

            const card =
                event.target.closest(
                    "[data-select]"
                );

            if (
                card &&
                getScope() === "single"
            ) {

                const newFileId =
    card.dataset.select;

                if (newFileId !== selectedFileId) {
                    selectedFileId = newFileId;

                    resetChatHistory();

                    messages.innerHTML = "";
                }
                else {
                    selectedFileId = newFileId;
                }

                renderFiles();
                updateStatus();
            }

        }
    );
}


// ============================================================
// REMOVE INDIVIDUAL FILE
// ============================================================

async function removeFile(fileId) {

    try {

        const response =
            await fetch(
                `${API}/files/${fileId}`,
                {
                    method: "DELETE"
                }
            );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Could not remove file."
            );
        }

        uploadedFiles =
            uploadedFiles.filter(
                file =>
                    file.file_id !==
                    fileId
            );


        // If currently selected file
        // was deleted, select another.
        if (
            selectedFileId ===
            fileId
        ) {

            selectedFileId =
                uploadedFiles.length > 0
                    ? uploadedFiles[
                        uploadedFiles.length - 1
                    ].file_id
                    : null;
        }

        renderFiles();
        updateStatus();

        addMessage(
            "assistant",
            "The file was removed."
        );

    } catch (error) {

        console.error(
            "File removal error:",
            error
        );

        addMessage(
            "assistant",
            `File removal error: ${error.message}`
        );
    }
}


// ============================================================
// CHAT SCOPE CHANGE
// ============================================================

document
    .querySelectorAll(
        'input[name="scope"]'
    )
    .forEach(input => {

        input.addEventListener(
            "change",
            () => {

                resetChatHistory();

                messages.innerHTML = "";

                renderFiles();

                updateStatus();
            }
        );

    });


// ============================================================
// DATASET RESULT TABLE
// ============================================================

function createResultTable(rows) {

    if (
        !Array.isArray(rows) ||
        rows.length === 0
    ) {

        return "";
    }

    const columns =
        Object.keys(rows[0]);

    let html = `
        <table class="result-table">
            <thead>
                <tr>
    `;

    columns.forEach(
        column => {

            html += `
                <th>
                    ${escapeHtml(
                        column
                    )}
                </th>
            `;

        }
    );

    html += `
                </tr>
            </thead>

            <tbody>
    `;

    rows.forEach(row => {

        html += "<tr>";

        columns.forEach(
            column => {

                html += `
                    <td>
                        ${escapeHtml(
                            row[column]
                        )}
                    </td>
                `;

            }
        );

        html += "</tr>";

    });

    html += `
            </tbody>
        </table>
    `;

    return html;
}


// ============================================================
// CHAT
// ============================================================

async function sendQuestion() {

    const question =
        questionInput.value.trim();

    const scope =
        getScope();

    const fileIds =
        getSelectedFileIds();


    // No question
    if (!question) {
        return;
    }


    // No files
    if (fileIds.length === 0) {

        addMessage(
            "assistant",
            scope === "single"
                ? "Please select a file first."
                : "Please select at least one file."
        );

        return;
    }


    clearWelcomeMessage();

    addMessage(
        "user",
        question
    );
    addToChatHistory(
        "user",
        question
    );

    questionInput.value = "";

    sendBtn.disabled = true;
    sendBtn.textContent = "...";


    try {

        const response =
            await fetch(
                `${API}/chat`,
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({
                        scope: scope,
                        file_ids: fileIds,
                        question: question,
                        chat_history: chatHistory
                    })
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Question failed."
            );
        }


        // ==================================================
        // ADDITIONAL RESPONSE INFORMATION
        // ==================================================

        let extraHtml = "";


        // -----------------------------
        // Text RAG sources
        // -----------------------------

        if (
            Array.isArray(
                data.sources
            ) &&
            data.sources.length > 0
        ) {

            extraHtml += `
                <div class="meta">
                    <strong>
                        Sources:
                    </strong>
                    <br>
            `;

            data.sources.forEach(
                source => {

                    extraHtml += `
                        <span
                            class="source-chip"
                        >
                            ${escapeHtml(
                                source.filename
                            )}
                            ${
                                source.chunk !==
                                undefined
                                ? ` • chunk ${
                                    escapeHtml(
                                        source.chunk
                                    )
                                }`
                                : ""
                            }
                        </span>
                    `;

                }
            );

            extraHtml += "</div>";
        }


        // -----------------------------
        // Generated SQL
        // -----------------------------

        if (data.sql) {

            extraHtml += `
                <div class="meta">
                    <strong>
                        Generated SQL
                    </strong>
                </div>

                <pre class="sql">
${escapeHtml(data.sql)}
                </pre>
            `;
        }


        // -----------------------------
        // Dataset result
        // -----------------------------

        if (
            Array.isArray(
                data.result
            ) &&
            data.result.length > 0
        ) {

            extraHtml +=
                createResultTable(
                    data.result
                );
        }


        // -----------------------------
        // Multi-file dataset results
        // -----------------------------

        if (
            Array.isArray(
                data.dataset_results
            )
        ) {

            data.dataset_results
                .forEach(
                    item => {

                        extraHtml += `
                            <div class="meta">
                                <strong>
                                    ${
                                        escapeHtml(
                                            item.filename
                                        )
                                    }
                                </strong>
                            </div>
                        `;

                        if (
                            item.sql
                        ) {

                            extraHtml += `
                                <pre class="sql">
${escapeHtml(
    item.sql
)}
                                </pre>
                            `;
                        }

                        if (
                            Array.isArray(
                                item.result
                            ) &&
                            item.result.length
                        ) {

                            extraHtml +=
                                createResultTable(
                                    item.result
                                );

                        }

                    }
                );
        }


        // ==================================================
        // DISPLAY FINAL RESPONSE
        // ==================================================

        addMessage(
            "assistant",
            data.answer ||
                "No answer was returned.",
            extraHtml
        );
        addToChatHistory(
            "assistant",
            data.answer ||
                "No answer was returned."
        );


    } catch (error) {

        console.error(
            "Chat error:",
            error
        );

        addMessage(
            "assistant",
            `Error: ${error.message}`
        );

    } finally {

        sendBtn.disabled = false;
        sendBtn.textContent = "Send";

    }
}


// ============================================================
// SEND BUTTON
// ============================================================

if (sendBtn) {

    sendBtn.addEventListener(
        "click",
        sendQuestion
    );
}


// ============================================================
// ENTER TO SEND
// ============================================================

if (questionInput) {

    questionInput.addEventListener(
        "keydown",
        event => {

            // Enter = Send
            // Shift+Enter = newline

            if (
                event.key === "Enter" &&
                !event.shiftKey
            ) {

                event.preventDefault();

                sendQuestion();
            }

        }
    );
}


// ============================================================
// REPORT GENERATION
// ============================================================

if (generateReportBtn) {

    generateReportBtn.addEventListener(
        "click",
        async () => {

            const scope =
                getScope();

            const fileIds =
                getSelectedFileIds();


            if (
                fileIds.length === 0
            ) {

                addMessage(
                    "assistant",
                    scope === "single"
                        ? "Please select a file before generating a report."
                        : "Please select at least one file before generating a report."
                );

                return;
            }


            generateReportBtn.disabled =
                true;

            generateReportBtn.textContent =
                "Generating...";


            try {

                const response =
                    await fetch(
                        `${API}/report`,
                        {
                            method: "POST",

                            headers: {
                                "Content-Type":
                                    "application/json"
                            },

                            body:
                                JSON.stringify({

                                    scope:
                                        scope,

                                    file_ids:
                                        fileIds,

                                    specifications:
                                        reportSpecs.value

                                })
                        }
                    );


                const data =
                    await response.json();


                if (!response.ok) {

                    throw new Error(
                        data.detail ||
                        "Report generation failed."
                    );
                }


                // Show report
                reportText.textContent =
                    data.content;


                // Download links
                pdfLink.href =
                    `${API}/report/${
                        data.report_id
                    }/pdf`;


                docxLink.href =
                    `${API}/report/${
                        data.report_id
                    }/docx`;


                reportOutput
                    .classList
                    .remove("hidden");


            } catch (error) {

                console.error(
                    "Report error:",
                    error
                );

                addMessage(
                    "assistant",
                    `Report error: ${error.message}`
                );

            } finally {

                generateReportBtn.disabled =
                    false;

                generateReportBtn.textContent =
                    "Generate Report";

            }

        }
    );
}


// ============================================================
// CLEAR CACHE
// ============================================================

if (clearCacheBtn) {

    clearCacheBtn.addEventListener(
        "click",
        async () => {

            const confirmed =
                window.confirm(
                    "Clear all uploaded files, indexed documents, datasets, reports and ChromaDB data?"
                );


            if (!confirmed) {
                return;
            }


            try {

                clearCacheBtn.disabled =
                    true;

                clearCacheBtn.textContent =
                    "Clearing...";


                const response =
                    await fetch(
                        `${API}/cache`,
                        {
                            method: "DELETE"
                        }
                    );


                const data =
                    await response.json();


                if (!response.ok) {

                    throw new Error(
                        data.detail ||
                        "Cache clear failed."
                    );
                }


                // Reset client-side state
                uploadedFiles = [];

                selectedFileId = null;


                // Reset UI
                renderFiles();

                updateStatus();


                if (reportOutput) {

                    reportOutput
                        .classList
                        .add("hidden");
                }


                // Remove old messages
                messages.innerHTML = "";


                addMessage(
                    "assistant",
                    "All uploaded files, indexed data and ChromaDB data have been cleared."
                );


            } catch (error) {

                console.error(
                    "Cache error:",
                    error
                );

                addMessage(
                    "assistant",
                    `Cache error: ${error.message}`
                );

            } finally {

                clearCacheBtn.disabled =
                    false;

                clearCacheBtn.textContent =
                    "Clear Cache";

            }

        }
    );
}


// ============================================================
// INITIALIZE
// ============================================================

loadFiles();
updateStatus();