// 保存原始的 alert
window._originalAlert = window.alert;

// alert 相关样式
const style = document.createElement('style');
style.textContent = `
.custom-alert {
    position: fixed;
    top: 20px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(0, 0, 0, 0.8);
    color: white;
    padding: 15px 25px;
    border-radius: 8px;
    z-index: 10000;
    font-size: 16px;
    max-width: 80%;
    word-wrap: break-word;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    animation: alertSlideDown 0.3s ease-out;
    cursor: pointer;
}
`;
document.head.appendChild(style);

// alert 实现
window.alert = function(message) {
    const alertEl = document.createElement('div');
    alertEl.className = 'custom-alert';
    alertEl.textContent = message;
    document.body.appendChild(alertEl);

    const closeAlert = () => {
        alertEl.style.animation = 'alertFadeOut 0.2s ease-out forwards';
        setTimeout(() => alertEl.remove(), 200);
    };

    setTimeout(() => {
        document.addEventListener('click', closeAlert, { once: true });
    }, 100);

    setTimeout(closeAlert, 3000);
};

// Bootstrap 风格的 confirm
FS_confirm = function(message, title = '确认') {
    return new Promise((resolve) => {
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.setAttribute('tabindex', '-1');

        modal.innerHTML = `
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">${title}</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p>${message}</p>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                        <button type="button" class="btn btn-primary confirm-btn">确定</button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        const bootstrapModal = new bootstrap.Modal(modal);

        modal.querySelector('.confirm-btn').onclick = () => {
            bootstrapModal.hide();
            resolve(true);
        };

        modal.addEventListener('hidden.bs.modal', () => {
            resolve(false);
            setTimeout(() => modal.remove(), 200);
        });

        bootstrapModal.show();
    });
};

// Bootstrap 风格的 prompt
FS_prompt = function(message, defaultValue = '', title = '请输入') {
    return new Promise((resolve) => {
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.setAttribute('tabindex', '-1');

        modal.innerHTML = `
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">${title}</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <form>
                            <div class="mb-3">
                                <label class="form-label">${message}</label>
                                <input type="text" class="form-control" value="${defaultValue}">
                            </div>
                        </form>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                        <button type="button" class="btn btn-primary confirm-btn">确定</button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        const bootstrapModal = new bootstrap.Modal(modal);
        const input = modal.querySelector('input');

        modal.querySelector('form').onsubmit = (e) => {
            e.preventDefault();
            modal.querySelector('.confirm-btn').click();
        };

        modal.querySelector('.confirm-btn').onclick = () => {
            bootstrapModal.hide();
            resolve(input.value);
        };

        modal.addEventListener('hidden.bs.modal', () => {
            resolve(null);
            setTimeout(() => modal.remove(), 200);
        });

        modal.addEventListener('shown.bs.modal', () => {
            input.select();
        });

        bootstrapModal.show();
    });
};
/*
// Confirm 示例
async function deleteItem() {
    if (await FS_confirm('确定要删除这个项目吗？', '删除确认')) {
        // 执行删除操作
    }
}

// Prompt 示例
async function rename() {
    const newName = await FS_prompt('请输入新的名称：', '默认名称', '重命名');
    if (newName !== null) {
        // 执行重命名操作
    }
}

 */