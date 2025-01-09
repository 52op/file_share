class MediaViewer {
    constructor() {
        this.initModal();
        this.bindEvents();
    }

    initModal() {
        const modal = document.createElement('div');
        modal.className = 'media-viewer-modal';
        modal.innerHTML = `
            <div class="viewer-header">
                <span class="viewer-title"></span>
                <div class="viewer-controls">
                    <button class="btn-pip" title="画中画"><i class="bi bi-pip"></i></button>
                    <button class="btn-page-fullscreen" title="网页全屏"><i class="bi bi-arrows-angle-expand"></i></button>
                    <button class="btn-fullscreen" title="设备全屏"><i class="bi bi-arrows-fullscreen"></i></button>
                    <button class="btn-close" title="关闭"><i class="bi bi-x-lg"></i></button>
                </div>
            </div>
            <div class="viewer-container">
                <div class="media-wrapper">
                    <video class="media-player" controls playsinline>
                        您的浏览器不支持 HTML5 视频播放
                    </video>
                    <div class="audio-poster">
                        <i class="bi bi-disc"></i>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        this.modal = modal;
        this.player = modal.querySelector('.media-player');
        this.container = modal.querySelector('.viewer-container');
        this.mediaWrapper = modal.querySelector('.media-wrapper');
        this.audioPoster = modal.querySelector('.audio-poster');
        // 添加退出全屏按钮
        const exitButton = document.createElement('button');
        exitButton.className = 'exit-fullscreen';
        exitButton.innerHTML = '<i class="bi bi-fullscreen-exit"></i> 退出全屏';
        exitButton.onclick = () => this.togglePageFullscreen();
        this.container.appendChild(exitButton);
    }

    bindEvents() {
        // 画中画
        this.modal.querySelector('.btn-pip').onclick = () => this.togglePiP();

        // 网页全屏
        this.modal.querySelector('.btn-page-fullscreen').onclick = () => this.togglePageFullscreen();

        // 设备全屏
        this.modal.querySelector('.btn-fullscreen').onclick = () => this.toggleFullscreen();

        // 关闭按钮
        this.modal.querySelector('.btn-close').onclick = () => this.close();

        // ESC键关闭（非全屏状态）
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modal.classList.contains('active') && !document.fullscreenElement) {
                this.close();
            }
        });

        // 监听播放错误
        this.player.addEventListener('error', () => {
            this.showError('媒体加载失败');
        });

        // 监听画中画状态变化
        this.player.addEventListener('enterpictureinpicture', () => {
            this.modal.querySelector('.btn-pip').classList.add('active');
        });

        this.player.addEventListener('leavepictureinpicture', () => {
            this.modal.querySelector('.btn-pip').classList.remove('active');
        });
    }

    show(mediaPath, title = '', type = 'video') {
        this.modal.classList.add('active');
        this.modal.querySelector('.viewer-title').textContent = title;

        // 重置之前的样式
        this.player.style.width = '';
        this.player.style.height = '';
        this.mediaWrapper.style.display = 'flex';
        this.mediaWrapper.style.alignItems = 'center';
        this.mediaWrapper.style.justifyContent = 'center';

        // 设置媒体源
        this.player.src = mediaPath;

        // 视频加载完成后设置合适的尺寸
        this.player.addEventListener('loadedmetadata', () => {
            const videoRatio = this.player.videoWidth / this.player.videoHeight;
            const containerWidth = this.container.clientWidth;
            const containerHeight = this.container.clientHeight;
            const containerRatio = containerWidth / containerHeight;

            if (videoRatio < 1) { // 竖向视频
                const maxHeight = containerHeight;
                const maxWidth = maxHeight * videoRatio;
                this.player.style.height = `${maxHeight}px`;
                this.player.style.width = `${maxWidth}px`;
            } else { // 横向视频
                const maxWidth = containerWidth;
                const maxHeight = maxWidth / videoRatio;
                this.player.style.width = `${maxWidth}px`;
                this.player.style.height = `${maxHeight}px`;
            }
        });

        // 根据类型显示不同样式
        if (type === 'audio') {
            this.mediaWrapper.classList.add('audio-mode');
            this.player.removeAttribute('poster');
        } else {
            this.mediaWrapper.classList.remove('audio-mode');
        }

        // 自动播放
        this.player.play().catch(() => {
            console.log('自动播放被阻止');
        });
    }

    close() {
        this.player.pause();
        this.player.src = '';
        this.modal.classList.remove('active', 'page-fullscreen');

        // 如果在画中画模式，退出
        if (document.pictureInPictureElement) {
            document.exitPictureInPicture();
        }
    }

    async togglePiP() {
        try {
            if (document.pictureInPictureElement) {
                await document.exitPictureInPicture();
            } else {
                await this.player.requestPictureInPicture();
            }
        } catch (error) {
            this.showError('画中画模式不可用');
        }
    }

    togglePageFullscreen() {
        this.modal.classList.toggle('page-fullscreen');
        this.player.focus();
    }

    toggleFullscreen() {
        if (!document.fullscreenElement) {
            this.player.requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    }

    showError(message) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'media-error';
        errorDiv.textContent = message;

        this.container.appendChild(errorDiv);
        setTimeout(() => errorDiv.remove(), 3000);
    }
}
