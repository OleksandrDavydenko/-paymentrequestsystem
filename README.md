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

## Ролі та групи
Ролі = членство в групах безпеки Microsoft Entra ID. Адмінка (`/admin/users`) змінює членство через Microsoft Graph
від імені адміністратора, що увійшов.

| Роль | Група | Змінна App Service |
|---|---|---|
| Адміністратор | PRS Адміністратори | `GROUP_ADMIN` |
| Ініціатор | PRS Ініціатори | `GROUP_INITIATOR` |
| Бухгалтер (готівка) | PRS Бухгалтери — готівка | `GROUP_ACC_CASH` |
| Бухгалтер (безготівка, резидент) | PRS Бухгалтери — резиденти | `GROUP_ACC_RESIDENT` (стара назва `GROUP_ACCOUNTANT` теж працює) |
| Бухгалтер (безготівка, нерезидент) | PRS Бухгалтери — нерезиденти | `GROUP_ACC_NONRESIDENT` |
| Фіндиректор | PRS Фіндиректори | `GROUP_CFO` |

`ADMIN_EMAILS` (за замовчуванням `od@ftpua.com`) — завжди адміністратори, навіть без групи.

Налаштування в Entra ID:
- App registration → Token configuration → Add groups claim → Security groups (ID token: Group ID)
- API permissions → Microsoft Graph (Delegated): `User.ReadBasic.All`, `GroupMember.ReadWrite.All`,
  `GroupMember.Read.All` → Grant admin consent

Маршрут погодження: Ініціатор → Бухгалтер → Фіндиректор → Оплата.
Бухгалтер (і перевірка, і оплата) визначається формою оплати та організацією заявки:
- Готівка → бухгалтер готівки (для будь-якої організації);
- Безготівка + організація-резидент (напр. ФТП) → бухгалтер резидентів;
- Безготівка + організація-нерезидент (напр. Litbia) → бухгалтер нерезидентів.

Список організацій-нерезидентів — `NONRESIDENT_ORGANIZATIONS` у `mock_data.py` (пізніше — з 1С).
