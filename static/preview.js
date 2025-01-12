let previewEditor = null;
let mdEditor = null;
let currentEditingFile = null;
let selectedItems = new Set();
// Add this at the top of your file with other global variables
const supportedTypes = {
    'md': 'markdown',
    'txt': 'text',
    'bat': 'batch',
    'cmd': 'batch',
    'vbs': 'vbscript',
    'json': 'json',
    'py': 'python',
    'css': 'css',
    'js': 'javascript',
    'html': 'html',
    'php': 'php',
    'xml': 'xml',
    'sql': 'sql',
    'java': 'java',
    'cpp': 'c_cpp',
    'c': 'c_cpp',
    'h': 'c_cpp',
    'cs': 'csharp',
    'go': 'golang',
    'rb': 'ruby',
    'rs': 'rust',
    'sh': 'sh',
    'yaml': 'yaml',
    'yml': 'yaml',
    'ini': 'ini',
    'conf': 'ini',
    'tsx': 'typescript',
    'ts': 'typescript'
};

//动态资源加载器
class ResourceLoader {
    static async loadCSS(url) {
        if (document.querySelector(`link[href="${url}"]`)) return;

        return new Promise((resolve, reject) => {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = url;
            link.onload = resolve;
            link.onerror = reject;
            document.head.appendChild(link);
        });
    }

    static async loadJS(url) {
        if (document.querySelector(`script[src="${url}"]`)) return;

        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = url;
            script.onload = resolve;
            script.onerror = reject;
            document.body.appendChild(script);
        });
    }
}

// 预览处理器注册表
const previewHandlers = {
    // 图片处理器
    image: {
        match: ext => ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext),
        handle: async (filepath, filename) => {
            await Promise.all([
                ResourceLoader.loadCSS('/static/imageViewer/imageViewer.css'),
                ResourceLoader.loadJS('/static/imageViewer/imageViewer.js')
            ]);

            if (!window.imageViewer) {
                window.imageViewer = new ImageViewer();
            }

            // 获取当前目录下所有图片
            const currentDirItems = Array.from(document.querySelectorAll('.list-group-item'));
            const images = currentDirItems
                .map(item => {
                    const link = item.querySelector('.item-link');
                    if (!link) return null;

                    const name = link.textContent.trim();
                    const ext = name.split('.').pop().toLowerCase();
                    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext)) {
                        const onclick = link.getAttribute('onclick');
                        if (onclick) {
                            const match = onclick.match(/handleDownload\('([^']+)',\s*'([^']+)'\)/);
                            if (match) {
                                return {
                                    name: name,
                                    path: `${match[1]}/${match[2]}`
                                };
                            }
                        }
                    }
                    return null;
                })
                .filter(img => img !== null);

            window.imageViewer.setImages(images);
            window.imageViewer.show(filepath, filename);
            return true;
        }
    },

    // PDF处理器
    pdf: {
        match: ext => ext === 'pdf',
        handle: async (filepath, filename) => {
            await Promise.all([
                ResourceLoader.loadCSS('/static/pdfViewer/pdfViewer.css'),
                ResourceLoader.loadJS('/static/pdfViewer/pdfViewer.js')
            ]);

            if (!window.pdfViewer) {
                window.pdfViewer = new PDFViewer();
            }

            window.pdfViewer.show(`/preview/${filepath}`, filename);
            return true;
        }
    },

    // 音视频处理器
    media: {
        match: ext => ['mp4', 'webm', 'ogg', 'mp3', 'wav'].includes(ext),
        handle: async (filepath, filename = '') => {
            await Promise.all([
                ResourceLoader.loadCSS('/static/mediaViewer/mediaViewer.css'),
                ResourceLoader.loadJS('/static/mediaViewer/mediaViewer.js')
            ]);

            if (!window.mediaViewer) {
                window.mediaViewer = new MediaViewer();
            }

            const ext = filepath.split('.').pop().toLowerCase();
            const isAudio = ['mp3', 'wav'].includes(ext);

            window.mediaViewer.show(`/preview/${filepath}`, filename, isAudio ? 'audio' : 'video');
            return true;
        }
    }

    // 可以轻松添加更多处理器...
};

// 文件预览处理
async function fileCheck(filename, filepath) {
    const checkResponse = await fetch(`/preview/${filepath}`);
    const contentType = checkResponse.headers.get('content-type');

    // 检查认证
    if (contentType?.includes('text/html')) {
        const content = await checkResponse.text();
        if (content.includes('passwordForm') && content.includes('password') && content.includes('checkPassword')) {
            window.location.reload();
            return true;
        }
    }

    const ext = filename.split('.').pop().toLowerCase();

    // 查找匹配的处理器
    const handler = Object.values(previewHandlers).find(h => h.match(ext));
    if (handler) {
        // 移除这行，因为handler.handle现在会直接处理预览内容
        //const previewContent = document.getElementById('preview-content');
        //previewContent.innerHTML = handler.handle(filepath);

        // 直接调用处理器的handle方法
        await handler.handle(filepath, filename);
        return true;
    }

    return false;
}

async function previewFile(filename, filepath) {
    const shouldStop = await fileCheck(filename, filepath);
    if (!shouldStop) {
        openFileInWorkspace(filename, filepath, false);
    }
}

async function editFile(filename, filepath) {
    const shouldStop = await fileCheck(filename, filepath);
    if (!shouldStop) {
        openFileInWorkspace(filename, filepath, true);
    }
}


document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.item-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', updateBatchActionsVisibility);
    });
});

function updateBatchActionsVisibility() {
    const checkedBoxes = document.querySelectorAll('.item-checkbox:checked');
    const batchActions = document.getElementById('batch-actions');
    batchActions.style.display = checkedBoxes.length > 0 ? 'block' : 'none';
}

function cancelSelection() {
    document.querySelectorAll('.item-checkbox:checked').forEach(checkbox => {
        checkbox.checked = false;
    });
    updateBatchActionsVisibility();
}


/* -------------------------------------- 分享链接相关逻辑 ------------------------------------ */
function generateRandomPassword(inputId) {
    const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    let password = '';
    for (let i = 0; i < 4; i++) {
        password += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    document.getElementById(inputId).value = password;
}

function showShareDialog(path, name) {
    document.getElementById('sharePath').value = path;
    document.getElementById('shareResult').classList.add('d-none');
    document.getElementById('shareForm').reset();
    generateRandomPassword('manage_code');
    new bootstrap.Modal(document.getElementById('shareDialog')).show();
}

async function createShare() {
    const path = document.getElementById('sharePath').value;
    const manageCode = document.getElementById('manage_code').value.trim();
    const password = document.getElementById('sharePassword').value.trim();
    const expireDays = document.getElementById('shareExpire').value;
    const desc = document.getElementById('desc').value;

    if (!manageCode) {
        alert('管理密码密码不能为空');
        return;
    }

    try {
        const data = JSON.stringify({
            path: path,
            manage_code: manageCode,
            password: password,
            desc: desc,
            expire_days: expireDays
        });

        const response = await fetch('/api/create-share', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: data
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('text/html')) {
            const content = await response.text();
            if (content.includes('passwordForm') && content.includes('password') && content.includes('checkPassword')) {
                window.location.reload();
                return;
            }
        }

        const result = await response.json();
        let shareUrl = window.location.origin + result.share_url;

        if (password) {
            shareUrl += ` 访问密码:${password}`;
        }

        document.getElementById('shareUrl').value = shareUrl;
        document.getElementById('shareResult').classList.remove('d-none');
    } catch (error) {
        alert('创建分享链接失败');
    }
}

function copyShareUrl() {
    const urlInput = document.getElementById('shareUrl');
    urlInput.select();
    document.execCommand('copy');
    alert('链接已复制到剪贴板');
}
