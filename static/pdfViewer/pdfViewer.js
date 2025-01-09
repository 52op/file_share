class PDFViewer {
    constructor() {
        this.initModal();
        this.bindEvents();
    }

    initModal() {
        const modal = document.createElement('div');
        modal.className = 'pdf-viewer-modal';
        modal.innerHTML = `
            <div class="viewer-header">
                <span class="viewer-title"></span>
                <div class="viewer-controls">
                    <button class="btn-fullscreen" title="全屏"><i class="bi bi-arrows-fullscreen"></i></button>
                    <button class="btn-download" title="下载"><i class="bi bi-download"></i></button>
                    <button class="btn-print" title="打印"><i class="bi bi-printer"></i></button>
                    <button class="btn-close" title="关闭"><i class="bi bi-x-lg"></i></button>
                </div>
            </div>
            <div class="viewer-container">
                <iframe class="pdf-frame" allowfullscreen></iframe>
            </div>
        `;

        document.body.appendChild(modal);
        this.modal = modal;
        this.iframe = modal.querySelector('.pdf-frame');
    }

    bindEvents() {
        // 全屏切换
        this.modal.querySelector('.btn-fullscreen').onclick = () => this.toggleFullscreen();

        // 下载PDF
        this.modal.querySelector('.btn-download').onclick = () => this.downloadPDF();

        // 打印PDF
        this.modal.querySelector('.btn-print').onclick = () => this.printPDF();

        // 关闭按钮
        this.modal.querySelector('.btn-close').onclick = () => this.close();

        // ESC键关闭
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modal.classList.contains('active')) {
                this.close();
            }
        });
    }

    show(pdfPath, title = '') {
        this.modal.classList.add('active');
        this.modal.querySelector('.viewer-title').textContent = title;
        this.iframe.src = pdfPath;
        this.currentPdfPath = pdfPath;
    }

    close() {
        this.modal.classList.remove('active');
        this.iframe.src = '';
    }

    toggleFullscreen() {
        if (!document.fullscreenElement) {
            this.modal.requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    }

    async downloadPDF() {
        try {
            const response = await fetch(this.currentPdfPath);
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = this.modal.querySelector('.viewer-title').textContent || 'document.pdf';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        } catch (error) {
            console.error('下载PDF失败:', error);
        }
    }

    printPDF() {
        this.iframe.contentWindow.print();
    }
}
