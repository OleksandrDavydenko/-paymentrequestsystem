# Система заявок на оплату

Flask-додаток з входом через Microsoft Entra ID (лише користувачі нашої організації).

## Локальний запуск
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # заповнити значення
.venv\Scripts\python app.py
```
Відкрити http://localhost:5000

## Налаштування Azure
**App registration** (Entra ID → App registrations → Payment Request System):
- Supported account types: *My organization only* (single tenant)
- Authentication → Web → Redirect URIs:
  - `http://localhost:5000/getAToken`
  - `https://paymentrequestsystem-ece9b4b4g0dbe7fu.polandcentral-01.azurewebsites.net/getAToken`

**App Service** → Environment variables:
| Name | Value |
|---|---|
| CLIENT_ID | Application (client) ID |
| TENANT_ID | Directory (tenant) ID |
| CLIENT_SECRET | client secret |
| FLASK_SECRET_KEY | випадковий рядок |
| REDIRECT_URI | `https://paymentrequestsystem-ece9b4b4g0dbe7fu.polandcentral-01.azurewebsites.net/getAToken` |
| SCM_DO_BUILD_DURING_DEPLOYMENT | `true` |

Startup command: `gunicorn --bind=0.0.0.0 --timeout 600 app:app`

## Деплой
Кожен push у `main` деплоїться через GitHub Actions (`.github/workflows/deploy.yml`).
Потрібен секрет репозиторію `AZURE_WEBAPP_PUBLISH_PROFILE` — вміст publish profile з App Service
(App Service → Configuration → SCM Basic Auth Publishing = On → Overview → Download publish profile).
