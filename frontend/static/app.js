const API_BASE = window.__API_BASE__ || "/api/v1";
const storageKeys = {
  accessToken: "task_console_access_token",
  refreshToken: "task_console_refresh_token",
  user: "task_console_user",
};

const state = {
  accessToken: localStorage.getItem(storageKeys.accessToken),
  refreshToken: localStorage.getItem(storageKeys.refreshToken),
  user: readStoredUser(),
  tasks: [],
};

const elements = {
  authPanel: document.getElementById("authPanel"),
  sessionPanel: document.getElementById("sessionPanel"),
  workspace: document.getElementById("workspace"),
  loginForm: document.getElementById("loginForm"),
  registerForm: document.getElementById("registerForm"),
  filtersForm: document.getElementById("filtersForm"),
  createTaskForm: document.getElementById("createTaskForm"),
  reloadTasksButton: document.getElementById("reloadTasksButton"),
  resetFiltersButton: document.getElementById("resetFiltersButton"),
  refreshButton: document.getElementById("refreshButton"),
  logoutButton: document.getElementById("logoutButton"),
  tasksList: document.getElementById("tasksList"),
  tasksMeta: document.getElementById("tasksMeta"),
  errorBanner: document.getElementById("errorBanner"),
  sessionEmail: document.getElementById("sessionEmail"),
  sessionRoles: document.getElementById("sessionRoles"),
  sessionTeams: document.getElementById("sessionTeams"),
  connectionBadge: document.getElementById("connectionBadge"),
  taskCardTemplate: document.getElementById("taskCardTemplate"),
};

bootstrap();

function bootstrap() {
  bindEvents();
  renderSession();
  if (state.accessToken) {
    loadCurrentUser().then(() => loadTasks()).catch(handleApiError);
  }
}

function bindEvents() {
  elements.loginForm.addEventListener("submit", handleLogin);
  elements.registerForm.addEventListener("submit", handleRegister);
  elements.filtersForm.addEventListener("submit", handleFiltersSubmit);
  elements.resetFiltersButton.addEventListener("click", handleFiltersReset);
  elements.createTaskForm.addEventListener("submit", handleCreateTask);
  elements.reloadTasksButton.addEventListener("click", () => loadTasks().catch(handleApiError));
  elements.refreshButton.addEventListener("click", handleRefresh);
  elements.logoutButton.addEventListener("click", handleLogout);
}

async function handleLogin(event) {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  await authRequest("/auth/login", {
    email: formData.get("email"),
    password: formData.get("password"),
  });
  event.currentTarget.reset();
}

async function handleRegister(event) {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  await authRequest("/auth/register", {
    email: formData.get("email"),
    password: formData.get("password"),
  });
  event.currentTarget.reset();
}

async function authRequest(path, payload) {
  clearError();
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await parseJson(response);
  if (!response.ok) {
    throw createApiError(response, data);
  }
  applySession(data);
  await loadCurrentUser();
  await loadTasks();
}

async function handleRefresh() {
  clearError();
  await refreshSession();
  await loadCurrentUser();
  await loadTasks();
}

async function handleLogout() {
  clearError();
  if (state.refreshToken) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: state.refreshToken }),
    }).catch(() => undefined);
  }
  clearSession();
}

async function handleFiltersSubmit(event) {
  event.preventDefault();
  clearError();
  await loadTasks();
}

function handleFiltersReset() {
  window.setTimeout(() => loadTasks().catch(handleApiError), 0);
}

async function handleCreateTask(event) {
  event.preventDefault();
  clearError();
  const formData = new FormData(event.currentTarget);
  const deadline = formData.get("deadline");
  const payload = {
    title: String(formData.get("title") || "").trim(),
    description: String(formData.get("description") || "").trim() || null,
    assignee_id: String(formData.get("assignee_id") || "").trim(),
    team_id: String(formData.get("team_id") || "").trim(),
    priority: formData.get("priority"),
    deadline: deadline ? new Date(String(deadline)).toISOString() : null,
  };

  await apiFetch("/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": `create-${crypto.randomUUID()}`,
    },
  });
  event.currentTarget.reset();
  await loadTasks();
}

async function handleStatusChange(taskId, form) {
  clearError();
  const formData = new FormData(form);
  const payload = {
    status: formData.get("status"),
    comment: String(formData.get("comment") || "").trim() || null,
  };
  await apiFetch(`/tasks/${taskId}/status`, {
    method: "PATCH",
    body: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": `status-${crypto.randomUUID()}`,
    },
  });
  await loadTasks();
}

async function loadCurrentUser() {
  const user = await apiFetch("/auth/me");
  state.user = user;
  localStorage.setItem(storageKeys.user, JSON.stringify(user));
  renderSession();
}

async function loadTasks() {
  const params = new URLSearchParams();
  const formData = new FormData(elements.filtersForm);
  for (const [key, rawValue] of formData.entries()) {
    const value = String(rawValue || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  const result = await apiFetch(`/tasks${query ? `?${query}` : ""}`);
  state.tasks = result.items || [];
  renderTasks(result);
}

async function apiFetch(path, options = {}, allowRetry = true) {
  const headers = new Headers(options.headers || {});
  if (state.accessToken) {
    headers.set("Authorization", `Bearer ${state.accessToken}`);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  const data = await parseJson(response);

  if (response.status === 401 && allowRetry && state.refreshToken) {
    await refreshSession();
    return apiFetch(path, options, false);
  }

  if (!response.ok) {
    throw createApiError(response, data);
  }
  return data;
}

async function refreshSession() {
  if (!state.refreshToken) {
    clearSession();
    throw new Error("Сессия истекла. Выполни вход заново.");
  }

  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: state.refreshToken }),
  });
  const data = await parseJson(response);
  if (!response.ok) {
    clearSession();
    throw createApiError(response, data);
  }
  applySession(data);
}

function applySession(data) {
  state.accessToken = data.access_token;
  state.refreshToken = data.refresh_token;
  state.user = data.user;
  localStorage.setItem(storageKeys.accessToken, state.accessToken);
  localStorage.setItem(storageKeys.refreshToken, state.refreshToken);
  localStorage.setItem(storageKeys.user, JSON.stringify(state.user));
  renderSession();
}

function clearSession() {
  state.accessToken = null;
  state.refreshToken = null;
  state.user = null;
  state.tasks = [];
  localStorage.removeItem(storageKeys.accessToken);
  localStorage.removeItem(storageKeys.refreshToken);
  localStorage.removeItem(storageKeys.user);
  renderSession();
  renderTasks({ items: [], total: 0, page: 1, page_size: 20, pages: 0 });
}

function renderSession() {
  const isAuthenticated = Boolean(state.accessToken && state.user);
  elements.authPanel.classList.toggle("hidden", isAuthenticated);
  elements.sessionPanel.classList.toggle("hidden", !isAuthenticated);
  elements.workspace.classList.toggle("hidden", !isAuthenticated);

  if (!isAuthenticated) {
    elements.sessionEmail.textContent = "-";
    elements.sessionRoles.textContent = "-";
    elements.sessionTeams.textContent = "-";
    elements.connectionBadge.textContent = "Ожидает авторизации";
    return;
  }

  elements.sessionEmail.textContent = state.user.email;
  elements.sessionRoles.textContent = (state.user.roles || []).join(", ") || "-";
  elements.sessionTeams.textContent = (state.user.team_ids || []).join(", ") || "-";
  elements.connectionBadge.textContent = "Сессия активна";
}

function renderTasks(result) {
  const items = result.items || [];
  elements.tasksMeta.textContent = `Всего: ${result.total ?? 0} | Страница: ${result.page ?? 1}/${result.pages ?? 0}`;

  if (!items.length) {
    elements.tasksList.className = "tasks-list empty-state";
    elements.tasksList.innerHTML = "<p>По текущим фильтрам задач нет.</p>";
    return;
  }

  elements.tasksList.className = "tasks-list";
  elements.tasksList.innerHTML = "";

  for (const task of items) {
    const fragment = elements.taskCardTemplate.content.cloneNode(true);
    const card = fragment.querySelector(".task-card");
    fillTaskField(fragment, "title", task.title);
    fillTaskField(fragment, "description", task.description || "Без описания");
    fillTaskField(fragment, "id", task.id);
    fillTaskField(fragment, "owner_id", task.owner_id);
    fillTaskField(fragment, "assignee_id", task.assignee_id);
    fillTaskField(fragment, "team_id", task.team_id);
    fillTaskField(fragment, "priority", task.priority);
    fillTaskField(fragment, "deadline", task.deadline ? formatDate(task.deadline) : "-" );
    fillTaskField(fragment, "status", task.status);

    const statusSelect = fragment.querySelector('select[name="status"]');
    if (statusSelect) {
      statusSelect.value = normalizeNextStatus(task.status);
    }

    const form = fragment.querySelector(".status-form");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await handleStatusChange(task.id, form);
      } catch (error) {
        handleApiError(error);
      }
    });

    elements.tasksList.appendChild(card);
  }
}

function fillTaskField(root, field, value) {
  const node = root.querySelector(`[data-field="${field}"]`);
  if (node) {
    node.textContent = value;
  }
}

function normalizeNextStatus(currentStatus) {
  switch (currentStatus) {
    case "todo":
      return "in_progress";
    case "in_progress":
      return "review";
    case "review":
      return "done";
    default:
      return "cancelled";
  }
}

function formatDate(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function parseJson(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return Promise.resolve(null);
  }
  return response.json();
}

function createApiError(response, payload) {
  const error = new Error(payload?.error?.message || payload?.detail || `HTTP ${response.status}`);
  error.status = response.status;
  error.payload = payload;
  return error;
}

function handleApiError(error) {
  const message = error?.payload?.error?.message || error?.message || "Неизвестная ошибка";
  const code = error?.payload?.error?.code;
  const details = error?.payload?.error?.details;
  const detailText = details ? `\n${JSON.stringify(details, null, 2)}` : "";
  elements.errorBanner.textContent = `${code ? `[${code}] ` : ""}${message}${detailText}`;
  elements.errorBanner.classList.remove("hidden");
  console.error(error);
}

function clearError() {
  elements.errorBanner.textContent = "";
  elements.errorBanner.classList.add("hidden");
}

function readStoredUser() {
  const raw = localStorage.getItem(storageKeys.user);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}