        function showAdminLogin() {
            new bootstrap.Modal(document.getElementById('adminLoginModal')).show();
        }

        async function handleAdminLogin(event) {
            event.preventDefault();
            const passwordInput = document.getElementById('adminPassword');
            const errorDiv = document.getElementById('loginError');

            const password = document.getElementById('adminPassword').value;
            const formData = new FormData();
            formData.append('password', password);

            try {
                const response = await fetch('/admin/login', {
                    method: 'POST',
                    body: formData
                });
                if (response.ok) {
                    location.reload();
                } else {
                    passwordInput.classList.add('is-invalid');
                    errorDiv.textContent = '管理密码错误';
                }
            } catch (error) {
                passwordInput.classList.add('is-invalid');
                errorDiv.textContent = '登录失败，请稍后重试';
            }
        }

 document.getElementById('adminPassword').addEventListener('input', function() {
    this.classList.remove('is-invalid');
    document.getElementById('loginError').textContent = '';
});//监听

// 目录管理员登录相关函数
function showDirAdminLogin() {
    new bootstrap.Modal(document.getElementById('dirAdminLoginModal')).show();
}

async function handleDirAdminLogin(event) {
    event.preventDefault();
    const passwordInput = document.getElementById('dirAdminPassword');
    const errorDiv = document.getElementById('dirLoginError');

    const password = passwordInput.value;
    const dirname = document.getElementById('dirAdminDirname').value;
    const formData = new FormData();
    formData.append('password', password);
    formData.append('dirname', dirname);

    try {
        const response = await fetch('/dir-admin/login', {
            method: 'POST',
            body: formData
        });
        if (response.ok) {
            location.reload();
        } else {
            passwordInput.classList.add('is-invalid');
            errorDiv.textContent = '目录管理密码错误';
        }
    } catch (error) {
        passwordInput.classList.add('is-invalid');
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