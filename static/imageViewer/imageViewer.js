class ImageViewer {
    constructor() {
        this.scale = 1;
        this.rotation = 0;
        this.position = { x: 0, y: 0 };
        this.currentImageIndex = 0;
        this.images = [];  // 存储当前目录下的所有图片
        this.initModal();
        this.bindEvents();
    }

    initModal() {
        const modal = document.createElement('div');
        modal.className = 'image-viewer-modal';
        modal.innerHTML = `
            <div class="viewer-header">
                <span class="viewer-title"></span>
                <div class="viewer-controls">
                    <button class="btn-fullscreen"><i class="bi bi-arrows-fullscreen"></i></button>
                    <button class="btn-close"><i class="bi bi-x-lg"></i></button>
                </div>
            </div>
            <div class="viewer-container">
                <button class="nav-btn prev-btn"><i class="bi bi-chevron-left"></i></button>
                <img class="viewer-image" draggable="false">
                <button class="nav-btn next-btn"><i class="bi bi-chevron-right"></i></button>
            </div>
            <div class="viewer-toolbar">
                <button class="btn-rotate-left" title="向左旋转"><i class="bi bi-arrow-counterclockwise"></i></button>
                <button class="btn-rotate-right" title="向右旋转"><i class="bi bi-arrow-clockwise"></i></button>
                <button class="btn-zoom-out" title="缩小"><i class="bi bi-zoom-out"></i></button>
                <select class="zoom-select">
                    <option value="0.25">25%</option>
                    <option value="0.5">50%</option>
                    <option value="1" selected>100%</option>
                    <option value="1.5">150%</option>
                    <option value="2">200%</option>
                    <option value="3">300%</option>
                    <option value="fit">适应窗口</option>
                </select>
                <button class="btn-zoom-in" title="放大"><i class="bi bi-zoom-in"></i></button>
                <button class="btn-reset" title="重置"><i class="bi bi-arrow-repeat"></i></button>
                <button class="btn-save" title="保存"><i class="bi bi-download"></i></button>
                <span class="image-counter"></span>
            </div>
        `;

        document.body.appendChild(modal);
        this.modal = modal;
        this.image = modal.querySelector('.viewer-image');
        this.container = modal.querySelector('.viewer-container');
        this.imageCounter = modal.querySelector('.image-counter');
    }

    bindEvents() {
        // 拖曳功能
        let isDragging = false;
        let startPos = { x: 0, y: 0 };

        this.container.addEventListener('mousedown', (e) => {
            if (e.target === this.image) {
                isDragging = true;
                startPos = {
                    x: e.clientX - this.position.x,
                    y: e.clientY - this.position.y
                };
                this.container.style.cursor = 'grabbing';
            }
        });

        document.addEventListener('mousemove', (e) => {
            if (isDragging) {
                this.position.x = e.clientX - startPos.x;
                this.position.y = e.clientY - startPos.y;
                this.updateImageTransform();
            }
        });

        document.addEventListener('mouseup', () => {
            isDragging = false;
            this.container.style.cursor = 'grab';
        });

        // 缩放功能
        this.container.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? 0.9 : 1.1;
            this.scale *= delta;
            this.scale = Math.min(Math.max(0.1, this.scale), 5);
            this.updateImageTransform();
            this.updateZoomSelect();
        });

        // 工具栏事件
        this.modal.querySelector('.btn-zoom-in').onclick = () => this.zoom(1.1);
        this.modal.querySelector('.btn-zoom-out').onclick = () => this.zoom(0.9);
        this.modal.querySelector('.btn-rotate-left').onclick = () => this.rotate(-90);
        this.modal.querySelector('.btn-rotate-right').onclick = () => this.rotate(90);
        this.modal.querySelector('.btn-reset').onclick = () => this.reset();
        this.modal.querySelector('.btn-save').onclick = () => this.saveImage();
        this.modal.querySelector('.btn-fullscreen').onclick = () => this.toggleFullscreen();
        this.modal.querySelector('.btn-close').onclick = () => this.close();

        // 缩放选择
        this.modal.querySelector('.zoom-select').onchange = (e) => {
            const value = e.target.value;
            if (value === 'fit') {
                this.fitToWindow();
            } else {
                this.scale = parseFloat(value);
                this.updateImageTransform();
            }
        };

        // 上一张下一张导航按钮事件
        this.modal.querySelector('.prev-btn').onclick = () => this.showPrevious();
        this.modal.querySelector('.next-btn').onclick = () => this.showNext();

        // 键盘快捷键
        document.addEventListener('keydown', (e) => {
            if (!this.modal.classList.contains('active')) return;

            switch(e.key) {
                case 'Escape': this.close(); break;
                case 'ArrowLeft': this.showPrevious(); break;
                case 'ArrowRight': this.showNext(); break;
                case '+': if (e.ctrlKey) { e.preventDefault(); this.zoom(1.1); } break;
                case '-': if (e.ctrlKey) { e.preventDefault(); this.zoom(0.9); } break;
                case '0': if (e.ctrlKey) { e.preventDefault(); this.reset(); } break;
            }
        });
    }

    // 设置当前目录下的所有图片
    setImages(images) {
        this.images = images;
    }

show(imagePath, title = '') {
    try {
        // 修正路径格式
        const normalizedPath = imagePath.replace(/^\/+/, ''); // 移除开头的斜杠

        // 找到当前图片在数组中的索引
        this.currentImageIndex = this.images.findIndex(img => {
            const imgPath = img.path.replace(/^\/+/, '');
            return imgPath === normalizedPath;
        });

        if (this.currentImageIndex === -1) {
            console.error('Image not found in the list:', imagePath);
            return;
        }

        this.modal.classList.add('active');
        const currentImage = this.images[this.currentImageIndex];

        // 设置标题
        this.modal.querySelector('.viewer-title').textContent = currentImage.name;

        // 重置位置和旋转
        this.rotation = 0;
        this.position = { x: 0, y: 0 };

        // 设置图片加载完成的回调
        this.image.onload = () => {
            // 等图片加载完成后再调用 fitToWindow
            this.fitToWindow();
        };

        // 设置图片源
        this.image.src = `/preview/${currentImage.path}`;

        // 更新计数器和导航按钮
        this.updateCounter();
        this.updateNavigationButtons();
    } catch (error) {
        console.error('Error showing image:', error);
    }
}

updateImage() {
    const currentImage = this.images[this.currentImageIndex];
    if (!currentImage) return;

    // 更新标题和计数器
    this.modal.querySelector('.viewer-title').textContent = currentImage.name;
    this.updateCounter();
    this.updateNavigationButtons();

    // 重置位置和旋转
    this.rotation = 0;
    this.position = { x: 0, y: 0 };

    // 设置图片加载完成的回调
    this.image.onload = () => {
        // 等图片加载完成后再调用 fitToWindow
        this.fitToWindow();
    };

    // 设置新图片源
    this.image.src = `/preview/${currentImage.path}`;
}

    updateCounter() {
        this.imageCounter.textContent = `${this.currentImageIndex + 1} / ${this.images.length}`;
    }

    updateNavigationButtons() {
        const prevBtn = this.modal.querySelector('.prev-btn');
        const nextBtn = this.modal.querySelector('.next-btn');

        prevBtn.style.visibility = this.currentImageIndex > 0 ? 'visible' : 'hidden';
        nextBtn.style.visibility = this.currentImageIndex < this.images.length - 1 ? 'visible' : 'hidden';
    }

    showPrevious() {
        if (this.currentImageIndex > 0) {
            this.currentImageIndex--;
            this.updateImage();
        }
    }

    showNext() {
        if (this.currentImageIndex < this.images.length - 1) {
            this.currentImageIndex++;
            this.updateImage();
        }
    }

    show_one(imagePath, title = '') {
        this.modal.classList.add('active');
        this.modal.querySelector('.viewer-title').textContent = title;
        // 先重置状态
        this.reset();

        // 设置图片加载完成的回调
        this.image.onload = () => {
            this.fitToWindow();
        };

        // 设置图片源
        this.image.src = imagePath;
    }

    close() {
        this.modal.classList.remove('active');
    }

    zoom(factor) {
        this.scale *= factor;
        this.scale = Math.min(Math.max(0.1, this.scale), 5);
        this.updateImageTransform();
        this.updateZoomSelect();
    }

    rotate(degrees) {
        this.rotation = (this.rotation + degrees) % 360;
        this.updateImageTransform();
    }

    reset() {
        /* 1比1显示
        this.scale = 1;
        this.rotation = 0;
        this.position = { x: 0, y: 0 };
        this.updateImageTransform();
        this.updateZoomSelect();
         */
        // 重置旋转角度
        this.rotation = 0;
        // 重置位置
        this.position = { x: 0, y: 0 };
        // 调用适应窗口方法来设置合适的缩放比例
        this.fitToWindow();
    }

    fitToWindow() {
        // 获取容器尺寸
        const containerRect = this.container.getBoundingClientRect();

        // 获取图片原始尺寸
        const naturalWidth = this.image.naturalWidth;
        const naturalHeight = this.image.naturalHeight;

        // 计算缩放比例
        const scaleX = containerRect.width / naturalWidth;
        const scaleY = containerRect.height / naturalHeight;
        this.scale = Math.min(scaleX, scaleY);

        // 重置位置并更新变换
        this.position = { x: 0, y: 0 };
        this.updateImageTransform();
        this.updateZoomSelect();
    }

    updateImageTransform() {
        this.image.style.transform = `translate(${this.position.x}px, ${this.position.y}px) scale(${this.scale}) rotate(${this.rotation}deg)`;
    }

    updateZoomSelect() {
        const select = this.modal.querySelector('.zoom-select');
        const percentage = Math.round(this.scale * 100);
        select.value = this.scale;

        if (!Array.from(select.options).some(option => option.value === String(this.scale))) {
            select.value = 'custom';
        }
    }

    toggleFullscreen() {
        if (!document.fullscreenElement) {
            this.modal.requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    }

    async saveImage() {
        try {
            const response = await fetch(this.image.src);
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = this.modal.querySelector('.viewer-title').textContent || 'image';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        } catch (error) {
            console.error('下载图片失败:', error);
        }
    }
}
