import sys; sys.path.insert(0, ".")
import notifications, mailer
def boom(role): raise RuntimeError("403 Authorization_RequestDenied")
notifications.role_members = boom
assert notifications.cached_role_members("cfo") == []
assert mailer.LOG[0]["ok"] is False and "GroupMember.Read.All" in mailer.LOG[0]["error"], mailer.LOG[0]
n = len(mailer.LOG); notifications.cached_role_members("cfo"); assert len(mailer.LOG) == n  # кеш помилки
notifications.role_members = lambda r: [{"email": "sa@x", "name": "S"}]
notifications._members_cache["cfo"] = (0, [], False)
assert notifications.cached_role_members("cfo")[0]["email"] == "sa@x"
print("members ok")
