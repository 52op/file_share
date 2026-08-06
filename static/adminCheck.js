        function showAdminLogin() {
            new bootstrap.Modal(document.getElementById('adminLoginModal')).show();
        }

        async function handleAdminLogin(event) {
            event.preventDefault();
            const passwordInput = document.getElementById('adminPassword');
            const codeInput = document.getElementById('adminCode');
            const errorDiv = document.getElementById('loginError');

            const formData = new FormData();
            if (passwordInput) formData.append('password', passwordInput.value);
            if (codeInput) formData.append('code', codeInput.value);

            try {
                const response = await fetch('/admin/login', {
                    method: 'POST',
                    body: formData
                });
                if (response.redirected && response.url.includes('/2fa')) {
                    // 兼容旧版两步验证流程，跳转到验证码页面
                    window.location.href = response.url;
                    return;
                }
                if (response.ok) {
                    location.reload();
                } else {
                    if (passwordInput) passwordInput.classList.add('is-invalid');
                    errorDiv.textContent = codeInput ? '密码或验证码错误' : '管理密码错误';
                }
            } catch (error) {
                if (passwordInput) passwordInput.classList.add('is-invalid');
                errorDiv.textContent = '登录失败，请稍后重试';
            }
        }

 function clearAdminError() {
    const e = document.getElementById('loginError');
    if (e) e.textContent = '';
    const p = document.getElementById('adminPassword');
    const c = document.getElementById('adminCode');
    if (p) p.classList.remove('is-invalid');
    if (c) c.classList.remove('is-invalid');
}
const _adminPwd = document.getElementById('adminPassword');
const _adminCode = document.getElementById('adminCode');
if (_adminPwd) _adminPwd.addEventListener('input', clearAdminError);
if (_adminCode) _adminCode.addEventListener('input', clearAdminError);

// 目录管理员登录相关函数
function showDirAdminLogin() {
    new bootstrap.Modal(document.getElementById('dirAdminLoginModal')).show();
}

async function handleDirAdminLogin(event) {
    event.preventDefault();
    const passwordInput = document.getElementById('dirAdminPassword');
    const codeInput = document.getElementById('dirAdminCode');
    const errorDiv = document.getElementById('dirLoginError');

    const formData = new FormData();
    if (passwordInput) formData.append('password', passwordInput.value);
    if (codeInput) formData.append('code', codeInput.value);
    formData.append('dirname', document.getElementById('dirAdminDirname').value);

    try {
        const response = await fetch('/dir-admin/login', {
            method: 'POST',
            body: formData
        });
        if (response.redirected && response.url.includes('/2fa')) {
            // 兼容旧版两步验证流程，跳转到验证码页面
            window.location.href = response.url;
            return;
        }
        if (response.ok) {
            location.reload();
        } else {
            if (passwordInput) passwordInput.classList.add('is-invalid');
            errorDiv.textContent = codeInput ? '密码或验证码错误' : '目录管理密码错误';
        }
    } catch (error) {
        if (passwordInput) passwordInput.classList.add('is-invalid');
        errorDiv.textContent = '登录失败，请稍后重试';
    }
}

// 监听目录管理员密码输入
document.addEventListener('DOMContentLoaded', function() {
    const dirAdminPasswordInput = document.getElementById('dirAdminPassword');
    if (dirAdminPasswordInput) {
        dirAdminPasswordInput.addEventListener('input', function() {
            this.classList.remove('is-invalid');
            document.getElementById('dirLoginError').textContent = '';
        });
    }
});