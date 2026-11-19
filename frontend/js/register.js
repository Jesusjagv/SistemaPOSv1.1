/* ======================================================
   register.js — Public registration with 7-day demo
   ====================================================== */

(function () {
  // Redirect if already logged in
  if (isLoggedIn()) {
    const u = getUser();
    window.location.href = u.role === 'superadmin' ? '/superadmin.html' : '/pos.html';
    return;
  }

  const form = document.getElementById('register-form');
  const errBox = document.getElementById('register-error');
  const errMsg = document.getElementById('register-error-msg');
  const successBox = document.getElementById('register-success');
  const registerBtn = document.getElementById('register-btn');
  const pwdInput = document.getElementById('reg-password');
  const togglePwd = document.getElementById('toggle-pwd');

  togglePwd.addEventListener('click', () => {
    const isText = pwdInput.type === 'text';
    pwdInput.type = isText ? 'password' : 'text';
    togglePwd.innerHTML = isText
      ? '<i class="fa-solid fa-eye"></i>'
      : '<i class="fa-solid fa-eye-slash"></i>';
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errBox.style.display = 'none';
    registerBtn.disabled = true;
    registerBtn.innerHTML = '<span class="spinner"></span> Creando cuenta...';

    const company_name   = document.getElementById('reg-company').value.trim();
    const admin_name     = document.getElementById('reg-name').value.trim();
    const admin_email    = document.getElementById('reg-email').value.trim();
    const admin_username = document.getElementById('reg-username').value.trim();
    const admin_password = document.getElementById('reg-password').value;

    if (!company_name || !admin_name || !admin_username || !admin_password) {
      errMsg.textContent = 'Todos los campos obligatorios deben ser completados';
      errBox.style.display = 'flex';
      registerBtn.disabled = false;
      registerBtn.innerHTML = '<i class="fa-solid fa-rocket"></i> Crear Mi Cuenta Gratis';
      return;
    }

    if (admin_password.length < 6) {
      errMsg.textContent = 'La contrasena debe tener al menos 6 caracteres';
      errBox.style.display = 'flex';
      registerBtn.disabled = false;
      registerBtn.innerHTML = '<i class="fa-solid fa-rocket"></i> Crear Mi Cuenta Gratis';
      return;
    }

    try {
      await api.post('/auth/register', {
        company_name,
        admin_name,
        admin_email,
        admin_username,
        admin_password
      });

      // Show success state
      form.style.display = 'none';
      errBox.style.display = 'none';
      successBox.style.display = 'block';

      // Hide the trial badge
      const badge = document.querySelector('[style*="gift"]');
      if (badge && badge.parentElement) badge.parentElement.style.display = 'none';

    } catch (err) {
      errMsg.textContent = err.message;
      errBox.style.display = 'flex';
    } finally {
      registerBtn.disabled = false;
      registerBtn.innerHTML = '<i class="fa-solid fa-rocket"></i> Crear Mi Cuenta Gratis';
    }
  });
})();
