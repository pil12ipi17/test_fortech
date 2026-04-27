const API_BASE = window.__API_BASE__ || "/api/v1";
const storageKeys = {
  accessToken: "task_console_access_token",
  refreshToken: "task_console_refresh_token",
  user: "task_console_user",
};

const screenMeta = {
  tasks: {
    title: "Список задач",
    subtitle: "Фильтруй данные, смотри lifecycle и меняй статусы через реальные backend-контракты.",
  },
  create: {
    title: "Создание задачи",
    subtitle: "Новый таск уходит в task-service с idempotency key и обычной backend-валидацией.",
  },
  reference: {
    title: "Справочник пользователей и команд",
    subtitle: "Этот экран помогает быстро находить рабочие ID и понимать, какие маршруты доступны текущей роли.",
  },
  manage: {
    title: "Управление пользователями и командами",
    subtitle: "Админские операции поверх уже существующих auth-service контрактов: создание пользователей, команд и membership.",
  },
};

const state = {
  accessToken: localStorage.getItem(storageKeys.accessToken),
  refreshToken: localStorage.getItem(storageKeys.refreshToken),
  user: readStoredUser(),
  tasks: [],
  users: [],
  teams: [],
  userDirectoryState: "idle",
  teamDirectoryState: "idle",
  activeScreen: "tasks",
  authMode: "login",
  bannerTimer: null,
};

const elements = {
  authView: document.getElementById("authView"),
  appView: document.getElementById("appView"),
  loginForm: document.getElementById("loginForm"),
  registerForm: document.getElementById("registerForm"),
  showLoginButton: document.getElementById("showLoginButton"),
  showRegisterButton: document.getElementById("showRegisterButton"),
  filtersForm: document.getElementById("filtersForm"),
  createTaskForm: document.getElementById("createTaskForm"),
  createAdminUserForm: document.getElementById("createAdminUserForm"),
  createTeamForm: document.getElementById("createTeamForm"),
  addTeamMemberForm: document.getElementById("addTeamMemberForm"),
  updateUserRolesForm: document.getElementById("updateUserRolesForm"),
  removeTeamMemberForm: document.getElementById("removeTeamMemberForm"),
  reloadTasksButton: document.getElementById("reloadTasksButton"),
  resetFiltersButton: document.getElementById("resetFiltersButton"),
  refreshButton: document.getElementById("refreshButton"),
  logoutButton: document.getElementById("logoutButton"),
  openTasksViewButton: document.getElementById("openTasksViewButton"),
  openCreateViewButton: document.getElementById("openCreateViewButton"),
  openReferenceViewButton: document.getElementById("openReferenceViewButton"),
  openManageViewButton: document.getElementById("openManageViewButton"),
  tasksScreen: document.getElementById("tasksScreen"),
  createScreen: document.getElementById("createScreen"),
  referenceScreen: document.getElementById("referenceScreen"),
  manageScreen: document.getElementById("manageScreen"),
  screenTitle: document.getElementById("screenTitle"),
  screenSubtitle: document.getElementById("screenSubtitle"),
  tasksList: document.getElementById("tasksList"),
  tasksMeta: document.getElementById("tasksMeta"),
  errorBanner: document.getElementById("errorBanner"),
  sessionEmail: document.getElementById("sessionEmail"),
  sessionRoles: document.getElementById("sessionRoles"),
  sessionTeams: document.getElementById("sessionTeams"),
  connectionBadge: document.getElementById("connectionBadge"),
  taskCardTemplate: document.getElementById("taskCardTemplate"),
  referenceItemTemplate: document.getElementById("referenceItemTemplate"),
  usersReference: document.getElementById("usersReference"),
  teamsReference: document.getElementById("teamsReference"),
  usersAccessBadge: document.getElementById("usersAccessBadge"),
  teamsAccessBadge: document.getElementById("teamsAccessBadge"),
  userIdSuggestions: document.getElementById("userIdSuggestions"),
  teamIdSuggestions: document.getElementById("teamIdSuggestions"),
};

bootstrap();

async function bootstrap() {
  bindEvents();
  renderAuthMode();
  renderSession();
  setScreen(state.activeScreen);
  renderReferenceData();

  if (!state.accessToken) {
    return;
  }

  try {
    await hydrateWorkspace();
  } catch (error) {
    clearSession();
    handleApiError(error);
  }
}

function bindEvents() {
  elements.showLoginButton.addEventListener("click", () => setAuthMode("login"));
  elements.showRegisterButton.addEventListener("click", () => setAuthMode("register"));
  elements.loginForm.addEventListener("submit", handleLogin);
  elements.registerForm.addEventListener("submit", handleRegister);
  elements.filtersForm.addEventListener("submit", handleFiltersSubmit);
  elements.resetFiltersButton.addEventListener("click", handleFiltersReset);
  elements.createTaskForm.addEventListener("submit", handleCreateTask);
  elements.createAdminUserForm.addEventListener("submit", handleAdminUserCreate);
  elements.createTeamForm.addEventListener("submit", handleTeamCreate);
  elements.addTeamMemberForm.addEventListener("submit", handleTeamMemberCreate);
  elements.updateUserRolesForm.addEventListener("submit", handleUserRolesUpdate);
  elements.removeTeamMemberForm.addEventListener("submit", handleTeamMemberRemove);
  elements.reloadTasksButton.addEventListener("click", () => loadTasks().catch(handleApiError));
  elements.refreshButton.addEventListener("click", handleRefresh);
  elements.logoutButton.addEventListener("click", handleLogout);
  elements.openTasksViewButton.addEventListener("click", () => setScreen("tasks"));
  elements.openCreateViewButton.addEventListener("click", () => setScreen("create"));
  elements.openReferenceViewButton.addEventListener("click", () => setScreen("reference"));
  elements.openManageViewButton.addEventListener("click", () => setScreen("manage"));
}

async function hydrateWorkspace() {
  await loadCurrentUser();
  await Promise.all([loadTasks(), loadReferenceData()]);
}

function setAuthMode(mode) {
  state.authMode = mode;
  renderAuthMode();
  clearBanner();
}

function renderAuthMode() {
  const isLogin = state.authMode === "login";
  elements.loginForm.classList.toggle("hidden", !isLogin);
  elements.registerForm.classList.toggle("hidden", isLogin);
  elements.showLoginButton.classList.toggle("active", isLogin);
  elements.showRegisterButton.classList.toggle("active", !isLogin);
}

function setScreen(screen) {
  if (screen === "manage" && !isAdmin()) {
    screen = "tasks";
  }

  state.activeScreen = screen;
  elements.tasksScreen.classList.toggle("hidden", screen !== "tasks");
  elements.createScreen.classList.toggle("hidden", screen !== "create");
  elements.referenceScreen.classList.toggle("hidden", screen !== "reference");
  elements.manageScreen.classList.toggle("hidden", screen !== "manage");
  elements.openTasksViewButton.classList.toggle("active", screen === "tasks");
  elements.openCreateViewButton.classList.toggle("active", screen === "create");
  elements.openReferenceViewButton.classList.toggle("active", screen === "reference");
  elements.openManageViewButton.classList.toggle("active", screen === "manage");

  const meta = screenMeta[screen];
  elements.screenTitle.textContent = meta.title;
  elements.screenSubtitle.textContent = meta.subtitle;
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
  clearBanner();
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
  await hydrateWorkspace();
  setScreen("tasks");
  showBanner("Сессия успешно открыта.", "success");
}

async function handleRefresh() {
  clearBanner();
  await refreshSession();
  await hydrateWorkspace();
  showBanner("Токены обновлены.", "success");
}

async function handleLogout() {
  clearBanner();
  if (state.refreshToken) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: state.refreshToken }),
    }).catch(() => undefined);
  }

  clearSession();
  setAuthMode("login");
  showBanner("Сессия завершена.", "success");
}

async function handleFiltersSubmit(event) {
  event.preventDefault();
  clearBanner();
  await loadTasks();
}

function handleFiltersReset() {
  window.setTimeout(() => loadTasks().catch(handleApiError), 0);
}

async function handleCreateTask(event) {
  event.preventDefault();
  clearBanner();

  const form = event.currentTarget;
  if (form.dataset.submitting === "true") {
    return;
  }

  form.dataset.submitting = "true";
  const idempotencyKey = form.dataset.idempotencyKey || `create-${crypto.randomUUID()}`;
  form.dataset.idempotencyKey = idempotencyKey;
  const submitButton = form.querySelector("[type='submit']");
  if (submitButton) {
    submitButton.disabled = true;
  }

  try {
    const formData = new FormData(form);
    const deadline = formData.get("deadline");
    const assigneeId = resolveUserReference(String(formData.get("assignee_id") || "").trim());
    const teamId = resolveTeamReference(String(formData.get("team_id") || "").trim());
    const payload = {
      title: String(formData.get("title") || "").trim(),
      description: String(formData.get("description") || "").trim() || null,
      assignee_id: assigneeId,
      team_id: teamId,
      priority: formData.get("priority"),
      deadline: deadline ? new Date(String(deadline)).toISOString() : null,
    };

    const task = await apiFetch("/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
    });

    form.reset();
    delete form.dataset.idempotencyKey;
    setScreen("tasks");
    await loadTasks();
    showBanner(`Задача «${task.title}» создана.`, "success");
  } finally {
    delete form.dataset.submitting;
    if (submitButton) {
      submitButton.disabled = false;
    }
  }
}
async function handleAdminUserCreate(event) {
  event.preventDefault();
  clearBanner();

  const form = event.currentTarget;
  const formData = new FormData(form);
  const roles = Array.from(form.querySelectorAll('input[name="roles"]:checked')).map((input) => input.value);
  const teamIds = parseCsvList(formData.get("team_ids")).map(resolveTeamReference);

  if (!roles.length) {
    showBanner("Для нового пользователя нужно выбрать хотя бы одну роль.");
    return;
  }

  const payload = {
    email: String(formData.get("email") || "").trim(),
    password: String(formData.get("password") || ""),
    roles,
    team_ids: teamIds,
  };

  const user = await apiFetch("/users", {
    method: "POST",
    body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" },
  });

  form.reset();
  form.querySelector('input[name="roles"][value="user"]').checked = true;
  await loadReferenceData();
  showBanner(`Пользователь ${user.email} создан.`, "success");
}

async function handleTeamCreate(event) {
  event.preventDefault();
  clearBanner();

  const formData = new FormData(event.currentTarget);
  const team = await apiFetch("/teams", {
    method: "POST",
    body: JSON.stringify({ name: String(formData.get("name") || "").trim() }),
    headers: { "Content-Type": "application/json" },
  });

  event.currentTarget.reset();
  await loadReferenceData();
  showBanner(`Команда ${team.name} создана.`, "success");
}

async function handleTeamMemberCreate(event) {
  event.preventDefault();
  clearBanner();

  const formData = new FormData(event.currentTarget);
  const teamId = resolveTeamReference(String(formData.get("team_id") || "").trim());
  const userId = resolveUserReference(String(formData.get("user_id") || "").trim());
  await apiFetch(`/teams/${teamId}/members`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
    headers: { "Content-Type": "application/json" },
  });

  event.currentTarget.reset();
  await loadReferenceData();
  showBanner("Участник добавлен в команду.", "success");
}

async function handleUserRolesUpdate(event) {
  event.preventDefault();
  clearBanner();

  const form = event.currentTarget;
  const formData = new FormData(form);
  const userId = resolveUserReference(String(formData.get("user_id") || "").trim());
  const roles = Array.from(form.querySelectorAll('input[name="roles"]:checked')).map((input) => input.value);

  if (!roles.length) {
    showBanner("Для обновления нужно выбрать хотя бы одну роль.");
    return;
  }

  const user = await apiFetch(`/users/${userId}/roles`, {
    method: "PATCH",
    body: JSON.stringify({ roles }),
    headers: { "Content-Type": "application/json" },
  });

  form.reset();
  await loadReferenceData();
  if (state.user?.id === user.id) {
    state.user = user;
    localStorage.setItem(storageKeys.user, JSON.stringify(user));
    renderSession();
  }
  showBanner(`Роли пользователя ${user.email} обновлены.`, "success");
}

async function handleTeamMemberRemove(event) {
  event.preventDefault();
  clearBanner();

  const formData = new FormData(event.currentTarget);
  const teamId = resolveTeamReference(String(formData.get("team_id") || "").trim());
  const userId = resolveUserReference(String(formData.get("user_id") || "").trim());

  await apiFetch(`/teams/${teamId}/members/${userId}`, {
    method: "DELETE",
  });

  event.currentTarget.reset();
  await loadReferenceData();
  showBanner("Участник удалён из команды.", "success");
}

async function handleStatusChange(taskId, form) {
  clearBanner();
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
  showBanner(`Статус задачи обновлён до ${payload.status}.`, "success");
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
  state.tasks = toItems(result);
  renderTasks(result);
}

async function loadReferenceData() {
  state.userDirectoryState = "loading";
  state.teamDirectoryState = "loading";
  renderReferenceData();

  try {
    const usersResponse = await apiFetch("/users?page=1&page_size=100", {}, true, true);
    state.users = toItems(usersResponse);
    state.userDirectoryState = "ready";
  } catch (error) {
    state.users = [];
    state.userDirectoryState = error?.status === 403 ? "denied" : "unavailable";
  }

  try {
    const teamsResponse = await apiFetch("/teams?page=1&page_size=100", {}, true, true);
    state.teams = toItems(teamsResponse);
    state.teamDirectoryState = "ready";
  } catch (error) {
    state.teams = [];
    state.teamDirectoryState = error?.status === 403 ? "denied" : "unavailable";
  }

  renderReferenceData();
  renderSession();
}

async function apiFetch(path, options = {}, allowRetry = true, quiet = false) {
  const headers = new Headers(options.headers || {});
  if (state.accessToken) {
    headers.set("Authorization", `Bearer ${state.accessToken}`);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const data = await parseJson(response);

  if (response.status === 401 && allowRetry && state.refreshToken) {
    try {
      await refreshSession();
      return apiFetch(path, options, false, quiet);
    } catch (error) {
      clearSession();
      throw error;
    }
  }

  if (response.status === 401 && !allowRetry) {
    clearSession();
  }

  if (!response.ok) {
    const error = createApiError(response, data);
    if (!quiet) {
      throw error;
    }
    throw error;
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
  state.users = [];
  state.teams = [];
  state.userDirectoryState = "idle";
  state.teamDirectoryState = "idle";
  localStorage.removeItem(storageKeys.accessToken);
  localStorage.removeItem(storageKeys.refreshToken);
  localStorage.removeItem(storageKeys.user);
  renderSession();
  renderTasks({ items: [], total: 0, page: 1, page_size: 20, pages: 0 });
  renderReferenceData();
}

function renderSession() {
  const isAuthenticated = Boolean(state.accessToken && state.user);
  elements.authView.classList.toggle("hidden", isAuthenticated);
  elements.appView.classList.toggle("hidden", !isAuthenticated);

  if (!isAuthenticated) {
    elements.sessionEmail.textContent = "-";
    elements.sessionRoles.textContent = "-";
    elements.sessionTeams.textContent = "-";
    elements.connectionBadge.textContent = "Ожидает авторизации";
    elements.openManageViewButton.classList.add("hidden");
    return;
  }

  elements.sessionEmail.textContent = state.user.email;
  elements.sessionRoles.textContent = (state.user.roles || []).join(", ") || "-";
  elements.sessionTeams.textContent = formatTeamList(state.user.team_ids) || "-";
  elements.connectionBadge.textContent = `Сессия активна · ${(state.user.roles || []).join(", ")}`;
  elements.openManageViewButton.classList.toggle("hidden", !isAdmin());

  if (!isAdmin() && state.activeScreen === "manage") {
    setScreen("tasks");
  }
}

function renderTasks(result) {
  const items = toItems(result);
  elements.tasksMeta.textContent = `Всего: ${result.total ?? items.length} | Страница: ${result.page ?? 1}/${result.pages ?? 1}`;

  if (!items.length) {
    elements.tasksList.className = "tasks-list empty-state";
    elements.tasksList.innerHTML = "<p>По текущим фильтрам задач нет.</p>";
    return;
  }

  elements.tasksList.className = "tasks-list";
  elements.tasksList.innerHTML = "";

  for (const task of items) {
    const fragment = elements.taskCardTemplate.content.cloneNode(true);
    fillField(fragment, "title", task.title);
    fillField(fragment, "description", task.description || "Без описания");
    fillField(fragment, "id", task.id);
    fillField(fragment, "owner_display", formatUserLabel(task.owner_id));
    fillField(fragment, "owner_id", formatRawId(task.owner_id));
    fillField(fragment, "assignee_display", formatUserLabel(task.assignee_id));
    fillField(fragment, "assignee_id", formatRawId(task.assignee_id));
    fillField(fragment, "team_display", formatTeamLabel(task.team_id));
    fillField(fragment, "team_id", formatRawId(task.team_id));
    fillField(fragment, "priority", task.priority);
    fillField(fragment, "deadline", task.deadline ? formatDate(task.deadline) : "-");
    fillField(fragment, "status", task.status);

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

    elements.tasksList.appendChild(fragment);
  }
}

function renderReferenceData() {
  const users = toItems(state.users);
  const teams = toItems(state.teams);

  updateReferenceBadge(elements.usersAccessBadge, state.userDirectoryState, "Пользователи");
  updateReferenceBadge(elements.teamsAccessBadge, state.teamDirectoryState, "Команды");
  renderReferenceList(
    elements.usersReference,
    users,
    mapUserReferenceItem,
    state.userDirectoryState,
    "Пользователи недоступны или ещё не загружены."
  );
  renderReferenceList(
    elements.teamsReference,
    teams,
    mapTeamReferenceItem,
    state.teamDirectoryState,
    "Команды недоступны или ещё не загружены."
  );
  renderSuggestions(elements.userIdSuggestions, users, (user) => `${user.email} — ${user.id}`);
  renderSuggestions(elements.teamIdSuggestions, teams, (team) => `${team.name} — ${team.id}`);
}

function updateReferenceBadge(node, stateValue, label) {
  node.className = "mini-badge";
  switch (stateValue) {
    case "ready":
      node.textContent = `${label} доступны`;
      break;
    case "loading":
      node.classList.add("neutral");
      node.textContent = "Загрузка...";
      break;
    case "denied":
      node.classList.add("denied");
      node.textContent = "Нет прав";
      break;
    case "unavailable":
      node.classList.add("neutral");
      node.textContent = "Временно недоступно";
      break;
    default:
      node.classList.add("neutral");
      node.textContent = "Не запрашивалось";
      break;
  }
}

function renderReferenceList(container, items, mapper, stateValue, emptyText) {
  if (stateValue === "loading") {
    container.className = "reference-list empty-state";
    container.innerHTML = "<p>Загрузка справочника...</p>";
    return;
  }

  if (!items.length) {
    container.className = "reference-list empty-state";
    if (stateValue === "denied") {
      container.innerHTML = "<p>У текущего пользователя нет прав на этот справочник.</p>";
      return;
    }
    container.innerHTML = `<p>${emptyText}</p>`;
    return;
  }

  container.className = "reference-list";
  container.innerHTML = "";
  for (const item of items) {
    const fragment = elements.referenceItemTemplate.content.cloneNode(true);
    const mapped = mapper(item);
    fillField(fragment, "title", mapped.title);
    fillField(fragment, "subtitle", mapped.subtitle);
    fillField(fragment, "id", mapped.id);
    fillField(fragment, "extra", mapped.extra);
    fillField(fragment, "badge", mapped.badge);
    container.appendChild(fragment);
  }
}

function renderSuggestions(container, items, formatter) {
  container.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.id;
    option.label = formatter(item);
    container.appendChild(option);
  }
}

function mapUserReferenceItem(user) {
  return {
    title: user.email,
    subtitle: (user.roles || []).join(", ") || "Без ролей",
    id: user.id,
    extra: formatTeamList(user.team_ids) || "Без команд",
    badge: user.is_active === false ? "inactive" : "active",
  };
}

function mapTeamReferenceItem(team) {
  return {
    title: team.name,
    subtitle: `${team.member_count} участников`,
    id: team.id,
    extra: team.created_at ? `Создана ${formatDate(team.created_at)}` : "-",
    badge: "team",
  };
}

function parseCsvList(rawValue) {
  return String(rawValue || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeLookupValue(value) {
  return String(value || "").trim().toLowerCase();
}

function resolveUserReference(value) {
  const normalized = normalizeLookupValue(value);
  if (!normalized) {
    return "";
  }
  const user = state.users.find((item) => item.id === value || normalizeLookupValue(item.email) === normalized);
  return user ? user.id : value;
}

function resolveTeamReference(value) {
  const normalized = normalizeLookupValue(value);
  if (!normalized) {
    return "";
  }
  const team = state.teams.find((item) => item.id === value || normalizeLookupValue(item.name) === normalized);
  return team ? team.id : value;
}

function isAdmin() {
  return Boolean(state.user?.roles?.includes("admin"));
}

function findUserById(userId) {
  if (!userId) {
    return null;
  }
  if (state.user?.id === userId) {
    return state.user;
  }
  return state.users.find((user) => user.id === userId) || null;
}

function findTeamById(teamId) {
  if (!teamId) {
    return null;
  }
  return state.teams.find((team) => team.id === teamId) || null;
}

function formatUserLabel(userId) {
  const user = findUserById(userId);
  if (!user) {
    return userId || "-";
  }
  return user.id === state.user?.id ? `${user.email} (ты)` : user.email;
}

function formatTeamLabel(teamId) {
  const team = findTeamById(teamId);
  return team ? team.name : teamId || "-";
}

function formatTeamList(teamIds) {
  if (!Array.isArray(teamIds) || !teamIds.length) {
    return "";
  }
  return teamIds.map((teamId) => formatTeamLabel(teamId)).join(", ");
}

function formatRawId(value) {
  return value ? `ID: ${value}` : "";
}

function toItems(payload) {
  if (Array.isArray(payload)) {
    return payload;
  }
  if (Array.isArray(payload?.items)) {
    return payload.items;
  }
  return [];
}

function fillField(root, field, value) {
  const node = root.querySelector(`[data-field="${field}"]`);
  if (node) {
    node.textContent = value ?? "-";
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
  showBanner(`${code ? `[${code}] ` : ""}${message}${detailText}`, "error");
  console.error(error);
}

function showBanner(message, type = "error") {
  if (state.bannerTimer) {
    clearTimeout(state.bannerTimer);
    state.bannerTimer = null;
  }

  elements.errorBanner.textContent = type === "success" ? `Успешно: ${message}` : message;
  elements.errorBanner.classList.remove("hidden", "success");
  if (type === "success") {
    elements.errorBanner.classList.add("success");
  }

  state.bannerTimer = window.setTimeout(() => {
    clearBanner();
  }, type === "success" ? 4200 : 7000);
}

function clearBanner() {
  if (state.bannerTimer) {
    clearTimeout(state.bannerTimer);
    state.bannerTimer = null;
  }
  elements.errorBanner.textContent = "";
  elements.errorBanner.classList.add("hidden");
  elements.errorBanner.classList.remove("success");
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

