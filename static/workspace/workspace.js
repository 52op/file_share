class WorkspaceManager {
    constructor() {
        this.workspace = null;
        this.tabs = new Map();
        this.activeTab = null;
        this.isMinimized = false;
        this.savedState = null;
        this.hasUnsavedChanges = new Map(); // 跟踪未保存的更改
        this.init();
    }

    init() {
        const workspace = document.createElement('div');
        workspace.className = 'workspace-container';
        workspace.innerHTML = `
            <div class="workspace-header">
                <div class="tab-list"></div>
                <div class="workspace-controls">
                    <button class="minimize-btn"><i class="bi bi-dash-lg"></i></button>
                    <button class="maximize-btn"><i class="bi bi-arrows-fullscreen"></i></button>
                    <button class="close-btn"><i class="bi bi-x-lg"></i></button>
                </div>
            </div>
            <div class="workspace-body">
                <button class="save-button" style="display: none;">
                    <i class="bi bi-save"></i> 保存
                </button>
            </div>
            <div class="workspace-statusbar">
                <div class="statusbar-main">
                    <span class="status-text"></span>
                    <span class="file-info"></span>
                </div>
                <div class="shortcuts-info">
                    <span class="shortcut-item"><span class="key">Ctrl</span>+<span class="key">S</span> 保存</span>
                    <span class="shortcut-item"><span class="key">Ctrl</span>+<span class="key">F</span> 查找</span>
                    <span class="shortcut-item"><span class="key">Ctrl</span>+<span class="key">H</span> 替换</span>
                    <span class="shortcut-item"><span class="key">Ctrl</span>+<span class="key">Z</span> 撤销</span>
                    <span class="shortcut-item"><span class="key">Ctrl</span>+<span class="key">Y</span> 重做</span>
                </div>
            </div>
        `;

        document.body.appendChild(workspace);
        this.workspace = workspace;

        this.bindControls();
        this.initDraggable();
        this.initResizable();
        this.registerShortcuts();
        this.watchWindowResize();
    }

    initDraggable() {
        const header = this.workspace.querySelector('.workspace-header');
        let isDragging = false;
        let pointerId = null;
        let startX, startY, startLeft, startTop;

        const onPointerMove = (e) => {
            if (!isDragging || e.pointerId !== pointerId) return;
            e.preventDefault();
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            this.workspace.style.left = `${startLeft + dx}px`;
            this.workspace.style.top = `${startTop + dy}px`;
        };

        const onPointerUp = (e) => {
            if (e.pointerId !== pointerId) return;
            isDragging = false;
            pointerId = null;
            if (header.releasePointerCapture) header.releasePointerCapture(e.pointerId);
            header.removeEventListener('pointermove', onPointerMove);
            header.removeEventListener('pointerup', onPointerUp);
            header.removeEventListener('pointercancel', onPointerUp);
            this.clampToViewport();
        };

        header.addEventListener('pointerdown', (e) => {
            if (e.target.closest('.workspace-controls') || e.target.closest('.tab-close')) {
                return;
            }
            // 移动端全屏显示，禁用手势拖动避免劫持页面滚动
            if (window.innerWidth <= 768) return;

            isDragging = true;
            pointerId = e.pointerId;
            startX = e.clientX;
            startY = e.clientY;
            startLeft = this.workspace.offsetLeft;
            startTop = this.workspace.offsetTop;
            if (header.setPointerCapture) header.setPointerCapture(e.pointerId);
            header.addEventListener('pointermove', onPointerMove, { passive: false });
            header.addEventListener('pointerup', onPointerUp);
            header.addEventListener('pointercancel', onPointerUp);
        });
    }

    initResizable() {
        const directions = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];
        directions.forEach(dir => {
            const handle = document.createElement('div');
            handle.className = `resize-handle resize-${dir}`;
            this.workspace.appendChild(handle);

            handle.onpointerdown = (e) => {
                e.preventDefault();
                e.stopPropagation();
                // 移动端全屏显示，不提供拖拽缩放
                if (window.innerWidth <= 768) return;

                const startX = e.clientX;
                const startY = e.clientY;
                const startWidth = this.workspace.offsetWidth;
                const startHeight = this.workspace.offsetHeight;
                const startLeft = this.workspace.offsetLeft;
                const startTop = this.workspace.offsetTop;
                const pointerId = e.pointerId;
                if (handle.setPointerCapture) handle.setPointerCapture(pointerId);

                const onPointerMove = (e) => {
                    const dx = e.clientX - startX;
                    const dy = e.clientY - startY;

                    if (dir.includes('e')) this.workspace.style.width = `${Math.max(300, startWidth + dx)}px`;
                    if (dir.includes('w')) {
                        const newWidth = Math.max(300, startWidth - dx);
                        this.workspace.style.width = `${newWidth}px`;
                        this.workspace.style.left = `${startLeft + (startWidth - newWidth)}px`;
                    }
                    if (dir.includes('s')) this.workspace.style.height = `${Math.max(200, startHeight + dy)}px`;
                    if (dir.includes('n')) {
                        const newHeight = Math.max(200, startHeight - dy);
                        this.workspace.style.height = `${newHeight}px`;
                        this.workspace.style.top = `${startTop + (startHeight - newHeight)}px`;
                    }

                    if (this.activeTab) {
                        const tab = this.tabs.get(this.activeTab);
                        if (tab && tab.editor) tab.editor.resize();
                    }
                };

                const onPointerUp = (e) => {
                    if (handle.releasePointerCapture) handle.releasePointerCapture(e.pointerId);
                    handle.removeEventListener('pointermove', onPointerMove);
                    handle.removeEventListener('pointerup', onPointerUp);
                    handle.removeEventListener('pointercancel', onPointerUp);
                    this.clampToViewport();
                };

                handle.addEventListener('pointermove', onPointerMove, { passive: false });
                handle.addEventListener('pointerup', onPointerUp);
                handle.addEventListener('pointercancel', onPointerUp);
            };
        });
    }

    // 将工作区尺寸/位置约束在视口内（桌面端；移动端由 CSS 全屏接管）
    clampToViewport() {
        if (!this.workspace ||
            this.workspace.classList.contains('maximized') ||
            this.workspace.classList.contains('minimized') ||
            this.workspace.style.display === 'none' ||
            window.innerWidth <= 768) {
            return;
        }

        const vw = window.innerWidth;
        const vh = window.innerHeight;

        // 尺寸未就绪时跳过，避免把内联尺寸误写成 0
        if (this.workspace.offsetWidth === 0 || this.workspace.offsetHeight === 0) {
            return;
        }

        let width = this.workspace.offsetWidth;
        let height = this.workspace.offsetHeight;
        width = Math.min(width, Math.max(300, vw - 20));
        height = Math.min(height, Math.max(200, vh - 20));
        this.workspace.style.width = `${width}px`;
        this.workspace.style.height = `${height}px`;

        let left = this.workspace.offsetLeft;
        let top = this.workspace.offsetTop;
        left = Math.min(Math.max(left, 5), Math.max(5, vw - width - 5));
        top = Math.min(Math.max(top, 5), Math.max(5, vh - height - 5));
        this.workspace.style.left = `${left}px`;
        this.workspace.style.top = `${top}px`;

        if (this.activeTab) {
            const tab = this.tabs.get(this.activeTab);
            if (tab && tab.editor) tab.editor.resize();
        }
    }

    // 窗口尺寸变化时自适应（桌面端约束视口，移动端由 CSS 全屏接管）
    watchWindowResize() {
        window.addEventListener('resize', () => {
            if (window.innerWidth <= 768) return;
            this.clampToViewport();
        });
    }

    bindControls() {
        const controls = this.workspace.querySelector('.workspace-controls');

        controls.querySelector('.minimize-btn').onclick = () => this.minimize();
        controls.querySelector('.maximize-btn').onclick = () => this.toggleMaximize();
        controls.querySelector('.close-btn').onclick = () => this.close();

        this.workspace.querySelector('.tab-list').onclick = (e) => {
            const tab = e.target.closest('.tab');
            if (tab) {
                if (e.target.closest('.tab-close')) {
                    this.closeTab(tab.dataset.id);
                } else {
                    this.activateTab(tab.dataset.id);
                }
            }
        };
    }

    registerShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (this.activeTab && e.ctrlKey && e.key === 's') {
                e.preventDefault();
                const tab = this.tabs.get(this.activeTab);
                if (tab.canEdit) {
                    this.saveFile(tab);
                }
            }
        });
    }

    minimize() {
        if (!this.isMinimized) {
            this.savedState = {
                width: this.workspace.style.width || '800px',
                height: this.workspace.style.height || '600px',
                left: this.workspace.style.left,
                top: this.workspace.style.top,
                isMaximized: this.workspace.classList.contains('maximized')
            };

            if (this.savedState.isMaximized) {
                this.workspace.classList.remove('maximized');
            }

            this.workspace.classList.add('minimized');
            this.isMinimized = true;

            if (this.activeTab) {
                const tab = this.tabs.get(this.activeTab);
                if (tab && tab.editor) {
                    tab.editor.container.style.display = 'none';
                }
            }
        }
    }

    restore() {
        if (this.isMinimized && this.savedState) {
            this.workspace.classList.remove('minimized');
            Object.assign(this.workspace.style, {
                width: this.savedState.width,
                height: this.savedState.height,
                left: this.savedState.left,
                top: this.savedState.top
            });

            if (this.savedState.isMaximized) {
                this.workspace.classList.add('maximized');
            }

            this.isMinimized = false;

            if (this.activeTab) {
                const tab = this.tabs.get(this.activeTab);
                if (tab && tab.editor) {
                    tab.editor.container.style.display = 'block';
                    tab.editor.resize();
                }
            }

            this.clampToViewport();
        }
    }


    toggleMaximize() {
        if (this.isMinimized) {
            this.restore();
        }

        const wasMaximized = this.workspace.classList.contains('maximized');
        this.workspace.classList.toggle('maximized');

        if (this.activeTab) {
            const tab = this.tabs.get(this.activeTab);
            if (tab && tab.editor) {
                tab.editor.container.style.display = 'block';
                tab.editor.resize();
            }
        }

        if (!this.workspace.classList.contains('maximized')) {
            this.clampToViewport();
        }
    }

    close() {
        // 检查是否有任何文件未保存
        if (this.hasUnsavedChanges.size > 0) {
            const unsavedFiles = Array.from(this.hasUnsavedChanges.keys())
                .map(id => this.tabs.get(id)?.filename)
                .filter(Boolean)
                .join(', ');

            if (!confirm(`以下文件尚未保存: \n${unsavedFiles}\n确定要关闭工作区吗？`)) {
                return;
            }
        }
        // 先隐藏窗口
        this.workspace.style.display = 'none';

        // 清理所有标签
        Array.from(this.tabs.keys()).forEach(id => {
            const tab = this.tabs.get(id);
            if (tab && tab.editor) {
                tab.editor.destroy();
                tab.editor.container.remove();
            }
            this.workspace.querySelector(`.tab[data-id="${id}"]`)?.remove();
        });

        // 清空标签集合
        this.tabs.clear();
        this.activeTab = null;

        // 清空未保存更改记录
        this.hasUnsavedChanges.clear();

        // 重置工作区状态
        this.isMinimized = false;
        this.savedState = null;
        this.workspace.classList.remove('minimized', 'maximized');
    }

    openFile(filename, filepath, content, canEdit = false) {
        if (this.isMinimized) {
            this.restore();
        }

        // 清理历史残留的异常 0 尺寸内联样式（避免打开后宽度被钳制）
        if (this.workspace.style.width === '0px' || this.workspace.style.height === '0px') {
            this.workspace.style.width = '';
            this.workspace.style.height = '';
        }

        const id = filepath;
        if (this.tabs.has(id)) {
            this.activateTab(id);
            return;
        }

        const tab = {
            id,
            filename,
            filepath,
            canEdit,
            editor: null
        };

        const tabEl = document.createElement('div');
        tabEl.className = 'tab';
        tabEl.dataset.id = id;
        tabEl.innerHTML = `
            <span class="tab-title">${filename}</span>
            <span class="tab-close"><i class="bi bi-x"></i></span>
        `;

        this.workspace.querySelector('.tab-list').appendChild(tabEl);

        const editorContainer = document.createElement('div');
        editorContainer.className = 'editor-container';
        editorContainer.id = `editor-${id}`;
        this.workspace.querySelector('.workspace-body').appendChild(editorContainer);

        const editor = ace.edit(editorContainer.id);
        editor.setTheme("ace/theme/monokai");
        editor.session.setMode(this.getAceMode(filename));
        editor.setValue(content, -1);
        editor.setReadOnly(!canEdit);
        editor.setOptions({
            fontSize: "14px",
            showPrintMargin: false,
            showGutter: true,
            highlightActiveLine: true,
            enableBasicAutocompletion: true,
            enableSnippets: true,
            enableLiveAutocompletion: true
        });

        // 添加更改监听
        editor.session.on('change', () => {
            if (!this.hasUnsavedChanges.get(id)) {
                this.hasUnsavedChanges.set(id, true);
                this.updateSaveButton(id);
                this.updateTabTitle(id);
            }
        });

        // 添加保存按钮
        const saveButton = this.workspace.querySelector('.save-button');
        saveButton.onclick = () => this.saveFile(tab);

        tab.editor = editor;
        this.tabs.set(id, tab);

        this.activateTab(id);
        this.workspace.style.display = 'flex';
        this.clampToViewport();
    }

    updateSaveButton(id) {
        const tab = this.tabs.get(id);
        const saveButton = this.workspace.querySelector('.save-button');

        if (tab && tab.canEdit && this.hasUnsavedChanges.get(id)) {
            saveButton.style.display = 'block';
        } else {
            saveButton.style.display = 'none';
        }
    }

    updateTabTitle(id) {
        const tabElement = this.workspace.querySelector(`.tab[data-id="${id}"]`);
        const titleElement = tabElement?.querySelector('.tab-title');
        if (titleElement) {
            const tab = this.tabs.get(id);
            titleElement.textContent = `${tab.filename}${this.hasUnsavedChanges.get(id) ? ' *' : ''}`;
        }
    }

    async saveFile(tab) {
        try {
            const content = tab.editor.getValue();
            const response = await fetch(`/api/save-file/${tab.filepath}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content})
            });

            if (response.ok) {
                //this.hasUnsavedChanges.set(tab.id, false);
                this.hasUnsavedChanges.delete(tab.id)
                this.updateSaveButton(tab.id);
                this.updateTabTitle(tab.id);
                this.setStatus('文件已保存', 'success');
            } else {
                throw new Error('保存失败');
            }
        } catch (err) {
            this.setStatus('保存失败: ' + err.message, 'error');
        }
    }

    activateTab(id) {
        if (this.activeTab === id) return;

        const tab = this.tabs.get(id);
        if (!tab || !tab.editor) return;  // 添加安全检查

        if (this.activeTab) {
            const oldTab = this.tabs.get(this.activeTab);
            if (oldTab && oldTab.editor) {
                oldTab.editor.container.style.display = 'none';
            }
            const oldTabElement = this.workspace.querySelector(`.tab[data-id="${this.activeTab}"]`);
            if (oldTabElement) {
                oldTabElement.classList.remove('active');
            }
        }

        tab.editor.container.style.display = 'block';
        const tabElement = this.workspace.querySelector(`.tab[data-id="${id}"]`);
        if (tabElement) {
            tabElement.classList.add('active');
        }
        tab.editor.focus();
        tab.editor.resize();

        this.updateSaveButton(id);
        this.activeTab = id;
        this.updateStatusBar(tab);
    }

    closeTab(id) {
        const tab = this.tabs.get(id);
        if (!tab) return;

        // 添加未保存更改检查
        if (this.hasUnsavedChanges.get(id)) {
            if (!confirm('文件有未保存的更改，确定要关闭吗？')) {
                return;
            }
        }

        // 安全地移除 DOM 元素
        const tabElement = this.workspace.querySelector(`.tab[data-id="${id}"]`);
        if (tabElement) {
            tabElement.remove();
        }

        // 安全地销毁编辑器
        if (tab.editor) {
            tab.editor.destroy();
            tab.editor.container.remove();
        }

        this.tabs.delete(id);

        if (this.activeTab === id) {
            const nextTab = Array.from(this.tabs.keys())[0];
            if (nextTab) {
                this.activateTab(nextTab);
            } else {
                this.activeTab = null;
                this.minimize();
            }
        }

        this.hasUnsavedChanges.delete(id);
    }

    async saveFile_(tab) {
        try {
            const content = tab.editor.getValue();
            const response = await fetch(`/api/save-file/${tab.filepath}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content})
            });

            if (response.ok) {
                this.setStatus('文件已保存', 'success');
            } else {
                throw new Error('保存失败');
            }
        } catch (err) {
            this.setStatus('保存失败: ' + err.message, 'error');
        }
    }

    setStatus(message, type = 'info') {
        const statusBar = this.workspace.querySelector('.status-text');
        statusBar.textContent = message;
        statusBar.className = `status-text status-${type}`;

        if (type !== 'error') {
            setTimeout(() => {
                statusBar.textContent = '';
                statusBar.className = 'status-text';
            }, 3000);
        }
    }

    updateStatusBar(tab) {
        const fileInfo = this.workspace.querySelector('.file-info');
        fileInfo.textContent = `${tab.filename} ${tab.canEdit ? '(可编辑)' : '(只读)'}`;
    }

    getAceMode(filename) {
        const ext = filename.split('.').pop().toLowerCase();
        const modes = {
            'md': 'markdown',
            'txt': 'text',
            'js': 'javascript',
            'py': 'python',
            'html': 'html',
            'css': 'css',
            'json': 'json',
            'xml': 'xml',
            'sql': 'sql',
            'java': 'java',
            'cpp': 'c_cpp',
            'c': 'c_cpp',
            'h': 'c_cpp',
            'go': 'golang',
            'php': 'php',
            'rb': 'ruby',
            'rs': 'rust',
            'sh': 'shell',
            'yaml': 'yaml',
            'yml': 'yaml',
            'ini': 'ini',
            'ts': 'typescript',
            'tsx': 'typescript'
        };
        return `ace/mode/${modes[ext] || 'text'}`;
    }
}

const workspace = new WorkspaceManager();

async function openFileInWorkspace(filename, filepath, canEdit = false) {
    try {
        const response = await fetch(`/preview/${filepath}`, {
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        const contentType = response.headers.get('content-type');

        // 如果不是JSON响应，直接获取文本错误
        if (!contentType?.includes('application/json')) {
            const errorText = await response.text();
            throw new Error(errorText);
        }

        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        workspace.openFile(filename, filepath, data.content, canEdit);

    } catch(err) {
        alert('打开文件失败: ' + err.message);
    }
}

