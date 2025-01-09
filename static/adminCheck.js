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