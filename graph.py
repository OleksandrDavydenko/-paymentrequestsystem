"""Мінімальний клієнт Microsoft Graph для керування членством у групах ролей.

Усі виклики йдуть із delegated-токеном користувача, що увійшов (адміністратора),
тож права обмежені тим, що дозволено йому в Entra ID.
"""
import requests

BASE = "https://graph.microsoft.com/v1.0"
TIMEOUT = 20

# Delegated-дозволи, потрібні адмінці (мають бути додані в API permissions + admin consent)
ADMIN_SCOPES = [
    "https://graph.microsoft.com/User.ReadBasic.All",
    "https://graph.microsoft.com/GroupMember.ReadWrite.All",
]


class GraphError(Exception):
    pass


def _raise_for(resp):
    if resp.ok:
        return
    try:
        err = resp.json().get("error", {})
        code, message = err.get("code", ""), err.get("message", "")
    except ValueError:
        code, message = "", resp.text[:200]
    if resp.status_code in (401, 403) or code == "Authorization_RequestDenied":
        raise GraphError(
            "Немає прав на цю дію в Azure. Перевірте, що для додатку надано admin consent на "
            "GroupMember.ReadWrite.All і що ви адміністратор тенанту або власник групи. "
            f"({code}: {message})"
        )
    if resp.status_code == 404:
        raise GraphError(f"Об'єкт не знайдено в Azure — перевірте ID групи. ({message})")
    raise GraphError(f"Помилка Microsoft Graph {resp.status_code}: {message}")


def _get(token, url, params=None, headers=None):
    resp = requests.get(url if url.startswith("http") else BASE + url,
                        headers={"Authorization": f"Bearer {token}", **(headers or {})},
                        params=params, timeout=TIMEOUT)
    _raise_for(resp)
    return resp.json()


def _user(o):
    return {
        "id": o["id"],
        "name": o.get("displayName") or o.get("userPrincipalName", ""),
        "email": o.get("mail") or o.get("userPrincipalName", ""),
    }


def group_members(token, group_id):
    """Користувачі — прямі члени групи."""
    users = []
    url = f"/groups/{group_id}/members/microsoft.graph.user"
    params = {"$select": "id,displayName,mail,userPrincipalName", "$top": "999"}
    while url:
        data = _get(token, url, params)
        users += [_user(o) for o in data.get("value", [])]
        url, params = data.get("@odata.nextLink"), None
    return users


def search_users(token, query):
    query = query.replace('"', "").strip()
    if len(query) < 2:
        return []
    data = _get(token, "/users", params={
        "$search": f'"displayName:{query}" OR "mail:{query}" OR "userPrincipalName:{query}"',
        "$select": "id,displayName,mail,userPrincipalName",
        "$top": "10",
        "$orderby": "displayName",
    }, headers={"ConsistencyLevel": "eventual"})
    return [_user(o) for o in data.get("value", [])]


def add_member(token, group_id, user_id):
    resp = requests.post(f"{BASE}/groups/{group_id}/members/$ref",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"@odata.id": f"{BASE}/directoryObjects/{user_id}"}, timeout=TIMEOUT)
    # 400 "already exist" — людина вже в групі, це не помилка
    if resp.status_code == 400 and "already exist" in resp.text:
        return
    _raise_for(resp)


def remove_member(token, group_id, user_id):
    resp = requests.delete(f"{BASE}/groups/{group_id}/members/{user_id}/$ref",
                           headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    if resp.status_code == 404:  # уже не член групи
        return
    _raise_for(resp)


def check_member_groups(token, group_ids):
    """Для overage: які з group_ids містять поточного користувача (транзитивно)."""
    resp = requests.post(f"{BASE}/me/checkMemberGroups",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"groupIds": list(group_ids)}, timeout=TIMEOUT)
    _raise_for(resp)
    return set(resp.json().get("value", []))
